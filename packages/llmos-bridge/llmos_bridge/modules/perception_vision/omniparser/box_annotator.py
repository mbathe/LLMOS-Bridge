"""Box annotation utilities for OmniParser.

Draws bounding boxes with labels on annotated screenshots.
Originally from Microsoft OmniParser v2 (MIT License).
"""

from __future__ import annotations

from typing import List, Optional, Tuple, Union

import cv2
import numpy as np
from supervision.detection.core import Detections
from supervision.draw.color import Color, ColorPalette


class BoxAnnotator:
    """Draw bounding boxes on an image using detections."""

    def __init__(
        self,
        color: Union[Color, ColorPalette] = ColorPalette.DEFAULT,
        thickness: int = 3,
        text_color: Color = Color.BLACK,
        text_scale: float = 0.5,
        text_thickness: int = 2,
        text_padding: int = 10,
        avoid_overlap: bool = True,
    ):
        self.color: Union[Color, ColorPalette] = color
        self.thickness: int = thickness
        self.text_color: Color = text_color
        self.text_scale: float = text_scale
        self.text_thickness: int = text_thickness
        self.text_padding: int = text_padding
        self.avoid_overlap: bool = avoid_overlap

    def annotate(
        self,
        scene: np.ndarray,
        detections: Detections,
        labels: Optional[List[str]] = None,
        skip_label: bool = False,
        image_size: Optional[Tuple[int, int]] = None,
    ) -> np.ndarray:
        """Draw bounding boxes on the frame using the detections provided."""
        font = cv2.FONT_HERSHEY_SIMPLEX
        for i in range(len(detections)):
            x1, y1, x2, y2 = detections.xyxy[i].astype(int)
            class_id = (
                detections.class_id[i] if detections.class_id is not None else None
            )
            idx = class_id if class_id is not None else i
            color = (
                self.color.by_idx(idx)
                if isinstance(self.color, ColorPalette)
                else self.color
            )
            cv2.rectangle(
                img=scene,
                pt1=(x1, y1),
                pt2=(x2, y2),
                color=color.as_bgr(),
                thickness=self.thickness,
            )
            if skip_label:
                continue

            text = (
                f"{class_id}"
                if (labels is None or len(detections) != len(labels))
                else labels[i]
            )

            text_width, text_height = cv2.getTextSize(
                text=text,
                fontFace=font,
                fontScale=self.text_scale,
                thickness=self.text_thickness,
            )[0]

            if not self.avoid_overlap:
                text_x = x1 + self.text_padding
                text_y = y1 - self.text_padding
                text_background_x1 = x1
                text_background_y1 = y1 - 2 * self.text_padding - text_height
                text_background_x2 = x1 + 2 * self.text_padding + text_width
                text_background_y2 = y1
            else:
                text_x, text_y, text_background_x1, text_background_y1, text_background_x2, text_background_y2 = get_optimal_label_pos(
                    self.text_padding, text_width, text_height, x1, y1, x2, y2, detections, image_size
                )

            cv2.rectangle(
                img=scene,
                pt1=(text_background_x1, text_background_y1),
                pt2=(text_background_x2, text_background_y2),
                color=color.as_bgr(),
                thickness=cv2.FILLED,
            )
            box_color = color.as_rgb()
            luminance = 0.299 * box_color[0] + 0.587 * box_color[1] + 0.114 * box_color[2]
            text_color = (0, 0, 0) if luminance > 160 else (255, 255, 255)
            cv2.putText(
                img=scene,
                text=text,
                org=(text_x, text_y),
                fontFace=font,
                fontScale=self.text_scale,
                color=text_color,
                thickness=self.text_thickness,
                lineType=cv2.LINE_AA,
            )
        return scene


def box_area(box):
    return (box[2] - box[0]) * (box[3] - box[1])


def intersection_area(box1, box2):
    x1 = max(box1[0], box2[0])
    y1 = max(box1[1], box2[1])
    x2 = min(box1[2], box2[2])
    y2 = min(box1[3], box2[3])
    return max(0, x2 - x1) * max(0, y2 - y1)


def IoU(box1, box2, return_max=True):
    intersection = intersection_area(box1, box2)
    union = box_area(box1) + box_area(box2) - intersection
    if box_area(box1) > 0 and box_area(box2) > 0:
        ratio1 = intersection / box_area(box1)
        ratio2 = intersection / box_area(box2)
    else:
        ratio1, ratio2 = 0, 0
    if return_max:
        return max(intersection / union, ratio1, ratio2)
    else:
        return intersection / union


def get_optimal_label_pos(text_padding, text_width, text_height, x1, y1, x2, y2, detections, image_size):
    """Find the best label position that avoids overlapping other bounding boxes."""

    def get_is_overlap(detections, tbx1, tby1, tbx2, tby2, image_size):
        for i in range(len(detections)):
            detection = detections.xyxy[i].astype(int)
            if IoU([tbx1, tby1, tbx2, tby2], detection) > 0.3:
                return True
        if tbx1 < 0 or tbx2 > image_size[0] or tby1 < 0 or tby2 > image_size[1]:
            return True
        return False

    # Try top-left
    text_x = x1 + text_padding
    text_y = y1 - text_padding
    tbx1 = x1
    tby1 = y1 - 2 * text_padding - text_height
    tbx2 = x1 + 2 * text_padding + text_width
    tby2 = y1
    if not get_is_overlap(detections, tbx1, tby1, tbx2, tby2, image_size):
        return text_x, text_y, tbx1, tby1, tbx2, tby2

    # Try outer-left
    text_x = x1 - text_padding - text_width
    text_y = y1 + text_padding + text_height
    tbx1 = x1 - 2 * text_padding - text_width
    tby1 = y1
    tbx2 = x1
    tby2 = y1 + 2 * text_padding + text_height
    if not get_is_overlap(detections, tbx1, tby1, tbx2, tby2, image_size):
        return text_x, text_y, tbx1, tby1, tbx2, tby2

    # Try outer-right
    text_x = x2 + text_padding
    text_y = y1 + text_padding + text_height
    tbx1 = x2
    tby1 = y1
    tbx2 = x2 + 2 * text_padding + text_width
    tby2 = y1 + 2 * text_padding + text_height
    if not get_is_overlap(detections, tbx1, tby1, tbx2, tby2, image_size):
        return text_x, text_y, tbx1, tby1, tbx2, tby2

    # Try top-right (fallback)
    text_x = x2 - text_padding - text_width
    text_y = y1 - text_padding
    tbx1 = x2 - 2 * text_padding - text_width
    tby1 = y1 - 2 * text_padding - text_height
    tbx2 = x2
    tby2 = y1
    return text_x, text_y, tbx1, tby1, tbx2, tby2
