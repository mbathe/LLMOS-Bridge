"""Unit tests — UltraVision backends (detector, OCR, grounder)."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from llmos_bridge.modules.perception_vision.ultra.backends.detector import (
    BaseDetector,
    DetectionOutput,
    DetectionResult,
    UIDetrDetector,
)
from llmos_bridge.modules.perception_vision.ultra.backends.grounder import (
    BaseGrounder,
    GroundingResult,
    UGroundGrounder,
    _parse_bbox_from_text,
)
from llmos_bridge.modules.perception_vision.ultra.backends.ocr import (
    BaseOCR,
    EasyOCRFallback,
    OCRBox,
    OCROutput,
    PPOCRv5Engine,
)


# ===========================================================================
# Data model tests
# ===========================================================================

@pytest.mark.unit
class TestDetectionResult:
    def test_basic_creation(self) -> None:
        r = DetectionResult(bbox=(0.1, 0.2, 0.3, 0.4), confidence=0.9)
        assert r.bbox == (0.1, 0.2, 0.3, 0.4)
        assert r.confidence == 0.9
        assert r.class_id is None

    def test_with_class_id(self) -> None:
        r = DetectionResult(bbox=(0.0, 0.0, 1.0, 1.0), confidence=0.5, class_id=3)
        assert r.class_id == 3


@pytest.mark.unit
class TestDetectionOutput:
    def test_basic_creation(self) -> None:
        d = DetectionOutput(
            detections=[DetectionResult(bbox=(0.1, 0.1, 0.5, 0.5), confidence=0.8)],
            image_width=1920, image_height=1080,
            model_id="test", inference_time_ms=100.0,
        )
        assert len(d.detections) == 1
        assert d.model_id == "test"

    def test_empty_detections(self) -> None:
        d = DetectionOutput(
            detections=[], image_width=1920, image_height=1080,
            model_id="test", inference_time_ms=0.0,
        )
        assert len(d.detections) == 0


@pytest.mark.unit
class TestOCRBox:
    def test_basic_creation(self) -> None:
        b = OCRBox(text="Hello", bbox=(0.1, 0.2, 0.3, 0.4), confidence=0.95)
        assert b.text == "Hello"
        assert b.language == "en"


@pytest.mark.unit
class TestOCROutput:
    def test_basic_creation(self) -> None:
        o = OCROutput(
            boxes=[OCRBox(text="Hello", bbox=(0.1, 0.1, 0.3, 0.2), confidence=0.9)],
            full_text="Hello",
            inference_time_ms=50.0,
            engine_id="test",
        )
        assert o.full_text == "Hello"


@pytest.mark.unit
class TestGroundingResult:
    def test_basic_creation(self) -> None:
        r = GroundingResult(
            bbox=(0.1, 0.2, 0.3, 0.4), confidence=0.8, query="search button",
        )
        assert r.query == "search button"


# ===========================================================================
# ABC tests
# ===========================================================================

@pytest.mark.unit
class TestBaseDetectorABC:
    def test_cannot_instantiate(self) -> None:
        with pytest.raises(TypeError):
            BaseDetector()  # type: ignore[abstract]

    def test_has_required_methods(self) -> None:
        methods = {"load", "detect", "unload", "is_loaded", "vram_estimate_mb"}
        for m in methods:
            assert hasattr(BaseDetector, m)


@pytest.mark.unit
class TestBaseOCRABC:
    def test_cannot_instantiate(self) -> None:
        with pytest.raises(TypeError):
            BaseOCR()  # type: ignore[abstract]

    def test_has_required_methods(self) -> None:
        methods = {"load", "recognize", "unload", "is_loaded"}
        for m in methods:
            assert hasattr(BaseOCR, m)


@pytest.mark.unit
class TestBaseGrounderABC:
    def test_cannot_instantiate(self) -> None:
        with pytest.raises(TypeError):
            BaseGrounder()  # type: ignore[abstract]

    def test_has_required_methods(self) -> None:
        methods = {"load", "ground", "unload", "is_loaded", "vram_estimate_mb"}
        for m in methods:
            assert hasattr(BaseGrounder, m)


# ===========================================================================
# UIDetrDetector tests
# ===========================================================================

@pytest.mark.unit
class TestUIDetrDetector:
    def test_initial_state(self) -> None:
        d = UIDetrDetector(repo_id="test/model")
        assert d.is_loaded is False
        assert d.vram_estimate_mb == 500

    def test_detect_raises_when_not_loaded(self) -> None:
        d = UIDetrDetector()
        mock_img = MagicMock()
        mock_img.size = (1920, 1080)
        with pytest.raises(RuntimeError, match="not loaded"):
            d.detect(mock_img)

    def test_unload_clears_model(self) -> None:
        d = UIDetrDetector()
        d._model = MagicMock()
        d._processor = MagicMock()
        with patch.dict("sys.modules", {"torch": MagicMock()}):
            d.unload()
        assert d._model is None
        assert d._processor is None
        assert d.is_loaded is False

    def test_unload_when_already_none(self) -> None:
        d = UIDetrDetector()
        d.unload()  # Should not raise.
        assert d.is_loaded is False

    def test_detect_yolo_path(self) -> None:
        """Test the YOLO detection path with mocked model."""
        d = UIDetrDetector()
        d._processor = "yolo"

        # Mock YOLO model results.
        mock_box = MagicMock()
        mock_box.xyxy.__getitem__ = lambda self, i: MagicMock(
            cpu=lambda: MagicMock(tolist=lambda: [100, 200, 300, 400])
        )
        mock_box.conf.__getitem__ = lambda self, i: 0.85
        mock_box.__len__ = lambda self: 1

        mock_result = MagicMock()
        mock_result.boxes = mock_box
        d._model = MagicMock(return_value=[mock_result])

        mock_img = MagicMock()
        mock_img.size = (1920, 1080)

        output = d.detect(mock_img)
        assert isinstance(output, DetectionOutput)
        assert output.image_width == 1920
        assert output.model_id == "yolo-v8-fallback"

    def test_detect_transformers_path(self) -> None:
        """Test the transformers detection path with mocked model."""
        d = UIDetrDetector()

        mock_processor = MagicMock()
        mock_model = MagicMock()
        d._processor = mock_processor
        d._model = mock_model
        d._device = "cpu"

        # Mock transformers post-processing output.
        mock_torch = MagicMock()
        mock_torch.inference_mode.return_value.__enter__ = lambda s: None
        mock_torch.inference_mode.return_value.__exit__ = lambda s, *a: None

        post_result = {
            "scores": [MagicMock(__float__=lambda s: 0.9)],
            "boxes": [MagicMock(cpu=lambda: MagicMock(tolist=lambda: [100, 200, 300, 400]))],
        }
        mock_processor.post_process_object_detection.return_value = [post_result]

        mock_img = MagicMock()
        mock_img.size = (1920, 1080)

        with patch.dict("sys.modules", {"torch": mock_torch}):
            output = d.detect(mock_img)

        assert isinstance(output, DetectionOutput)
        assert output.model_id == "ui-detr-1-transformers"


# ===========================================================================
# PPOCRv5Engine tests
# ===========================================================================

@pytest.mark.unit
class TestPPOCRv5Engine:
    def test_initial_state(self) -> None:
        e = PPOCRv5Engine()
        assert e.is_loaded is False

    def test_recognize_raises_when_not_loaded(self) -> None:
        e = PPOCRv5Engine()
        with pytest.raises(RuntimeError, match="not loaded"):
            e.recognize(MagicMock())

    def test_unload(self) -> None:
        e = PPOCRv5Engine()
        e._engine = MagicMock()
        e.unload()
        assert e.is_loaded is False

    def test_recognize_with_mock_engine(self) -> None:
        """Test OCR recognition with mocked PaddleOCR."""
        e = PPOCRv5Engine()
        mock_engine = MagicMock()
        mock_engine.ocr.return_value = [[
            [[[10, 20], [100, 20], [100, 40], [10, 40]], ("Hello World", 0.95)],
        ]]
        e._engine = mock_engine

        mock_img = MagicMock()
        mock_img.size = (200, 100)

        mock_np = MagicMock()
        mock_np.array.return_value = MagicMock()
        with patch.dict("sys.modules", {"numpy": mock_np}):
            output = e.recognize(mock_img)

        assert isinstance(output, OCROutput)
        assert output.engine_id == "ppocr-v5"
        assert len(output.boxes) == 1
        assert output.boxes[0].text == "Hello World"
        assert output.boxes[0].confidence == 0.95

    def test_recognize_empty_result(self) -> None:
        e = PPOCRv5Engine()
        mock_engine = MagicMock()
        mock_engine.ocr.return_value = [None]
        e._engine = mock_engine

        mock_img = MagicMock()
        mock_img.size = (200, 100)

        mock_np = MagicMock()
        mock_np.array.return_value = MagicMock()
        with patch.dict("sys.modules", {"numpy": mock_np}):
            output = e.recognize(mock_img)

        assert len(output.boxes) == 0
        assert output.full_text == ""


# ===========================================================================
# EasyOCRFallback tests
# ===========================================================================

@pytest.mark.unit
class TestEasyOCRFallback:
    def test_initial_state(self) -> None:
        e = EasyOCRFallback()
        assert e.is_loaded is False

    def test_recognize_raises_when_not_loaded(self) -> None:
        e = EasyOCRFallback()
        with pytest.raises(RuntimeError, match="not loaded"):
            e.recognize(MagicMock())

    def test_unload(self) -> None:
        e = EasyOCRFallback()
        e._reader = MagicMock()
        e.unload()
        assert e.is_loaded is False

    def test_recognize_with_mock_reader(self) -> None:
        e = EasyOCRFallback()
        mock_reader = MagicMock()
        mock_reader.readtext.return_value = [
            ([[10, 20], [100, 20], [100, 40], [10, 40]], "Test Text", 0.88),
        ]
        e._reader = mock_reader

        mock_img = MagicMock()

        mock_np = MagicMock()
        mock_np.array.return_value = MagicMock(shape=(100, 200, 3))
        with patch.dict("sys.modules", {"numpy": mock_np}):
            output = e.recognize(mock_img)

        assert isinstance(output, OCROutput)
        assert output.engine_id == "easyocr"
        assert len(output.boxes) == 1
        assert output.boxes[0].text == "Test Text"


# ===========================================================================
# UGroundGrounder tests
# ===========================================================================

@pytest.mark.unit
class TestUGroundGrounder:
    def test_initial_state(self) -> None:
        g = UGroundGrounder(repo_id="test/model")
        assert g.is_loaded is False
        assert g.vram_estimate_mb == 1500

    def test_ground_raises_when_not_loaded(self) -> None:
        g = UGroundGrounder()
        with pytest.raises(RuntimeError, match="not loaded"):
            g.ground(MagicMock(), "search button")

    def test_unload_clears_model(self) -> None:
        g = UGroundGrounder()
        g._model = MagicMock()
        g._processor = MagicMock()
        with patch.dict("sys.modules", {"torch": MagicMock()}):
            g.unload()
        assert g._model is None
        assert g._processor is None
        assert g.is_loaded is False

    def test_unload_cancels_timer(self) -> None:
        g = UGroundGrounder(idle_timeout=10.0)
        g._model = MagicMock()
        g._processor = MagicMock()
        mock_timer = MagicMock()
        g._unload_timer = mock_timer
        with patch.dict("sys.modules", {"torch": MagicMock()}):
            g.unload()
        mock_timer.cancel.assert_called_once()

    def test_idle_timeout_setting(self) -> None:
        g = UGroundGrounder(idle_timeout=30.0)
        assert g._idle_timeout == 30.0


# ===========================================================================
# Bbox parsing tests
# ===========================================================================

@pytest.mark.unit
class TestParseBboxFromText:
    def test_box_tags(self) -> None:
        result = _parse_bbox_from_text("<box>0.1 0.2 0.3 0.4</box>")
        assert result is not None
        assert result == pytest.approx((0.1, 0.2, 0.3, 0.4), abs=0.01)

    def test_bracket_format(self) -> None:
        result = _parse_bbox_from_text("[0.1, 0.2, 0.3, 0.4]")
        assert result is not None
        assert result == pytest.approx((0.1, 0.2, 0.3, 0.4), abs=0.01)

    def test_paren_format(self) -> None:
        result = _parse_bbox_from_text("(0.1, 0.2, 0.3, 0.4)")
        assert result is not None
        assert result == pytest.approx((0.1, 0.2, 0.3, 0.4), abs=0.01)

    def test_raw_coordinates(self) -> None:
        result = _parse_bbox_from_text("\n0.1 0.2 0.3 0.4")
        assert result is not None

    def test_scaled_coordinates(self) -> None:
        """UGround sometimes outputs in [0, 1000] scale."""
        result = _parse_bbox_from_text("<box>100 200 300 400</box>")
        assert result is not None
        assert all(0.0 <= c <= 1.0 for c in result)

    def test_no_match_returns_none(self) -> None:
        assert _parse_bbox_from_text("No coordinates here") is None

    def test_empty_string(self) -> None:
        assert _parse_bbox_from_text("") is None

    def test_swapped_coordinates_fixed(self) -> None:
        """Ensure x1 < x2 and y1 < y2."""
        result = _parse_bbox_from_text("[0.5, 0.5, 0.1, 0.1]")
        assert result is not None
        x1, y1, x2, y2 = result
        assert x1 <= x2
        assert y1 <= y2

    def test_clamped_to_0_1(self) -> None:
        result = _parse_bbox_from_text("[0.0, 0.0, 1.0, 1.0]")
        assert result is not None
        for c in result:
            assert 0.0 <= c <= 1.0

    def test_box_with_text_around(self) -> None:
        text = "The element is at <box>0.2 0.3 0.4 0.5</box> on the screen."
        result = _parse_bbox_from_text(text)
        assert result is not None
        assert result == pytest.approx((0.2, 0.3, 0.4, 0.5), abs=0.01)
