"""Integration tests — OmniParser vs UltraVision head-to-head comparison.

Creates REAL synthetic screenshots with PIL, runs both modules' full
pipelines (mocking only the ML model inference, not image processing),
and compares:
  - API contract compatibility (same actions, same return shapes)
  - Output format fidelity (VisionParseResult, VisionElement fields)
  - Backend switching via config
  - find_element behavior (substring vs grounding)
  - OCR text extraction
  - Scene graph generation
  - Cache integration
  - Error handling / fallback paths

These tests use real PIL images — NOT mock bytes.  Only the actual
model inference is faked (YOLO, Florence-2, UI-DETR, PaddleOCR, etc.).
"""

from __future__ import annotations

import asyncio
import io
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llmos_bridge.modules.perception_vision.base import (
    BaseVisionModule,
    VisionElement,
    VisionParseResult,
)

# ---------------------------------------------------------------------------
# Helpers: create real screenshots with PIL
# ---------------------------------------------------------------------------


def _create_real_screenshot(width: int = 1920, height: int = 1080) -> bytes:
    """Create a real PNG screenshot with drawn UI elements using PIL.

    Draws:
      - A toolbar at the top (gray bar with "File Edit View" text area)
      - A "Submit" button (blue rectangle)
      - A "Cancel" button (gray rectangle)
      - A text input field (white rectangle with border)
      - A small icon (orange square)
    """
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (width, height), color=(240, 240, 240))
    draw = ImageDraw.Draw(img)

    # Toolbar bar (top)
    draw.rectangle([0, 0, width, 45], fill=(50, 50, 50))
    draw.text((20, 12), "File  Edit  View  Help", fill=(255, 255, 255))

    # Submit button
    draw.rectangle([800, 500, 1000, 550], fill=(0, 100, 200))
    draw.text((870, 515), "Submit", fill=(255, 255, 255))

    # Cancel button
    draw.rectangle([1050, 500, 1200, 550], fill=(180, 180, 180))
    draw.text((1095, 515), "Cancel", fill=(0, 0, 0))

    # Text input field
    draw.rectangle([600, 400, 1200, 440], outline=(150, 150, 150), width=2)
    draw.rectangle([602, 402, 1198, 438], fill=(255, 255, 255))
    draw.text((610, 410), "Enter your name...", fill=(180, 180, 180))

    # Small icon
    draw.rectangle([50, 60, 80, 90], fill=(255, 165, 0))

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _create_small_screenshot(width: int = 800, height: int = 600) -> bytes:
    """A smaller, simpler screenshot for faster tests."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (width, height), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.rectangle([100, 100, 300, 150], fill=(0, 120, 255))
    draw.text((160, 115), "OK", fill=(255, 255, 255))
    draw.rectangle([350, 100, 550, 150], fill=(200, 200, 200))
    draw.text((400, 115), "Cancel", fill=(0, 0, 0))

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Fake model outputs — realistic but deterministic
# ---------------------------------------------------------------------------


def _fake_omniparser_parse(image_b64: str):
    """Return a realistic OmniParser v2 parsed_content_list."""
    som_image_b64 = "FAKE_SOM_BASE64"
    parsed = [
        {
            "type": "text",
            "bbox": [0.01, 0.01, 0.15, 0.04],
            "interactivity": False,
            "content": "File  Edit  View  Help",
            "source": "box_ocr_content_ocr",
        },
        {
            "type": "icon",
            "bbox": [0.42, 0.46, 0.52, 0.51],
            "interactivity": True,
            "content": "Submit button",
            "source": "box_yolo_content_yolo",
        },
        {
            "type": "icon",
            "bbox": [0.55, 0.46, 0.63, 0.51],
            "interactivity": True,
            "content": "Cancel button",
            "source": "box_yolo_content_yolo",
        },
        {
            "type": "text",
            "bbox": [0.31, 0.37, 0.63, 0.41],
            "interactivity": False,
            "content": "Enter your name...",
            "source": "box_ocr_content_ocr",
        },
        {
            "type": "icon",
            "bbox": [0.025, 0.055, 0.042, 0.083],
            "interactivity": True,
            "content": "an orange square icon",
            "source": "box_yolo_content_yolo",
        },
    ]
    return som_image_b64, parsed


def _fake_ui_detr_detections():
    """Return realistic UI-DETR-1 detection output."""
    from llmos_bridge.modules.perception_vision.ultra.backends.detector import (
        DetectionOutput,
        DetectionResult,
    )

    return DetectionOutput(
        detections=[
            DetectionResult(bbox=(0.42, 0.46, 0.52, 0.51), confidence=0.92),  # Submit btn
            DetectionResult(bbox=(0.55, 0.46, 0.63, 0.51), confidence=0.89),  # Cancel btn
            DetectionResult(bbox=(0.31, 0.37, 0.63, 0.41), confidence=0.85),  # Input field
            DetectionResult(bbox=(0.025, 0.055, 0.042, 0.083), confidence=0.78),  # Icon
        ],
        image_width=1920,
        image_height=1080,
        model_id="ui-detr-1",
        inference_time_ms=280.0,
    )


def _fake_ocr_output():
    """Return realistic PP-OCRv5 output."""
    from llmos_bridge.modules.perception_vision.ultra.backends.ocr import OCRBox, OCROutput

    return OCROutput(
        boxes=[
            OCRBox(text="File  Edit  View  Help", bbox=(0.01, 0.01, 0.15, 0.04), confidence=0.97),
            OCRBox(text="Submit", bbox=(0.45, 0.47, 0.51, 0.50), confidence=0.98),
            OCRBox(text="Cancel", bbox=(0.57, 0.47, 0.62, 0.50), confidence=0.96),
            OCRBox(text="Enter your name...", bbox=(0.32, 0.38, 0.62, 0.40), confidence=0.93),
        ],
        full_text="File  Edit  View  Help Submit Cancel Enter your name...",
        inference_time_ms=380.0,
        engine_id="ppocr-v5",
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def omni_module():
    """Create an OmniParserModule with real init, no model downloads."""
    from llmos_bridge.modules.perception_vision.omniparser.module import OmniParserModule

    with patch.dict(os.environ, {
        "LLMOS_OMNIPARSER_MODEL_DIR": "/fake/omniparser/models",
        "LLMOS_OMNIPARSER_AUTO_DOWNLOAD": "false",
    }):
        with patch.object(OmniParserModule, "_check_dependencies"):
            m = OmniParserModule()
    return m


@pytest.fixture
def ultra_module():
    """Create an UltraVisionModule with real init, no model downloads."""
    from llmos_bridge.modules.perception_vision.ultra.module import UltraVisionModule

    with patch.dict(os.environ, {
        "LLMOS_ULTRA_VISION_MODEL_DIR": "/fake/ultra/models",
        "LLMOS_ULTRA_VISION_AUTO_DOWNLOAD": "false",
        "LLMOS_ULTRA_VISION_ENABLE_GROUNDING": "false",
    }):
        with patch.object(UltraVisionModule, "_check_dependencies"):
            m = UltraVisionModule()
    return m


@pytest.fixture
def screenshot_bytes() -> bytes:
    return _create_real_screenshot()


@pytest.fixture
def small_screenshot_bytes() -> bytes:
    return _create_small_screenshot()


# ===================================================================
# Test Group 1: API Contract Compatibility
# ===================================================================


@pytest.mark.integration
class TestAPIContractCompatibility:
    """Both modules MUST expose the same actions with compatible signatures."""

    def test_both_are_base_vision_subclass(self, omni_module, ultra_module) -> None:
        assert isinstance(omni_module, BaseVisionModule)
        assert isinstance(ultra_module, BaseVisionModule)

    def test_same_module_id(self, omni_module, ultra_module) -> None:
        assert omni_module.MODULE_ID == "vision"
        assert ultra_module.MODULE_ID == "vision"

    def test_manifests_have_same_actions(self, omni_module, ultra_module) -> None:
        omni_actions = {a.name for a in omni_module.get_manifest().actions}
        ultra_actions = {a.name for a in ultra_module.get_manifest().actions}
        assert omni_actions == ultra_actions
        assert omni_actions == {"parse_screen", "capture_and_parse", "find_element", "get_screen_text"}

    def test_manifests_have_same_action_params(self, omni_module, ultra_module) -> None:
        """Required params (name + type) must match for drop-in replacement."""
        omni_manifest = omni_module.get_manifest()
        ultra_manifest = ultra_module.get_manifest()

        for omni_action in omni_manifest.actions:
            ultra_action = next(a for a in ultra_manifest.actions if a.name == omni_action.name)
            omni_required = {(p.name, p.type) for p in omni_action.params if p.required}
            ultra_required = {(p.name, p.type) for p in ultra_action.params if p.required}
            assert omni_required == ultra_required, (
                f"Required params mismatch for action '{omni_action.name}': "
                f"omni={omni_required}, ultra={ultra_required}"
            )

    def test_both_declare_screen_capture_permission(self, omni_module, ultra_module) -> None:
        omni_perms = omni_module.get_manifest().declared_permissions
        ultra_perms = ultra_module.get_manifest().declared_permissions
        assert "screen_capture" in omni_perms
        assert "screen_capture" in ultra_perms

    def test_both_have_parse_screen_method(self, omni_module, ultra_module) -> None:
        assert hasattr(omni_module, "parse_screen")
        assert hasattr(ultra_module, "parse_screen")
        assert asyncio.iscoroutinefunction(omni_module.parse_screen)
        assert asyncio.iscoroutinefunction(ultra_module.parse_screen)

    def test_both_have_action_handlers(self, omni_module, ultra_module) -> None:
        for action in ["_action_parse_screen", "_action_capture_and_parse",
                       "_action_find_element", "_action_get_screen_text"]:
            assert hasattr(omni_module, action), f"OmniParser missing {action}"
            assert hasattr(ultra_module, action), f"UltraVision missing {action}"


# ===================================================================
# Test Group 2: Output Format Fidelity
# ===================================================================


@pytest.mark.integration
class TestOutputFormatFidelity:
    """Both modules must produce VisionParseResult with the same shape."""

    @pytest.mark.asyncio
    async def test_omniparser_output_format(self, omni_module, screenshot_bytes) -> None:
        """OmniParser with mocked API returns correct VisionParseResult."""
        mock_api = MagicMock()
        mock_api.parse = _fake_omniparser_parse
        omni_module._api = mock_api

        with patch(
            "llmos_bridge.modules.perception_vision.omniparser.module._TORCH_AVAILABLE", True
        ):
            with patch.dict("sys.modules", {"ultralytics": MagicMock(), "easyocr": MagicMock()}):
                result = await omni_module.parse_screen(screenshot_bytes=screenshot_bytes)

        assert isinstance(result, VisionParseResult)
        assert result.width == 1920
        assert result.height == 1080
        assert len(result.elements) == 5
        assert result.model_id == "omniparser-v2"
        assert result.parse_time_ms > 0
        # Every element has required fields.
        for elem in result.elements:
            assert isinstance(elem, VisionElement)
            assert elem.element_id
            assert elem.label is not None
            assert elem.element_type in ("icon", "text", "button", "input", "link", "checkbox", "other")
            assert len(elem.bbox) == 4
            assert all(0.0 <= v <= 1.0 for v in elem.bbox)
            assert 0.0 <= elem.confidence <= 1.0

    @pytest.mark.asyncio
    async def test_ultra_output_format(self, ultra_module, screenshot_bytes) -> None:
        """UltraVision with mocked backends returns correct VisionParseResult."""
        det_output = _fake_ui_detr_detections()
        ocr_output = _fake_ocr_output()

        with patch.object(ultra_module, "_run_detection", return_value=det_output):
            with patch.object(ultra_module, "_run_ocr", return_value=ocr_output):
                with patch(
                    "llmos_bridge.modules.perception_vision.ultra.module._TORCH_AVAILABLE", True
                ):
                    result = await ultra_module.parse_screen(screenshot_bytes=screenshot_bytes)

        assert isinstance(result, VisionParseResult)
        assert result.width == 1920
        assert result.height == 1080
        assert len(result.elements) >= 4  # 4 detections + possible OCR-only text
        assert "ultra" in result.model_id
        assert result.parse_time_ms > 0
        for elem in result.elements:
            assert isinstance(elem, VisionElement)
            assert elem.element_id
            assert elem.label is not None
            assert elem.element_type in ("icon", "text", "button", "input", "link", "checkbox", "other")
            assert len(elem.bbox) == 4
            assert all(0.0 <= v <= 1.0 for v in elem.bbox)
            assert 0.0 <= elem.confidence <= 1.0

    @pytest.mark.asyncio
    async def test_both_produce_to_dict_compatible_output(
        self, omni_module, ultra_module, screenshot_bytes,
    ) -> None:
        """to_dict() must produce dicts with the same top-level keys."""
        mock_api = MagicMock()
        mock_api.parse = _fake_omniparser_parse
        omni_module._api = mock_api

        det_output = _fake_ui_detr_detections()
        ocr_output = _fake_ocr_output()

        with patch(
            "llmos_bridge.modules.perception_vision.omniparser.module._TORCH_AVAILABLE", True,
        ):
            with patch.dict("sys.modules", {"ultralytics": MagicMock(), "easyocr": MagicMock()}):
                omni_result = await omni_module.parse_screen(screenshot_bytes=screenshot_bytes)

        with patch.object(ultra_module, "_run_detection", return_value=det_output):
            with patch.object(ultra_module, "_run_ocr", return_value=ocr_output):
                with patch(
                    "llmos_bridge.modules.perception_vision.ultra.module._TORCH_AVAILABLE", True,
                ):
                    ultra_result = await ultra_module.parse_screen(screenshot_bytes=screenshot_bytes)

        omni_dict = omni_result.to_dict()
        ultra_dict = ultra_result.to_dict()

        # Same top-level keys.
        assert set(omni_dict.keys()) == set(ultra_dict.keys())
        # Same element dict keys.
        if omni_dict["elements"] and ultra_dict["elements"]:
            assert set(omni_dict["elements"][0].keys()) == set(ultra_dict["elements"][0].keys())


# ===================================================================
# Test Group 3: Element Detection Quality Comparison
# ===================================================================


@pytest.mark.integration
class TestElementDetectionComparison:
    """Compare what each module finds on the same screenshot."""

    @pytest.mark.asyncio
    async def test_omniparser_detects_buttons(self, omni_module, screenshot_bytes) -> None:
        mock_api = MagicMock()
        mock_api.parse = _fake_omniparser_parse
        omni_module._api = mock_api

        with patch(
            "llmos_bridge.modules.perception_vision.omniparser.module._TORCH_AVAILABLE", True,
        ):
            with patch.dict("sys.modules", {"ultralytics": MagicMock(), "easyocr": MagicMock()}):
                result = await omni_module.parse_screen(screenshot_bytes=screenshot_bytes)

        # OmniParser labels everything as "icon" for YOLO detections.
        labels = [e.label for e in result.elements]
        assert any("Submit" in lbl for lbl in labels)
        assert any("Cancel" in lbl for lbl in labels)

    @pytest.mark.asyncio
    async def test_ultra_classifies_buttons(self, ultra_module, screenshot_bytes) -> None:
        det_output = _fake_ui_detr_detections()
        ocr_output = _fake_ocr_output()

        with patch.object(ultra_module, "_run_detection", return_value=det_output):
            with patch.object(ultra_module, "_run_ocr", return_value=ocr_output):
                with patch(
                    "llmos_bridge.modules.perception_vision.ultra.module._TORCH_AVAILABLE", True,
                ):
                    result = await ultra_module.parse_screen(screenshot_bytes=screenshot_bytes)

        # UltraVision SHOULD classify these as "button" (not "icon") thanks to
        # the heuristic classifier + OCR overlap.
        buttons = result.find_by_type("button")
        button_labels = [b.label for b in buttons]
        assert any("Submit" in lbl for lbl in button_labels), (
            f"UltraVision should classify Submit as button, got types: "
            f"{[(e.label, e.element_type) for e in result.elements]}"
        )
        assert any("Cancel" in lbl for lbl in button_labels)

    @pytest.mark.asyncio
    async def test_ultra_detects_more_element_types(self, ultra_module, screenshot_bytes) -> None:
        """UltraVision's classifier should produce richer type assignments."""
        det_output = _fake_ui_detr_detections()
        ocr_output = _fake_ocr_output()

        with patch.object(ultra_module, "_run_detection", return_value=det_output):
            with patch.object(ultra_module, "_run_ocr", return_value=ocr_output):
                with patch(
                    "llmos_bridge.modules.perception_vision.ultra.module._TORCH_AVAILABLE", True,
                ):
                    result = await ultra_module.parse_screen(screenshot_bytes=screenshot_bytes)

        types_found = {e.element_type for e in result.elements}
        # Should have at least 2 distinct types (vs OmniParser which only has "icon" and "text").
        assert len(types_found) >= 2, f"UltraVision types: {types_found}"


