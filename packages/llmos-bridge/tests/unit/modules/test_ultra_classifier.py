"""Unit tests — ElementClassifier (heuristic GUI element classification)."""

from __future__ import annotations

import pytest

from llmos_bridge.modules.perception_vision.ultra.backends.detector import DetectionResult
from llmos_bridge.modules.perception_vision.ultra.backends.ocr import OCRBox
from llmos_bridge.modules.perception_vision.ultra.classifier import (
    ClassificationResult,
    ElementClassifier,
    _iou,
    _overlap_ratio,
    _position_descriptor,
)


@pytest.fixture
def classifier() -> ElementClassifier:
    return ElementClassifier()


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestHelperFunctions:
    def test_iou_identical_boxes(self) -> None:
        box = (0.1, 0.1, 0.5, 0.5)
        assert _iou(box, box) == pytest.approx(1.0)

    def test_iou_no_overlap(self) -> None:
        assert _iou((0.0, 0.0, 0.1, 0.1), (0.5, 0.5, 0.6, 0.6)) == 0.0

    def test_iou_partial_overlap(self) -> None:
        result = _iou((0.0, 0.0, 0.4, 0.4), (0.2, 0.2, 0.6, 0.6))
        assert 0.0 < result < 1.0

    def test_overlap_ratio_fully_contained(self) -> None:
        inner = (0.2, 0.2, 0.3, 0.3)
        outer = (0.1, 0.1, 0.5, 0.5)
        assert _overlap_ratio(inner, outer) == pytest.approx(1.0)

    def test_overlap_ratio_no_overlap(self) -> None:
        assert _overlap_ratio((0.0, 0.0, 0.1, 0.1), (0.5, 0.5, 0.6, 0.6)) == 0.0

    def test_position_descriptor_top_left(self) -> None:
        assert _position_descriptor((0.0, 0.0, 0.2, 0.2)) == "top-left"

    def test_position_descriptor_bottom_right(self) -> None:
        assert _position_descriptor((0.7, 0.7, 0.9, 0.9)) == "bottom-right"

    def test_position_descriptor_center(self) -> None:
        assert _position_descriptor((0.4, 0.4, 0.6, 0.6)) == "center"

    def test_position_descriptor_top(self) -> None:
        assert _position_descriptor((0.4, 0.0, 0.6, 0.2)) == "top"


