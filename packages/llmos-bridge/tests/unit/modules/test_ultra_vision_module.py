"""Unit tests — UltraVisionModule (all torch/PIL/mss calls mocked).

Tests the UltraVision module: field mapping, action handlers,
cache integration, scene graph, fallback behavior, etc.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llmos_bridge.modules.perception_vision.base import (
    BaseVisionModule,
    VisionElement,
    VisionParseResult,
)
from llmos_bridge.modules.perception_vision.ultra.module import UltraVisionModule


@pytest.fixture
def module() -> UltraVisionModule:
    with patch.dict(os.environ, {
        "LLMOS_ULTRA_VISION_MODEL_DIR": "/fake/models",
        "LLMOS_ULTRA_VISION_AUTO_DOWNLOAD": "false",
        "LLMOS_ULTRA_VISION_ENABLE_GROUNDING": "false",
    }):
        with patch.object(UltraVisionModule, "_check_dependencies"):
            m = UltraVisionModule()
    return m


def _mock_parse_result() -> VisionParseResult:
    return VisionParseResult(
        elements=[
            VisionElement(
                element_id="e0000",
                label="Submit",
                element_type="button",
                bbox=(0.5, 0.5, 0.6, 0.55),
                confidence=0.95,
                text="Submit",
                interactable=True,
            ),
            VisionElement(
                element_id="e0001",
                label="Cancel",
                element_type="button",
                bbox=(0.3, 0.5, 0.4, 0.55),
                confidence=0.88,
                text="Cancel",
                interactable=True,
            ),
        ],
        width=1920,
        height=1080,
        raw_ocr="Submit Cancel",
        labeled_image_b64=None,
        parse_time_ms=500.0,
        model_id="ultra-vision-test",
    )


# ===========================================================================
# Module metadata tests
# ===========================================================================

@pytest.mark.unit
class TestModuleMetadata:
    def test_module_id(self, module: UltraVisionModule) -> None:
        assert module.MODULE_ID == "vision"

    def test_version(self, module: UltraVisionModule) -> None:
        assert module.VERSION == "1.0.0"

    def test_is_vision_subclass(self, module: UltraVisionModule) -> None:
        assert isinstance(module, BaseVisionModule)

    def test_supported_platforms(self, module: UltraVisionModule) -> None:
        from llmos_bridge.modules.base import Platform
        assert Platform.LINUX in module.SUPPORTED_PLATFORMS


# ===========================================================================
# Manifest tests
# ===========================================================================

@pytest.mark.unit
class TestGetManifest:
    def test_manifest_has_actions(self, module: UltraVisionModule) -> None:
        manifest = module.get_manifest()
        assert manifest.module_id == "vision"
        action_names = {a.name for a in manifest.actions}
        assert "parse_screen" in action_names
        assert "capture_and_parse" in action_names
        assert "find_element" in action_names
        assert "get_screen_text" in action_names

    def test_manifest_version(self, module: UltraVisionModule) -> None:
        manifest = module.get_manifest()
        assert manifest.version == "1.0.0"

    def test_manifest_description_mentions_ultra(self, module: UltraVisionModule) -> None:
        manifest = module.get_manifest()
        assert "UltraVision" in manifest.description

    def test_manifest_tags(self, module: UltraVisionModule) -> None:
        manifest = module.get_manifest()
        assert "ultra" in manifest.tags
        assert "grounding" in manifest.tags

    def test_manifest_permissions(self, module: UltraVisionModule) -> None:
        manifest = module.get_manifest()
        assert "screen_capture" in manifest.declared_permissions


# ===========================================================================
# Config tests
# ===========================================================================

@pytest.mark.unit
class TestConfig:
    def test_model_dir_from_env(self) -> None:
        with patch.dict(os.environ, {"LLMOS_ULTRA_VISION_MODEL_DIR": "/custom/path"}):
            m = UltraVisionModule()
        assert m._model_dir == "/custom/path"

    def test_default_model_dir(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            # Remove if set.
            env = dict(os.environ)
            env.pop("LLMOS_ULTRA_VISION_MODEL_DIR", None)
            with patch.dict(os.environ, env, clear=True):
                m = UltraVisionModule()
        assert ".llmos/models/ultra_vision" in m._model_dir

    def test_box_threshold_from_env(self) -> None:
        with patch.dict(os.environ, {"LLMOS_ULTRA_VISION_BOX_THRESH": "0.5"}):
            m = UltraVisionModule()
        assert m._box_threshold == 0.5

    def test_ocr_engine_from_env(self) -> None:
        with patch.dict(os.environ, {"LLMOS_ULTRA_VISION_OCR_ENGINE": "easyocr"}):
            m = UltraVisionModule()
        assert m._ocr_engine_name == "easyocr"

    def test_grounding_disabled(self) -> None:
        with patch.dict(os.environ, {"LLMOS_ULTRA_VISION_ENABLE_GROUNDING": "false"}):
            m = UltraVisionModule()
        assert m._enable_grounding is False

    def test_auto_download_from_env(self) -> None:
        with patch.dict(os.environ, {"LLMOS_ULTRA_VISION_AUTO_DOWNLOAD": "false"}):
            m = UltraVisionModule()
        assert m._auto_download is False


# ===========================================================================
# parse_screen tests
# ===========================================================================

@pytest.mark.unit
class TestParseScreen:
    @pytest.mark.asyncio
    async def test_parse_screen_pil_only_fallback(self, module: UltraVisionModule) -> None:
        """When torch is not available, falls back to PIL-only path."""
        with patch("llmos_bridge.modules.perception_vision.ultra.module._TORCH_AVAILABLE", False):
            mock_img = MagicMock()
            mock_img.size = (1920, 1080)
            with patch.object(module, "_load_image", return_value=mock_img):
                with patch("llmos_bridge.modules.perception_vision.ultra.module._PIL_AVAILABLE", True):
                    result = await module.parse_screen(screenshot_bytes=b"fake")
        assert isinstance(result, VisionParseResult)
        assert "pil-fallback" in result.model_id

    @pytest.mark.asyncio
    async def test_parse_screen_cache_hit(self, module: UltraVisionModule) -> None:
        """Should return cached result when screenshot bytes match."""
        cached = _mock_parse_result()
        mock_cache = MagicMock()
        mock_cache.get.return_value = cached
        module._cache = mock_cache

        result = await module.parse_screen(screenshot_bytes=b"cached_bytes")
        assert result is cached

    @pytest.mark.asyncio
    async def test_parse_screen_requires_input(self, module: UltraVisionModule) -> None:
        """Should raise when no screenshot is provided."""
        from llmos_bridge.exceptions import ActionExecutionError
        with pytest.raises(ActionExecutionError):
            await module.parse_screen()

    @pytest.mark.asyncio
    async def test_parse_screen_full_pipeline(self, module: UltraVisionModule) -> None:
        """Test the full pipeline with mocked backends."""
        from llmos_bridge.modules.perception_vision.ultra.backends.detector import DetectionOutput, DetectionResult
        from llmos_bridge.modules.perception_vision.ultra.backends.ocr import OCRBox, OCROutput
        from llmos_bridge.modules.perception_vision.ultra.classifier import ClassificationResult

        mock_img = MagicMock()
        mock_img.size = (1920, 1080)

        det_output = DetectionOutput(
            detections=[DetectionResult(bbox=(0.3, 0.5, 0.45, 0.55), confidence=0.9)],
            image_width=1920, image_height=1080,
            model_id="test-detector", inference_time_ms=100,
        )
        ocr_output = OCROutput(
            boxes=[OCRBox(text="Submit", bbox=(0.3, 0.5, 0.45, 0.55), confidence=0.95)],
            full_text="Submit",
            inference_time_ms=50,
            engine_id="test-ocr",
        )

        with patch.object(module, "_load_image", return_value=mock_img):
            with patch.object(module, "_run_detection", return_value=det_output):
                with patch.object(module, "_run_ocr", return_value=ocr_output):
                    with patch.object(module, "_get_som_renderer") as mock_som:
                        mock_som.return_value.render_to_base64.return_value = "base64data"
                        with patch("llmos_bridge.modules.perception_vision.ultra.module._TORCH_AVAILABLE", True):
                            with patch("llmos_bridge.modules.perception_vision.ultra.module._PIL_AVAILABLE", True):
                                result = await module.parse_screen(screenshot_bytes=b"test")

        assert isinstance(result, VisionParseResult)
        assert len(result.elements) >= 1
        assert result.width == 1920
        assert result.height == 1080
        assert result.raw_ocr == "Submit"


# ===========================================================================
# Action handler tests
# ===========================================================================

@pytest.mark.unit
class TestActionParseScreen:
    @pytest.mark.asyncio
    async def test_with_screenshot_path(self, module: UltraVisionModule) -> None:
        mock_result = _mock_parse_result()
        with patch.object(module, "parse_screen", new_callable=AsyncMock, return_value=mock_result):
            with patch("builtins.open", MagicMock()):
                result = await module._action_parse_screen({"screenshot_path": "/tmp/test.png"})
        assert "elements" in result

    @pytest.mark.asyncio
    async def test_without_screenshot_path(self, module: UltraVisionModule) -> None:
        mock_result = _mock_parse_result()
        with patch.object(module, "parse_screen", new_callable=AsyncMock, return_value=mock_result):
            with patch.object(module, "_capture_screen", new_callable=AsyncMock, return_value=b"bytes"):
                result = await module._action_parse_screen({})
        assert "elements" in result


@pytest.mark.unit
class TestActionCaptureAndParse:
    @pytest.mark.asyncio
    async def test_basic(self, module: UltraVisionModule) -> None:
        mock_result = _mock_parse_result()
        with patch.object(module, "parse_screen", new_callable=AsyncMock, return_value=mock_result):
            with patch.object(module, "_capture_screen", new_callable=AsyncMock, return_value=b"bytes"):
                result = await module._action_capture_and_parse({})
        assert "elements" in result

    @pytest.mark.asyncio
    async def test_with_monitor(self, module: UltraVisionModule) -> None:
        mock_result = _mock_parse_result()
        with patch.object(module, "parse_screen", new_callable=AsyncMock, return_value=mock_result):
            with patch.object(module, "_capture_screen", new_callable=AsyncMock, return_value=b"bytes") as mock_cap:
                await module._action_capture_and_parse({"monitor": 1})
                mock_cap.assert_called_once_with(monitor=1, region=None)


@pytest.mark.unit
class TestActionFindElement:
    @pytest.mark.asyncio
    async def test_find_element_fallback_found(self, module: UltraVisionModule) -> None:
        """Fallback path: parse + substring match."""
        mock_result = _mock_parse_result()
        with patch.object(module, "parse_screen", new_callable=AsyncMock, return_value=mock_result):
            with patch.object(module, "_capture_screen", new_callable=AsyncMock, return_value=b"bytes"):
                with patch.object(module, "_load_image", return_value=MagicMock(size=(1920, 1080))):
                    result = await module._action_find_element({"query": "Submit"})
        assert result["found"] is True
        assert result["element"]["label"] == "Submit"
        assert result["pixel_x"] is not None

    @pytest.mark.asyncio
    async def test_find_element_not_found(self, module: UltraVisionModule) -> None:
        mock_result = _mock_parse_result()
        with patch.object(module, "parse_screen", new_callable=AsyncMock, return_value=mock_result):
            with patch.object(module, "_capture_screen", new_callable=AsyncMock, return_value=b"bytes"):
                with patch.object(module, "_load_image", return_value=MagicMock(size=(1920, 1080))):
                    result = await module._action_find_element({"query": "NonexistentButton"})
        assert result["found"] is False
        assert result["element"] is None

    @pytest.mark.asyncio
    async def test_find_element_with_type_filter(self, module: UltraVisionModule) -> None:
        mock_result = _mock_parse_result()
        with patch.object(module, "parse_screen", new_callable=AsyncMock, return_value=mock_result):
            with patch.object(module, "_capture_screen", new_callable=AsyncMock, return_value=b"bytes"):
                with patch.object(module, "_load_image", return_value=MagicMock(size=(1920, 1080))):
                    result = await module._action_find_element({
                        "query": "Submit", "element_type": "icon",
                    })
        # "Submit" is a button, not an icon.
        assert result["found"] is False

    @pytest.mark.asyncio
    async def test_find_element_with_grounding(self, module: UltraVisionModule) -> None:
        """UGround grounding path."""
        from llmos_bridge.modules.perception_vision.ultra.backends.grounder import GroundingResult

        mock_grounder = MagicMock()
        mock_grounder.ground.return_value = GroundingResult(
            bbox=(0.3, 0.4, 0.5, 0.6), confidence=0.85, query="Search",
        )
        module._grounder = mock_grounder
        module._enable_grounding = True

        with patch.object(module, "_capture_screen", new_callable=AsyncMock, return_value=b"bytes"):
            with patch.object(module, "_load_image", return_value=MagicMock(size=(1920, 1080))):
                result = await module._action_find_element({"query": "Search"})
        assert result["found"] is True
        assert result["element"]["extra"]["source"] == "uground_grounding"


@pytest.mark.unit
class TestActionGetScreenText:
    @pytest.mark.asyncio
    async def test_basic(self, module: UltraVisionModule) -> None:
        mock_result = _mock_parse_result()
        with patch.object(module, "parse_screen", new_callable=AsyncMock, return_value=mock_result):
            with patch.object(module, "_capture_screen", new_callable=AsyncMock, return_value=b"bytes"):
                result = await module._action_get_screen_text({})
        assert "text" in result
        assert "line_count" in result
        assert result["text"] == "Submit Cancel"


# ===========================================================================
# Lazy loading tests
# ===========================================================================

@pytest.mark.unit
class TestLazyLoading:
    def test_get_classifier_returns_classifier(self, module: UltraVisionModule) -> None:
        from llmos_bridge.modules.perception_vision.ultra.classifier import ElementClassifier
        cls = module._get_classifier()
        assert isinstance(cls, ElementClassifier)

    def test_get_classifier_cached(self, module: UltraVisionModule) -> None:
        cls1 = module._get_classifier()
        cls2 = module._get_classifier()
        assert cls1 is cls2

    def test_get_som_renderer_returns_renderer(self, module: UltraVisionModule) -> None:
        from llmos_bridge.modules.perception_vision.ultra.som import SoMRenderer
        r = module._get_som_renderer()
        assert isinstance(r, SoMRenderer)

    def test_get_grounder_none_when_disabled(self, module: UltraVisionModule) -> None:
        module._enable_grounding = False
        assert module._get_grounder() is None

    def test_get_weight_manager(self, module: UltraVisionModule) -> None:
        from llmos_bridge.modules.perception_vision.ultra.weight_manager import WeightManager
        wm = module._get_weight_manager()
        assert isinstance(wm, WeightManager)

    def test_get_vram_budget(self, module: UltraVisionModule) -> None:
        from llmos_bridge.modules.perception_vision.ultra.weight_manager import VRAMBudget
        budget = module._get_vram_budget()
        assert isinstance(budget, VRAMBudget)


# ===========================================================================
# Overlap detection tests
# ===========================================================================

@pytest.mark.unit
class TestOverlapDetection:
    def test_no_overlap(self) -> None:
        assert UltraVisionModule._overlaps_any(
            (0.0, 0.0, 0.1, 0.1), [(0.5, 0.5, 0.6, 0.6)],
        ) is False

    def test_has_overlap(self) -> None:
        assert UltraVisionModule._overlaps_any(
            (0.1, 0.1, 0.3, 0.3), [(0.1, 0.1, 0.3, 0.3)],
        ) is True

    def test_empty_existing(self) -> None:
        assert UltraVisionModule._overlaps_any(
            (0.1, 0.1, 0.3, 0.3), [],
        ) is False

    def test_partial_overlap_below_threshold(self) -> None:
        assert UltraVisionModule._overlaps_any(
            (0.0, 0.0, 0.2, 0.2), [(0.15, 0.15, 0.35, 0.35)], threshold=0.5,
        ) is False


# ===========================================================================
# Image loading tests
# ===========================================================================

@pytest.mark.unit
class TestImageLoading:
    def test_load_from_bytes(self, module: UltraVisionModule) -> None:
        """Test loading from bytes with mocked PIL."""
        mock_img = MagicMock()
        mock_img.convert.return_value = mock_img
        with patch("llmos_bridge.modules.perception_vision.ultra.module.PILImage") as mock_pil:
            mock_pil_module = MagicMock()
            with patch("PIL.Image.open", return_value=mock_img):
                img = module._load_image(screenshot_bytes=b"\x89PNG\r\n\x1a\n")
                # Should not raise.

    def test_load_raises_no_input(self, module: UltraVisionModule) -> None:
        from llmos_bridge.exceptions import ActionExecutionError
        with pytest.raises(ActionExecutionError):
            module._load_image()

    def test_assert_pil_available_raises(self) -> None:
        from llmos_bridge.exceptions import ModuleLoadError
        with patch("llmos_bridge.modules.perception_vision.ultra.module._PIL_AVAILABLE", False):
            m = UltraVisionModule()
            with pytest.raises(ModuleLoadError):
                m._assert_pil_available()