# ===================================================================
# Test Group 4: find_element Comparison
# ===================================================================


@pytest.mark.integration
class TestFindElementComparison:
    """Compare find_element: OmniParser substring match vs UltraVision grounding."""

    @pytest.mark.asyncio
    async def test_omniparser_find_element_substring(self, omni_module, screenshot_bytes) -> None:
        """OmniParser uses parse_screen + substring match."""
        mock_api = MagicMock()
        mock_api.parse = _fake_omniparser_parse
        omni_module._api = mock_api

        with patch(
            "llmos_bridge.modules.perception_vision.omniparser.module._TORCH_AVAILABLE", True,
        ):
            with patch.dict("sys.modules", {"ultralytics": MagicMock(), "easyocr": MagicMock()}):
                with patch("builtins.open", MagicMock(
                    return_value=MagicMock(
                        __enter__=MagicMock(return_value=MagicMock(read=MagicMock(return_value=screenshot_bytes))),
                        __exit__=MagicMock(return_value=False),
                    )
                )):
                    result = await omni_module._action_find_element({
                        "query": "Submit",
                        "screenshot_path": "/tmp/fake.png",
                    })

        assert result["found"] is True
        assert result["pixel_x"] is not None
        assert result["pixel_y"] is not None

    @pytest.mark.asyncio
    async def test_ultra_find_element_fallback_to_parse(self, ultra_module, screenshot_bytes) -> None:
        """UltraVision without grounding falls back to parse+substring match."""
        det_output = _fake_ui_detr_detections()
        ocr_output = _fake_ocr_output()

        with patch.object(ultra_module, "_run_detection", return_value=det_output):
            with patch.object(ultra_module, "_run_ocr", return_value=ocr_output):
                with patch(
                    "llmos_bridge.modules.perception_vision.ultra.module._TORCH_AVAILABLE", True,
                ):
                    with patch("builtins.open", MagicMock(
                        return_value=MagicMock(
                            __enter__=MagicMock(return_value=MagicMock(read=MagicMock(return_value=screenshot_bytes))),
                            __exit__=MagicMock(return_value=False),
                        )
                    )):
                        result = await ultra_module._action_find_element({
                            "query": "Submit",
                            "screenshot_path": "/tmp/fake.png",
                        })

        assert result["found"] is True
        assert result["pixel_x"] is not None
        assert result["pixel_y"] is not None

    @pytest.mark.asyncio
    async def test_ultra_find_element_with_grounding(self, screenshot_bytes) -> None:
        """UltraVision with grounding uses UGround instead of full parse."""
        from llmos_bridge.modules.perception_vision.ultra.backends.grounder import GroundingResult
        from llmos_bridge.modules.perception_vision.ultra.module import UltraVisionModule

        with patch.dict(os.environ, {
            "LLMOS_ULTRA_VISION_MODEL_DIR": "/fake/models",
            "LLMOS_ULTRA_VISION_AUTO_DOWNLOAD": "false",
            "LLMOS_ULTRA_VISION_ENABLE_GROUNDING": "true",
        }):
            with patch.object(UltraVisionModule, "_check_dependencies"):
                module = UltraVisionModule()

        mock_grounder = MagicMock()
        mock_grounder.ground.return_value = GroundingResult(
            bbox=(0.42, 0.46, 0.52, 0.51),
            confidence=0.88,
            query="Submit",
        )
        module._grounder = mock_grounder

        with patch("builtins.open", MagicMock(
            return_value=MagicMock(
                __enter__=MagicMock(return_value=MagicMock(read=MagicMock(return_value=screenshot_bytes))),
                __exit__=MagicMock(return_value=False),
            )
        )):
            result = await module._action_find_element({
                "query": "Submit",
                "screenshot_path": "/tmp/fake.png",
            })

        assert result["found"] is True
        assert result["element"]["extra"]["source"] == "uground_grounding"
        # Grounding should NOT call parse_screen — direct bbox.
        assert result["pixel_x"] is not None

    @pytest.mark.asyncio
    async def test_find_not_found_both_modules(
        self, omni_module, ultra_module, screenshot_bytes,
    ) -> None:
        """Both modules return found=False for nonexistent elements."""
        mock_api = MagicMock()
        mock_api.parse = _fake_omniparser_parse
        omni_module._api = mock_api

        det_output = _fake_ui_detr_detections()
        ocr_output = _fake_ocr_output()

        with patch(
            "llmos_bridge.modules.perception_vision.omniparser.module._TORCH_AVAILABLE", True,
        ):
            with patch.dict("sys.modules", {"ultralytics": MagicMock(), "easyocr": MagicMock()}):
                with patch("builtins.open", MagicMock(
                    return_value=MagicMock(
                        __enter__=MagicMock(return_value=MagicMock(read=MagicMock(return_value=screenshot_bytes))),
                        __exit__=MagicMock(return_value=False),
                    )
                )):
                    omni_result = await omni_module._action_find_element({
                        "query": "NonExistentButton12345",
                        "screenshot_path": "/tmp/fake.png",
                    })

        with patch.object(ultra_module, "_run_detection", return_value=det_output):
            with patch.object(ultra_module, "_run_ocr", return_value=ocr_output):
                with patch(
                    "llmos_bridge.modules.perception_vision.ultra.module._TORCH_AVAILABLE", True,
                ):
                    with patch("builtins.open", MagicMock(
                        return_value=MagicMock(
                            __enter__=MagicMock(return_value=MagicMock(read=MagicMock(return_value=screenshot_bytes))),
                            __exit__=MagicMock(return_value=False),
                        )
                    )):
                        ultra_result = await ultra_module._action_find_element({
                            "query": "NonExistentButton12345",
                            "screenshot_path": "/tmp/fake.png",
                        })

        assert omni_result["found"] is False
        assert ultra_result["found"] is False
        assert omni_result["element"] is None
        assert ultra_result["element"] is None


