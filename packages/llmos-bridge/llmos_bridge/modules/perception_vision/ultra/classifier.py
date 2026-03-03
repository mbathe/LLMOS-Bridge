"""Heuristic GUI element type classifier.

Replaces Florence-2 captioning (~2-3s GPU per parse) with pure Python
geometry + OCR overlap heuristics (~1ms total).

Given a detected bounding box and any overlapping OCR text regions,
classify the element as button, input, text, icon, link, or checkbox
and produce a human-readable label.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from llmos_bridge.modules.perception_vision.ultra.backends.detector import DetectionResult
from llmos_bridge.modules.perception_vision.ultra.backends.ocr import OCRBox


# URL-like patterns for link detection.
_URL_RE = re.compile(
    r"(?:https?://|www\.|\.com|\.org|\.net|\.io|\.fr|\.de|\.uk|/[a-z])"
)

# Email pattern.
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")


@dataclass
class ClassificationResult:
    """Result of classifying a detected GUI element."""

    element_type: str
    """Semantic type: button, input, text, icon, link, checkbox, other."""

    label: str
    """Human-readable label for the element."""

    interactable: bool
    """Whether the element can be clicked or interacted with."""


def _iou(box_a: tuple[float, float, float, float], box_b: tuple[float, float, float, float]) -> float:
    """Compute Intersection over Union between two normalised bboxes."""
    x1 = max(box_a[0], box_b[0])
    y1 = max(box_a[1], box_b[1])
    x2 = min(box_a[2], box_b[2])
    y2 = min(box_a[3], box_b[3])

    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    if inter == 0:
        return 0.0

    area_a = (box_a[2] - box_a[0]) * (box_a[3] - box_a[1])
    area_b = (box_b[2] - box_b[0]) * (box_b[3] - box_b[1])
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def _overlap_ratio(inner: tuple[float, float, float, float], outer: tuple[float, float, float, float]) -> float:
    """Fraction of *inner* box that overlaps with *outer*."""
    x1 = max(inner[0], outer[0])
    y1 = max(inner[1], outer[1])
    x2 = min(inner[2], outer[2])
    y2 = min(inner[3], outer[3])

    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    area_inner = (inner[2] - inner[0]) * (inner[3] - inner[1])
    return inter / area_inner if area_inner > 0 else 0.0


def _position_descriptor(bbox: tuple[float, float, float, float]) -> str:
    """Generate a positional descriptor like 'top-left' from normalised bbox."""
    cx = (bbox[0] + bbox[2]) / 2
    cy = (bbox[1] + bbox[3]) / 2

    v = "top" if cy < 0.33 else ("bottom" if cy > 0.66 else "middle")
    h = "left" if cx < 0.33 else ("right" if cx > 0.66 else "center")

    if v == "middle" and h == "center":
        return "center"
    if v == "middle":
        return h
    if h == "center":
        return v
    return f"{v}-{h}"


class ElementClassifier:
    """Classify detected GUI elements using geometry + OCR overlap heuristics.

    Classification rules (applied in priority order):
      1. If OCR text overlaps detection box and text contains URL → "link"
      2. If OCR text overlaps and text contains email → "link"
      3. If aspect ratio > 4:1 (very wide) → "input"
      4. If area < 0.15% of screen and roughly square → "checkbox"
      5. If OCR text overlaps and box is small-medium + interactable → "button"
      6. If OCR text overlaps and box is large or text-heavy → "text"
      7. If no OCR overlap and small (< 2% of screen) → "icon"
      8. Default → "icon" (interactable)

    The label is taken from overlapping OCR text when available,
    otherwise a positional descriptor is used (e.g. "icon at top-right").
    """

    def __init__(self, ocr_overlap_threshold: float = 0.3) -> None:
        self._ocr_overlap_threshold = ocr_overlap_threshold

    def classify(
        self,
        detection: DetectionResult,
        ocr_boxes: list[OCRBox],
        screen_width: int,
        screen_height: int,
    ) -> ClassificationResult:
        """Classify a single detected element.

        Args:
            detection: The detected bounding box.
            ocr_boxes: All OCR text boxes from the image.
            screen_width: Image width in pixels.
            screen_height: Image height in pixels.

        Returns:
            ClassificationResult with element_type, label, interactable.
        """
        det_bbox = detection.bbox

        # Find overlapping OCR boxes.
        overlapping_text = self._find_overlapping_ocr(det_bbox, ocr_boxes)
        combined_text = " ".join(o.text for o in overlapping_text).strip()

        # Geometry metrics.
        box_w = det_bbox[2] - det_bbox[0]
        box_h = det_bbox[3] - det_bbox[1]
        area = box_w * box_h
        aspect_ratio = box_w / box_h if box_h > 0 else 1.0

        # Rule 1: Link (URL).
        if combined_text and _URL_RE.search(combined_text.lower()):
            return ClassificationResult("link", combined_text, True)

        # Rule 2: Link (email).
        if combined_text and _EMAIL_RE.search(combined_text):
            return ClassificationResult("link", combined_text, True)

        # Rule 3: Input field (very wide aspect ratio).
        if aspect_ratio > 4.0 and area > 0.002:
            label = combined_text if combined_text else f"input at {_position_descriptor(det_bbox)}"
            return ClassificationResult("input", label, True)

        # Rule 4: Checkbox (small, roughly square).
        if area < 0.0015 and 0.5 < aspect_ratio < 2.0:
            label = combined_text if combined_text else f"checkbox at {_position_descriptor(det_bbox)}"
            return ClassificationResult("checkbox", label, True)

        # Rule 5: Button (has text, small-medium, interactable shape).
        if combined_text and area < 0.05 and 0.5 < aspect_ratio < 6.0:
            return ClassificationResult("button", combined_text, True)

        # Rule 6: Text (has text, larger area or text-heavy).
        if combined_text and (area >= 0.05 or len(combined_text) > 50):
            return ClassificationResult("text", combined_text, False)

        # Rule 7: Small text with OCR.
        if combined_text:
            return ClassificationResult("text", combined_text, area < 0.02)

        # Rule 8: Icon (no OCR overlap, small).
        if area < 0.02:
            label = f"icon at {_position_descriptor(det_bbox)}"
            return ClassificationResult("icon", label, True)

        # Default: icon.
        label = f"element at {_position_descriptor(det_bbox)}"
        return ClassificationResult("icon", label, True)

    def classify_batch(
        self,
        detections: list[DetectionResult],
        ocr_boxes: list[OCRBox],
        screen_width: int,
        screen_height: int,
    ) -> list[ClassificationResult]:
        """Classify multiple detections in batch."""
        return [
            self.classify(det, ocr_boxes, screen_width, screen_height)
            for det in detections
        ]

    def _find_overlapping_ocr(
        self,
        det_bbox: tuple[float, float, float, float],
        ocr_boxes: list[OCRBox],
    ) -> list[OCRBox]:
        """Find OCR boxes that overlap significantly with the detection."""
        result = []
        for ocr in ocr_boxes:
            overlap = _overlap_ratio(ocr.bbox, det_bbox)
            if overlap >= self._ocr_overlap_threshold:
                result.append(ocr)
        return result