# ---------------------------------------------------------------------------
# Classification rule tests
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestClassificationRules:
    def test_link_with_url(self, classifier: ElementClassifier) -> None:
        det = DetectionResult(bbox=(0.1, 0.1, 0.5, 0.15), confidence=0.9)
        ocr = [OCRBox(text="https://google.com", bbox=(0.1, 0.1, 0.5, 0.15), confidence=0.9)]
        result = classifier.classify(det, ocr, 1920, 1080)
        assert result.element_type == "link"
        assert result.interactable is True

    def test_link_with_email(self, classifier: ElementClassifier) -> None:
        det = DetectionResult(bbox=(0.1, 0.1, 0.5, 0.15), confidence=0.9)
        ocr = [OCRBox(text="user@example.com", bbox=(0.1, 0.1, 0.5, 0.15), confidence=0.9)]
        result = classifier.classify(det, ocr, 1920, 1080)
        assert result.element_type == "link"

    def test_input_wide_aspect(self, classifier: ElementClassifier) -> None:
        # Very wide box (aspect > 4:1) with area > 0.002.
        det = DetectionResult(bbox=(0.1, 0.4, 0.9, 0.45), confidence=0.8)
        result = classifier.classify(det, [], 1920, 1080)
        assert result.element_type == "input"
        assert result.interactable is True

    def test_checkbox_small_square(self, classifier: ElementClassifier) -> None:
        # Small square area < 0.0015, aspect ~1.0.
        det = DetectionResult(bbox=(0.5, 0.5, 0.52, 0.52), confidence=0.8)
        result = classifier.classify(det, [], 1920, 1080)
        assert result.element_type == "checkbox"
        assert result.interactable is True

    def test_button_with_text(self, classifier: ElementClassifier) -> None:
        det = DetectionResult(bbox=(0.3, 0.5, 0.45, 0.55), confidence=0.9)
        ocr = [OCRBox(text="Submit", bbox=(0.3, 0.5, 0.45, 0.55), confidence=0.95)]
        result = classifier.classify(det, ocr, 1920, 1080)
        assert result.element_type == "button"
        assert result.label == "Submit"
        assert result.interactable is True

    def test_text_large_area(self, classifier: ElementClassifier) -> None:
        det = DetectionResult(bbox=(0.1, 0.1, 0.9, 0.5), confidence=0.8)
        ocr = [OCRBox(text="This is a long paragraph", bbox=(0.1, 0.1, 0.9, 0.5), confidence=0.9)]
        result = classifier.classify(det, ocr, 1920, 1080)
        assert result.element_type == "text"
        assert result.interactable is False

    def test_text_long_content(self, classifier: ElementClassifier) -> None:
        long_text = "A" * 60
        # Use a box that won't trigger the "input" rule (aspect < 4:1).
        det = DetectionResult(bbox=(0.1, 0.1, 0.5, 0.3), confidence=0.8)
        ocr = [OCRBox(text=long_text, bbox=(0.1, 0.1, 0.5, 0.3), confidence=0.9)]
        result = classifier.classify(det, ocr, 1920, 1080)
        assert result.element_type == "text"

    def test_icon_no_ocr_small(self, classifier: ElementClassifier) -> None:
        # Small but above checkbox threshold (area >= 0.0015, aspect ~1.0).
        # 0.05*0.05 = 0.0025 > 0.0015, so it passes the checkbox rule.
        # Use a wider shape to break the checkbox aspect ratio check.
        det = DetectionResult(bbox=(0.05, 0.05, 0.12, 0.08), confidence=0.7)
        result = classifier.classify(det, [], 1920, 1080)
        assert result.element_type == "icon"
        assert "icon at" in result.label
        assert result.interactable is True

    def test_default_large_no_ocr(self, classifier: ElementClassifier) -> None:
        det = DetectionResult(bbox=(0.1, 0.1, 0.5, 0.5), confidence=0.5)
        result = classifier.classify(det, [], 1920, 1080)
        assert result.element_type == "icon"
        assert "element at" in result.label

    def test_no_overlap_ocr_ignored(self, classifier: ElementClassifier) -> None:
        det = DetectionResult(bbox=(0.0, 0.0, 0.1, 0.1), confidence=0.8)
        # OCR is far away — should not match.
        ocr = [OCRBox(text="Submit", bbox=(0.8, 0.8, 0.9, 0.9), confidence=0.9)]
        result = classifier.classify(det, ocr, 1920, 1080)
        assert result.label != "Submit"

    def test_link_with_www(self, classifier: ElementClassifier) -> None:
        det = DetectionResult(bbox=(0.1, 0.1, 0.5, 0.15), confidence=0.9)
        ocr = [OCRBox(text="www.example.com", bbox=(0.1, 0.1, 0.5, 0.15), confidence=0.9)]
        result = classifier.classify(det, ocr, 1920, 1080)
        assert result.element_type == "link"


# ---------------------------------------------------------------------------
# Batch classification tests
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestBatchClassification:
    def test_classify_batch_empty(self, classifier: ElementClassifier) -> None:
        results = classifier.classify_batch([], [], 1920, 1080)
        assert results == []

    def test_classify_batch_mixed(self, classifier: ElementClassifier) -> None:
        detections = [
            DetectionResult(bbox=(0.3, 0.5, 0.45, 0.55), confidence=0.9),
            DetectionResult(bbox=(0.05, 0.05, 0.12, 0.08), confidence=0.7),
        ]
        ocr = [OCRBox(text="OK", bbox=(0.3, 0.5, 0.45, 0.55), confidence=0.95)]
        results = classifier.classify_batch(detections, ocr, 1920, 1080)
        assert len(results) == 2
        assert results[0].element_type == "button"
        assert results[1].element_type == "icon"

    def test_classify_batch_returns_correct_count(self, classifier: ElementClassifier) -> None:
        detections = [
            DetectionResult(bbox=(0.1 * i, 0.1, 0.1 * i + 0.05, 0.15), confidence=0.8)
            for i in range(5)
        ]
        results = classifier.classify_batch(detections, [], 1920, 1080)
        assert len(results) == 5