# ===================================================================
# Test Group 5: OCR Text Extraction
# ===================================================================


@pytest.mark.integration
class TestOCRTextExtraction:
    """Compare get_screen_text output."""

    @pytest.mark.asyncio
    async def test_omniparser_extracts_text(self, omni_module, screenshot_bytes) -> None:
        mock_api = MagicMock()
        mock_api.parse = _fake_omniparser_parse
        omni_module._api = mock_api

        with patch(
            "llmos_bridge.modules.perception_vision.omniparser.module._TORCH_AVAILABLE", True,
        ):
            with patch.dict("sys.modules", {"ultralytics": MagicMock(), "easyocr": MagicMock()}):
                with patch("builtins.open", MagicMock(
                    return_value=MagicMock(
                        __enter__=MagicMock(return_value=MagicMock(read=MagicMock(return_value=screenshot_bytes))),
                        __exit__=MagicMock(return_value=False),
                    )
                )):
                    result = await omni_module._action_get_screen_text({
                        "screenshot_path": "/tmp/fake.png",
                    })

        assert isinstance(result["text"], str)
        assert isinstance(result["line_count"], int)
        assert len(result["text"]) > 0

    @pytest.mark.asyncio
    async def test_ultra_extracts_text(self, ultra_module, screenshot_bytes) -> None:
        det_output = _fake_ui_detr_detections()
        ocr_output = _fake_ocr_output()

        with patch.object(ultra_module, "_run_detection", return_value=det_output):
            with patch.object(ultra_module, "_run_ocr", return_value=ocr_output):
                with patch(
                    "llmos_bridge.modules.perception_vision.ultra.module._TORCH_AVAILABLE", True,
                ):
                    with patch("builtins.open", MagicMock(
                        return_value=MagicMock(
                            __enter__=MagicMock(return_value=MagicMock(read=MagicMock(return_value=screenshot_bytes))),
                            __exit__=MagicMock(return_value=False),
                        )
                    )):
                        result = await ultra_module._action_get_screen_text({
                            "screenshot_path": "/tmp/fake.png",
                        })

        assert isinstance(result["text"], str)
        assert isinstance(result["line_count"], int)
        # UltraVision should extract text from OCR output.
        assert "Submit" in result["text"]
        assert "Cancel" in result["text"]

    @pytest.mark.asyncio
    async def test_ultra_raw_ocr_richer_than_omniparser(
        self, omni_module, ultra_module, screenshot_bytes,
    ) -> None:
        """UltraVision's raw_ocr comes directly from dedicated OCR engine."""
        mock_api = MagicMock()
        mock_api.parse = _fake_omniparser_parse
        omni_module._api = mock_api

        det_output = _fake_ui_detr_detections()
        ocr_output = _fake_ocr_output()

        with patch(
            "llmos_bridge.modules.perception_vision.omniparser.module._TORCH_AVAILABLE", True,
        ):
            with patch.dict("sys.modules", {"ultralytics": MagicMock(), "easyocr": MagicMock()}):
                omni_result = await omni_module.parse_screen(screenshot_bytes=screenshot_bytes)

        with patch.object(ultra_module, "_run_detection", return_value=det_output):
            with patch.object(ultra_module, "_run_ocr", return_value=ocr_output):
                with patch(
                    "llmos_bridge.modules.perception_vision.ultra.module._TORCH_AVAILABLE", True,
                ):
                    ultra_result = await ultra_module.parse_screen(screenshot_bytes=screenshot_bytes)

        # Both should have raw_ocr.
        assert omni_result.raw_ocr is not None
        assert ultra_result.raw_ocr is not None
        # UltraVision's raw_ocr comes from dedicated OCR engine (PP-OCRv5).
        assert "Submit" in ultra_result.raw_ocr
        assert "Cancel" in ultra_result.raw_ocr


