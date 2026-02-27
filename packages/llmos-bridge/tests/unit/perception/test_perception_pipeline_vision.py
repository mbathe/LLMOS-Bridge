"""Unit tests — PerceptionPipeline with vision module integration."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llmos_bridge.perception.pipeline import ActionPerceptionResult, PerceptionPipeline
from llmos_bridge.modules.perception_vision.base import VisionElement, VisionParseResult
from llmos_bridge.protocol.models import PerceptionConfig


def _make_vision_result() -> VisionParseResult:
    return VisionParseResult(
        elements=[
            VisionElement(
                element_id="e0",
                label="Button",
                element_type="button",
                bbox=(0.5, 0.5, 0.6, 0.55),
                confidence=0.9,
                interactable=True,
            ),
            VisionElement(
                element_id="e1",
                label="Input",
                element_type="input",
                bbox=(0.2, 0.3, 0.4, 0.35),
                confidence=0.85,
                interactable=True,
            ),
        ],
        width=1920,
        height=1080,
        raw_ocr="Button Input Some Text",
        labeled_image_b64=None,
        parse_time_ms=200.0,
        model_id="test-vision",
    )


@pytest.fixture
def mock_capture() -> MagicMock:
    capture = MagicMock()
    screenshot = MagicMock()
    screenshot.data = b"fake_png_data"
    capture.capture = AsyncMock(return_value=screenshot)
    return capture


@pytest.fixture
def mock_ocr() -> MagicMock:
    ocr = MagicMock()
    ocr_result = MagicMock()
    ocr_result.text = "OCR text output"
    ocr_result.confidence = 85.0
    ocr.extract = AsyncMock(return_value=ocr_result)
    return ocr


@pytest.fixture
def mock_vision_module() -> MagicMock:
    vision = MagicMock()
    vision.parse_screen = AsyncMock(return_value=_make_vision_result())
    return vision


@pytest.mark.unit
class TestPipelineWithVision:
    @pytest.mark.asyncio
    async def test_vision_fields_populated(
        self, mock_capture: MagicMock, mock_ocr: MagicMock, mock_vision_module: MagicMock
    ) -> None:
        pipeline = PerceptionPipeline(
            capture=mock_capture,
            ocr=mock_ocr,
            vision_module=mock_vision_module,
        )
        config = PerceptionConfig(
            capture_before=False,
            capture_after=True,
            ocr_enabled=True,
        )
        result = await pipeline.run_after("action_1", config)
        assert result.vision_elements is not None
        assert result.vision_element_count == 2
        assert result.vision_interactable_count == 2

    @pytest.mark.asyncio
    async def test_vision_fields_in_to_dict(
        self, mock_capture: MagicMock, mock_ocr: MagicMock, mock_vision_module: MagicMock
    ) -> None:
        pipeline = PerceptionPipeline(
            capture=mock_capture,
            ocr=mock_ocr,
            vision_module=mock_vision_module,
        )
        config = PerceptionConfig(capture_after=True, ocr_enabled=True)
        result = await pipeline.run_after("action_1", config)
        d = result.to_dict()
        assert "vision_elements" in d
        assert "vision_element_count" in d
        assert "vision_interactable_count" in d

    @pytest.mark.asyncio
    async def test_no_vision_fields_when_no_module(
        self, mock_capture: MagicMock, mock_ocr: MagicMock
    ) -> None:
        pipeline = PerceptionPipeline(
            capture=mock_capture,
            ocr=mock_ocr,
            vision_module=None,
        )
        config = PerceptionConfig(capture_after=True, ocr_enabled=True)
        result = await pipeline.run_after("action_1", config)
        d = result.to_dict()
        assert "vision_elements" not in d
        assert result.vision_elements is None

    @pytest.mark.asyncio
    async def test_vision_failure_is_soft(
        self, mock_capture: MagicMock, mock_ocr: MagicMock
    ) -> None:
        vision = MagicMock()
        vision.parse_screen = AsyncMock(side_effect=RuntimeError("model load failed"))
        pipeline = PerceptionPipeline(
            capture=mock_capture,
            ocr=mock_ocr,
            vision_module=vision,
        )
        config = PerceptionConfig(capture_after=True, ocr_enabled=True)
        # Should not raise
        result = await pipeline.run_after("action_1", config)
        assert result.vision_elements is None  # Failed gracefully

    @pytest.mark.asyncio
    async def test_vision_enriches_missing_ocr_text(
        self, mock_capture: MagicMock, mock_vision_module: MagicMock
    ) -> None:
        """When OCR is disabled but vision is available, use vision's raw_ocr."""
        pipeline = PerceptionPipeline(
            capture=mock_capture,
            ocr=MagicMock(),  # OCR won't be called since ocr_enabled=False
            vision_module=mock_vision_module,
        )
        config = PerceptionConfig(capture_after=True, ocr_enabled=False)
        result = await pipeline.run_after("action_1", config)
        assert result.after_text == "Button Input Some Text"

    @pytest.mark.asyncio
    async def test_vision_caps_elements_at_50(
        self, mock_capture: MagicMock, mock_ocr: MagicMock
    ) -> None:
        many_elements = [
            VisionElement(
                element_id=f"e{i}",
                label=f"Elem{i}",
                element_type="text",
                bbox=(0.1, 0.1, 0.2, 0.2),
                confidence=0.5,
            )
            for i in range(100)
        ]
        big_result = VisionParseResult(
            elements=many_elements,
            width=1920, height=1080,
            raw_ocr="", parse_time_ms=300.0, model_id="test",
        )
        vision = MagicMock()
        vision.parse_screen = AsyncMock(return_value=big_result)
        pipeline = PerceptionPipeline(
            capture=mock_capture,
            ocr=mock_ocr,
            vision_module=vision,
        )
        config = PerceptionConfig(capture_after=True, ocr_enabled=True)
        result = await pipeline.run_after("action_1", config)
        assert result.vision_element_count == 100
        assert len(result.vision_elements) == 50  # Capped

    @pytest.mark.asyncio
    async def test_no_vision_parse_when_no_screenshot(
        self, mock_ocr: MagicMock, mock_vision_module: MagicMock
    ) -> None:
        capture = MagicMock()
        capture.capture = AsyncMock(return_value=None)
        pipeline = PerceptionPipeline(
            capture=capture,
            ocr=mock_ocr,
            vision_module=mock_vision_module,
        )
        config = PerceptionConfig(capture_after=False, ocr_enabled=False)
        result = await pipeline.run_after("action_1", config)
        # Vision shouldn't be called when capture_after is False
        mock_vision_module.parse_screen.assert_not_called()


@pytest.mark.unit
class TestActionPerceptionResultVisionFields:
    def test_to_dict_without_vision(self) -> None:
        r = ActionPerceptionResult(action_id="a1", captured=True)
        d = r.to_dict()
        assert "vision_elements" not in d

    def test_to_dict_with_vision(self) -> None:
        r = ActionPerceptionResult(
            action_id="a1",
            captured=True,
            vision_elements=[{"label": "X"}],
            vision_element_count=1,
            vision_interactable_count=0,
        )
        d = r.to_dict()
        assert d["vision_elements"] == [{"label": "X"}]
        assert d["vision_element_count"] == 1
        assert d["vision_interactable_count"] == 0
