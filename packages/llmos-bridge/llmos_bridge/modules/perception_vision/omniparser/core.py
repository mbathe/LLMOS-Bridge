"""OmniParser core — main entry point.

Wraps the ML pipeline behind a simple ``parse(image_base64)`` API.
Originally from Microsoft OmniParser v2 (MIT License).
"""

from __future__ import annotations

import base64
import io
import logging
from typing import Dict, Tuple

import torch
from PIL import Image

from .utils import (
    check_ocr_box,
    get_caption_model_processor,
    get_som_labeled_img,
    get_yolo_model,
)

log = logging.getLogger(__name__)


class Omniparser:
    """Screen parser using YOLO + Florence-2 + EasyOCR."""

    def __init__(self, config: Dict) -> None:
        self.config = config
        device = "cuda" if torch.cuda.is_available() else "cpu"

        self.som_model = get_yolo_model(model_path=config["som_model_path"])
        self.caption_model_processor = get_caption_model_processor(
            model_name=config["caption_model_name"],
            model_name_or_path=config["caption_model_path"],
            device=device,
        )
        log.info("OmniParser initialised (device=%s)", device)

    def parse(self, image_base64: str) -> Tuple:
        """Parse a base64-encoded screenshot and return (labeled_img_b64, elements)."""
        image_bytes = base64.b64decode(image_base64)
        image = Image.open(io.BytesIO(image_bytes))
        log.debug("Parsing image %dx%d", image.size[0], image.size[1])

        box_overlay_ratio = max(image.size) / 3200
        draw_bbox_config = {
            "text_scale": 0.8 * box_overlay_ratio,
            "text_thickness": max(int(2 * box_overlay_ratio), 1),
            "text_padding": max(int(3 * box_overlay_ratio), 1),
            "thickness": max(int(3 * box_overlay_ratio), 1),
        }

        (text, ocr_bbox), _ = check_ocr_box(
            image,
            display_img=False,
            output_bb_format="xyxy",
            easyocr_args={"text_threshold": 0.8},
            use_paddleocr=False,
        )
        labeled_img, label_coordinates, parsed_content_list = get_som_labeled_img(
            image,
            self.som_model,
            BOX_TRESHOLD=self.config["BOX_TRESHOLD"],
            output_coord_in_ratio=True,
            ocr_bbox=ocr_bbox,
            draw_bbox_config=draw_bbox_config,
            caption_model_processor=self.caption_model_processor,
            ocr_text=text,
            use_local_semantics=True,
            iou_threshold=0.7,
            scale_img=False,
            batch_size=128,
        )

        return labeled_img, parsed_content_list