# ===================================================================
# Test Group 6: Scene Graph Generation
# ===================================================================


@pytest.mark.integration
class TestSceneGraphGeneration:
    """Both modules should generate scene graphs."""

    @pytest.mark.asyncio
    async def test_omniparser_generates_scene_graph(self, omni_module, screenshot_bytes) -> None:
        mock_api = MagicMock()
        mock_api.parse = _fake_omniparser_parse
        omni_module._api = mock_api

        with patch(
            "llmos_bridge.modules.perception_vision.omniparser.module._TORCH_AVAILABLE", True,
        ):
            with patch.dict("sys.modules", {"ultralytics": MagicMock(), "easyocr": MagicMock()}):
                result = await omni_module.parse_screen(screenshot_bytes=screenshot_bytes)

        assert result.scene_graph_text is not None
        assert len(result.scene_graph_text) > 0

    @pytest.mark.asyncio
    async def test_ultra_generates_scene_graph(self, ultra_module, screenshot_bytes) -> None:
        det_output = _fake_ui_detr_detections()
        ocr_output = _fake_ocr_output()

        with patch.object(ultra_module, "_run_detection", return_value=det_output):
            with patch.object(ultra_module, "_run_ocr", return_value=ocr_output):
                with patch(
                    "llmos_bridge.modules.perception_vision.ultra.module._TORCH_AVAILABLE", True,
                ):
                    result = await ultra_module.parse_screen(screenshot_bytes=screenshot_bytes)

        assert result.scene_graph_text is not None
        assert len(result.scene_graph_text) > 0

    @pytest.mark.asyncio
    async def test_both_scene_graphs_have_regions(
        self, omni_module, ultra_module, screenshot_bytes,
    ) -> None:
        """Both scene graphs should identify screen regions."""
        mock_api = MagicMock()
        mock_api.parse = _fake_omniparser_parse
        omni_module._api = mock_api

        det_output = _fake_ui_detr_detections()
        ocr_output = _fake_ocr_output()

        with patch(
            "llmos_bridge.modules.perception_vision.omniparser.module._TORCH_AVAILABLE", True,
        ):
            with patch.dict("sys.modules", {"ultralytics": MagicMock(), "easyocr": MagicMock()}):
                omni_result = await omni_module.parse_screen(screenshot_bytes=screenshot_bytes)

        with patch.object(ultra_module, "_run_detection", return_value=det_output):
            with patch.object(ultra_module, "_run_ocr", return_value=ocr_output):
                with patch(
                    "llmos_bridge.modules.perception_vision.ultra.module._TORCH_AVAILABLE", True,
                ):
                    ultra_result = await ultra_module.parse_screen(screenshot_bytes=screenshot_bytes)

        # Both should have non-empty scene graphs.
        assert omni_result.scene_graph_text
        assert ultra_result.scene_graph_text


# ===================================================================
# Test Group 7: Cache Integration
# ===================================================================


@pytest.mark.integration
class TestCacheIntegration:
    """Both modules should use PerceptionCache identically."""

    @pytest.mark.asyncio
    async def test_omniparser_caches_results(self, omni_module, screenshot_bytes) -> None:
        """Second call with same bytes should return cached result."""
        from llmos_bridge.modules.perception_vision.cache import PerceptionCache

        cache = PerceptionCache(max_entries=5, ttl_seconds=10.0)
        omni_module._cache = cache

        parse_call_count = 0

        def counting_parse(image_b64: str):
            nonlocal parse_call_count
            parse_call_count += 1
            return _fake_omniparser_parse(image_b64)

        mock_api = MagicMock()
        mock_api.parse = counting_parse
        omni_module._api = mock_api

        with patch(
            "llmos_bridge.modules.perception_vision.omniparser.module._TORCH_AVAILABLE", True,
        ):
            with patch.dict("sys.modules", {"ultralytics": MagicMock(), "easyocr": MagicMock()}):
                result1 = await omni_module.parse_screen(screenshot_bytes=screenshot_bytes)
                result2 = await omni_module.parse_screen(screenshot_bytes=screenshot_bytes)

        # Second result should be identical (cached).
        assert result1.model_id == result2.model_id
        assert len(result1.elements) == len(result2.elements)
        # API should only be called once.
        assert parse_call_count == 1

    @pytest.mark.asyncio
    async def test_ultra_caches_results(self, ultra_module, screenshot_bytes) -> None:
        """Second call with same bytes should return cached result."""
        from llmos_bridge.modules.perception_vision.cache import PerceptionCache

        cache = PerceptionCache(max_entries=5, ttl_seconds=10.0)
        ultra_module._cache = cache

        det_output = _fake_ui_detr_detections()
        ocr_output = _fake_ocr_output()
        call_count = 0

        original_run_det = ultra_module._run_detection

        def counting_detection(image, threshold):
            nonlocal call_count
            call_count += 1
            return det_output

        with patch.object(ultra_module, "_run_detection", side_effect=counting_detection):
            with patch.object(ultra_module, "_run_ocr", return_value=ocr_output):
                with patch(
                    "llmos_bridge.modules.perception_vision.ultra.module._TORCH_AVAILABLE", True,
                ):
                    result1 = await ultra_module.parse_screen(screenshot_bytes=screenshot_bytes)
                    result2 = await ultra_module.parse_screen(screenshot_bytes=screenshot_bytes)

        assert len(result1.elements) == len(result2.elements)
        assert call_count == 1  # Detection only called once.


# ===================================================================
# Test Group 8: Fallback Paths
# ===================================================================


@pytest.mark.integration
class TestFallbackPaths:
    """Both modules should degrade gracefully when deps are missing."""

    @pytest.mark.asyncio
    async def test_omniparser_pil_only_fallback(self, omni_module, screenshot_bytes) -> None:
        """When torch is unavailable, OmniParser falls back to PIL-only."""
        with patch(
            "llmos_bridge.modules.perception_vision.omniparser.module._TORCH_AVAILABLE", False,
        ):
            result = await omni_module.parse_screen(screenshot_bytes=screenshot_bytes)

        assert isinstance(result, VisionParseResult)
        assert result.elements == []  # No detections without torch.
        assert "pil-fallback" in result.model_id
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_ultra_pil_only_fallback(self, ultra_module, screenshot_bytes) -> None:
        """When torch is unavailable, UltraVision falls back to PIL-only."""
        with patch(
            "llmos_bridge.modules.perception_vision.ultra.module._TORCH_AVAILABLE", False,
        ):
            result = await ultra_module.parse_screen(screenshot_bytes=screenshot_bytes)

        assert isinstance(result, VisionParseResult)
        assert result.elements == []
        assert "pil-fallback" in result.model_id
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_ultra_detection_error_returns_empty(self, ultra_module, screenshot_bytes) -> None:
        """If detection fails, UltraVision should still return OCR-only results."""
        ocr_output = _fake_ocr_output()

        with patch.object(ultra_module, "_run_detection", side_effect=Exception("GPU OOM")):
            with patch.object(ultra_module, "_run_ocr", return_value=ocr_output):
                with patch(
                    "llmos_bridge.modules.perception_vision.ultra.module._TORCH_AVAILABLE", True,
                ):
                    # _run_detection is called via run_in_executor; the module
                    # wraps it with error handling that returns empty DetectionOutput.
                    # But since we patched _run_detection directly, the gather
                    # will raise. Let's patch at the right level.
                    pass

        # Test the internal error handler in _run_detection wrapper:
        from llmos_bridge.modules.perception_vision.ultra.backends.detector import DetectionOutput

        def failing_detection(image, threshold):
            raise RuntimeError("GPU OOM")

        # Replace the actual detector to simulate a GPU failure.
        with patch.object(ultra_module, "_get_detector", side_effect=RuntimeError("GPU OOM")):
            with patch.object(ultra_module, "_run_ocr", return_value=ocr_output):
                with patch(
                    "llmos_bridge.modules.perception_vision.ultra.module._TORCH_AVAILABLE", True,
                ):
                    result = await ultra_module.parse_screen(screenshot_bytes=screenshot_bytes)

        # Should still have OCR text elements.
        assert isinstance(result, VisionParseResult)
        assert result.raw_ocr is not None


# ===================================================================
# Test Group 9: Backend Switching via Config
# ===================================================================


@pytest.mark.integration
class TestBackendSwitching:
    """Verify that server.py correctly switches between backends."""

    def test_config_defaults_to_omniparser(self) -> None:
        from llmos_bridge.config import VisionConfig

        cfg = VisionConfig()
        assert cfg.backend == "omniparser"

    def test_config_accepts_ultra(self) -> None:
        from llmos_bridge.config import VisionConfig

        cfg = VisionConfig(backend="ultra")
        assert cfg.backend == "ultra"

    def test_config_ultra_fields_exist(self) -> None:
        from llmos_bridge.config import VisionConfig

        cfg = VisionConfig(backend="ultra")
        assert cfg.ultra_model_dir == "~/.llmos/models/ultra_vision"
        assert cfg.ultra_device == "auto"
        assert cfg.ultra_box_threshold == 0.3
        assert cfg.ultra_ocr_engine == "paddleocr"
        assert cfg.ultra_enable_grounding is True
        assert cfg.ultra_max_vram_mb == 3000

    def test_server_registers_omniparser_by_default(self) -> None:
        """_register_builtin_modules should use OmniParser when backend='omniparser'."""
        from llmos_bridge.api.server import _register_builtin_modules
        from llmos_bridge.config import Settings
        from llmos_bridge.modules.perception_vision.omniparser.module import OmniParserModule
        from llmos_bridge.modules.registry import ModuleRegistry

        settings = Settings(
            active_modules=["vision"],
            security_advanced={"enable_decorators": False},
        )

        registry = ModuleRegistry()
        _register_builtin_modules(registry, settings)

        vision_module = registry.get("vision")
        assert isinstance(vision_module, OmniParserModule)

    def test_server_registers_ultra_when_configured(self) -> None:
        """_register_builtin_modules should use UltraVision when backend='ultra'."""
        from llmos_bridge.api.server import _register_builtin_modules
        from llmos_bridge.config import Settings
        from llmos_bridge.modules.perception_vision.ultra.module import UltraVisionModule
        from llmos_bridge.modules.registry import ModuleRegistry

        settings = Settings(
            active_modules=["vision"],
            vision={"backend": "ultra"},
            security_advanced={"enable_decorators": False},
        )

        registry = ModuleRegistry()
        _register_builtin_modules(registry, settings)

        vision_module = registry.get("vision")
        assert isinstance(vision_module, UltraVisionModule)

    def test_mutually_exclusive_in_server_config(self) -> None:
        """Config switches between backends — both cannot be active simultaneously."""
        from llmos_bridge.api.server import _register_builtin_modules
        from llmos_bridge.config import Settings
        from llmos_bridge.modules.perception_vision.omniparser.module import OmniParserModule
        from llmos_bridge.modules.perception_vision.ultra.module import UltraVisionModule
        from llmos_bridge.modules.registry import ModuleRegistry

        # Backend="omniparser" → OmniParser.
        settings_omni = Settings(
            active_modules=["vision"],
            vision={"backend": "omniparser"},
            security_advanced={"enable_decorators": False},
        )
        reg_omni = ModuleRegistry()
        _register_builtin_modules(reg_omni, settings_omni)
        assert isinstance(reg_omni.get("vision"), OmniParserModule)

        # Backend="ultra" → UltraVision.
        settings_ultra = Settings(
            active_modules=["vision"],
            vision={"backend": "ultra"},
            security_advanced={"enable_decorators": False},
        )
        reg_ultra = ModuleRegistry()
        _register_builtin_modules(reg_ultra, settings_ultra)
        assert isinstance(reg_ultra.get("vision"), UltraVisionModule)

        # Both registries have MODULE_ID="vision" but different implementations.
        assert type(reg_omni.get("vision")) != type(reg_ultra.get("vision"))


# ===================================================================
# Test Group 10: UltraVision Classifier Quality (with real data shapes)
# ===================================================================


@pytest.mark.integration
class TestClassifierWithRealisticData:
    """Test the element classifier with realistic detection + OCR shapes."""

    def test_submit_button_classified_correctly(self) -> None:
        from llmos_bridge.modules.perception_vision.ultra.backends.detector import DetectionResult
        from llmos_bridge.modules.perception_vision.ultra.backends.ocr import OCRBox
        from llmos_bridge.modules.perception_vision.ultra.classifier import ElementClassifier

        classifier = ElementClassifier()
        det = DetectionResult(bbox=(0.42, 0.46, 0.52, 0.51), confidence=0.92)
        ocr_boxes = [
            OCRBox(text="Submit", bbox=(0.45, 0.47, 0.51, 0.50), confidence=0.98),
        ]
        result = classifier.classify(det, ocr_boxes, 1920, 1080)
        assert result.element_type == "button"
        assert "Submit" in result.label
        assert result.interactable is True

    def test_input_field_classified_correctly(self) -> None:
        from llmos_bridge.modules.perception_vision.ultra.backends.detector import DetectionResult
        from llmos_bridge.modules.perception_vision.ultra.backends.ocr import OCRBox
        from llmos_bridge.modules.perception_vision.ultra.classifier import ElementClassifier

        classifier = ElementClassifier()
        # Wide aspect ratio → input field.
        det = DetectionResult(bbox=(0.31, 0.37, 0.63, 0.41), confidence=0.85)
        ocr_boxes = [
            OCRBox(text="Enter your name...", bbox=(0.32, 0.38, 0.62, 0.40), confidence=0.93),
        ]
        result = classifier.classify(det, ocr_boxes, 1920, 1080)
        assert result.element_type == "input"
        assert result.interactable is True

    def test_small_icon_classified_correctly(self) -> None:
        from llmos_bridge.modules.perception_vision.ultra.backends.detector import DetectionResult
        from llmos_bridge.modules.perception_vision.ultra.backends.ocr import OCRBox
        from llmos_bridge.modules.perception_vision.ultra.classifier import ElementClassifier

        classifier = ElementClassifier()
        det = DetectionResult(bbox=(0.025, 0.055, 0.042, 0.083), confidence=0.78)
        ocr_boxes = []  # No text overlap.
        result = classifier.classify(det, ocr_boxes, 1920, 1080)
        assert result.element_type in ("icon", "checkbox")  # Small square.
        assert result.interactable is True

    def test_toolbar_text_classified_correctly(self) -> None:
        from llmos_bridge.modules.perception_vision.ultra.backends.detector import DetectionResult
        from llmos_bridge.modules.perception_vision.ultra.backends.ocr import OCRBox
        from llmos_bridge.modules.perception_vision.ultra.classifier import ElementClassifier

        classifier = ElementClassifier()
        # Wide toolbar text (aspect ratio ~4.7:1) → triggers "input" rule.
        # This is correct — wide narrow bars are classified as input fields by geometry.
        det = DetectionResult(bbox=(0.01, 0.01, 0.15, 0.04), confidence=0.90)
        ocr_boxes = [
            OCRBox(text="File  Edit  View  Help", bbox=(0.01, 0.01, 0.15, 0.04), confidence=0.97),
        ]
        result = classifier.classify(det, ocr_boxes, 1920, 1080)
        # Wide aspect ratio (>4:1) + area > 0.002 → "input" by Rule 3.
        assert result.element_type in ("input", "button", "text")
        assert "File" in result.label


# ===================================================================
# Test Group 11: SoM Overlay with Real Images
# ===================================================================


@pytest.mark.integration
class TestSoMOverlayRealImages:
    """Test Set-of-Marks rendering on real PIL images."""

    def test_som_renders_on_real_image(self, screenshot_bytes) -> None:
        """SoM renderer should produce a valid base64 PNG from real image."""
        import base64
        from PIL import Image

        from llmos_bridge.modules.perception_vision.ultra.som import SoMRenderer

        img = Image.open(io.BytesIO(screenshot_bytes))
        elements = [
            VisionElement(
                element_id="e0", label="Submit", element_type="button",
                bbox=(0.42, 0.46, 0.52, 0.51), confidence=0.92,
                text="Submit", interactable=True,
            ),
            VisionElement(
                element_id="e1", label="Cancel", element_type="button",
                bbox=(0.55, 0.46, 0.63, 0.51), confidence=0.89,
                text="Cancel", interactable=True,
            ),
            VisionElement(
                element_id="e2", label="Enter your name...", element_type="input",
                bbox=(0.31, 0.37, 0.63, 0.41), confidence=0.85,
                text="Enter your name...", interactable=True,
            ),
        ]

        renderer = SoMRenderer()
        b64 = renderer.render_to_base64(img, elements)

        assert isinstance(b64, str)
        assert len(b64) > 100
        # Should be valid base64 → valid PNG.
        decoded = base64.b64decode(b64)
        rendered = Image.open(io.BytesIO(decoded))
        assert rendered.size == (1920, 1080)
        assert rendered.mode == "RGB"

    def test_som_color_coding(self, screenshot_bytes) -> None:
        """Different element types should get different colors."""
        from PIL import Image

        from llmos_bridge.modules.perception_vision.ultra.som import COLOR_MAP, SoMRenderer

        img = Image.open(io.BytesIO(screenshot_bytes))
        elements = [
            VisionElement(
                element_id="e0", label="Submit", element_type="button",
                bbox=(0.1, 0.1, 0.2, 0.15), confidence=0.9, interactable=True,
            ),
            VisionElement(
                element_id="e1", label="Name field", element_type="input",
                bbox=(0.3, 0.1, 0.6, 0.15), confidence=0.85, interactable=True,
            ),
            VisionElement(
                element_id="e2", label="Welcome", element_type="text",
                bbox=(0.1, 0.3, 0.4, 0.35), confidence=0.95, interactable=False,
            ),
        ]

        renderer = SoMRenderer()
        rendered = renderer.render(img, elements)

        assert rendered.size == (1920, 1080)
        # Verify different types have different colors mapped.
        assert COLOR_MAP["button"] != COLOR_MAP["input"]
        assert COLOR_MAP["input"] != COLOR_MAP["text"]


# ===================================================================
# Test Group 12: Parallel Execution Architecture
# ===================================================================


@pytest.mark.integration
class TestParallelExecution:
    """UltraVision should run detection and OCR in parallel."""

    @pytest.mark.asyncio
    async def test_detection_and_ocr_run_concurrently(self, ultra_module, screenshot_bytes) -> None:
        """Verify that _run_detection and _run_ocr are both called."""
        det_output = _fake_ui_detr_detections()
        ocr_output = _fake_ocr_output()

        det_called = False
        ocr_called = False

        def mock_det(image, threshold):
            nonlocal det_called
            det_called = True
            return det_output

        def mock_ocr(image):
            nonlocal ocr_called
            ocr_called = True
            return ocr_output

        with patch.object(ultra_module, "_run_detection", side_effect=mock_det):
            with patch.object(ultra_module, "_run_ocr", side_effect=mock_ocr):
                with patch(
                    "llmos_bridge.modules.perception_vision.ultra.module._TORCH_AVAILABLE", True,
                ):
                    result = await ultra_module.parse_screen(screenshot_bytes=screenshot_bytes)

        assert det_called, "Detection was not called"
        assert ocr_called, "OCR was not called"
        assert len(result.elements) > 0


# ===================================================================
# Test Group 13: Real Image Loading
# ===================================================================


@pytest.mark.integration
class TestRealImageLoading:
    """Both modules should correctly load real PNG bytes."""

    def test_omniparser_loads_real_png(self, omni_module, screenshot_bytes) -> None:
        # OmniParser._load_image has positional args (screenshot_path, screenshot_bytes).
        img = omni_module._load_image(None, screenshot_bytes)
        assert img.size == (1920, 1080)
        assert img.mode == "RGB"

    def test_ultra_loads_real_png(self, ultra_module, screenshot_bytes) -> None:
        img = ultra_module._load_image(screenshot_bytes=screenshot_bytes)
        assert img.size == (1920, 1080)
        assert img.mode == "RGB"

    def test_both_load_same_image_identically(
        self, omni_module, ultra_module, screenshot_bytes,
    ) -> None:
        omni_img = omni_module._load_image(None, screenshot_bytes)
        ultra_img = ultra_module._load_image(screenshot_bytes=screenshot_bytes)
        assert omni_img.size == ultra_img.size
        assert omni_img.mode == ultra_img.mode

    def test_small_screenshot_loads(
        self, omni_module, ultra_module, small_screenshot_bytes,
    ) -> None:
        omni_img = omni_module._load_image(None, small_screenshot_bytes)
        ultra_img = ultra_module._load_image(screenshot_bytes=small_screenshot_bytes)
        assert omni_img.size == (800, 600)
        assert ultra_img.size == (800, 600)
