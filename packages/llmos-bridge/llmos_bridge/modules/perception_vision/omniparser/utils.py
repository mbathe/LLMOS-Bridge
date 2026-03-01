"""OmniParser utility functions — ML pipeline.

Contains model loading, OCR, YOLO prediction, icon captioning,
and the main ``get_som_labeled_img`` pipeline function.

Originally from Microsoft OmniParser v2 (MIT License).
Cleaned and integrated into LLMOS Bridge.
"""

from __future__ import annotations

import base64
import io
import logging
import time
from typing import Dict, List, Tuple, Union

import cv2
import numpy as np
import torch
from PIL import Image
from torchvision.ops import box_convert
from torchvision.transforms import ToPILImage

import supervision as sv

from .box_annotator import BoxAnnotator

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# PaddleOCR — optional, imported lazily
# ---------------------------------------------------------------------------
try:
    from paddleocr import PaddleOCR

    _paddle_ocr = PaddleOCR(
        lang="en",
        use_angle_cls=False,
        use_gpu=False,
        show_log=False,
        max_batch_size=1024,
        use_dilation=True,
        det_db_score_mode="slow",
        rec_batch_num=1024,
    )
except ImportError:
    PaddleOCR = None  # type: ignore[assignment,misc]
    _paddle_ocr = None

# ---------------------------------------------------------------------------
# EasyOCR — lazy singleton to avoid loading at import time
# ---------------------------------------------------------------------------
_easyocr_reader = None


def _get_easyocr_reader():
    """Return a lazily-initialised EasyOCR reader."""
    global _easyocr_reader
    if _easyocr_reader is None:
        import easyocr

        _easyocr_reader = easyocr.Reader(["en"])
    return _easyocr_reader


# ===================================================================
# Model loading
# ===================================================================


def get_caption_model_processor(
    model_name: str,
    model_name_or_path: str = "Salesforce/blip2-opt-2.7b",
    device: str | None = None,
) -> Dict:
    """Load a caption model (BLIP-2 or Florence-2) and its processor."""
    if not device:
        device = "cuda" if torch.cuda.is_available() else "cpu"

    if model_name == "blip2":
        from transformers import Blip2ForConditionalGeneration, Blip2Processor

        processor = Blip2Processor.from_pretrained("Salesforce/blip2-opt-2.7b")
        dtype = torch.float32 if device == "cpu" else torch.float16
        model = Blip2ForConditionalGeneration.from_pretrained(
            model_name_or_path, device_map=None, torch_dtype=dtype
        )
    elif model_name == "florence2":
        from transformers import AutoModelForCausalLM, AutoProcessor

        processor = AutoProcessor.from_pretrained(
            "microsoft/Florence-2-base", trust_remote_code=True
        )
        dtype = torch.float32 if device == "cpu" else torch.float16
        model = AutoModelForCausalLM.from_pretrained(
            model_name_or_path, torch_dtype=dtype, trust_remote_code=True
        )
    else:
        raise ValueError(f"Unknown caption model: {model_name}")

    return {"model": model.to(device), "processor": processor}


def get_yolo_model(model_path: str):
    """Load a YOLO model from *model_path*."""
    from ultralytics import YOLO

    return YOLO(model_path)


# ===================================================================
# Icon captioning
# ===================================================================


@torch.inference_mode()
def get_parsed_content_icon(
    filtered_boxes,
    starting_idx,
    image_source,
    caption_model_processor,
    prompt=None,
    batch_size=128,
):
    """Caption detected icon regions using the caption model."""
    to_pil = ToPILImage()
    non_ocr_boxes = filtered_boxes[starting_idx:] if starting_idx else filtered_boxes
    cropped_pil_images: list[Image.Image] = []

    for coord in non_ocr_boxes:
        try:
            xmin = int(coord[0] * image_source.shape[1])
            xmax = int(coord[2] * image_source.shape[1])
            ymin = int(coord[1] * image_source.shape[0])
            ymax = int(coord[3] * image_source.shape[0])
            cropped = image_source[ymin:ymax, xmin:xmax, :]
            cropped = cv2.resize(cropped, (64, 64))
            cropped_pil_images.append(to_pil(cropped))
        except Exception:
            continue

    model = caption_model_processor["model"]
    processor = caption_model_processor["processor"]
    if not prompt:
        if "florence" in model.config.name_or_path:
            prompt = "<CAPTION>"
        else:
            prompt = "The image shows"

    generated_texts: list[str] = []
    device = model.device

    for i in range(0, len(cropped_pil_images), batch_size):
        batch = cropped_pil_images[i : i + batch_size]
        if model.device.type == "cuda":
            inputs = processor(
                images=batch,
                text=[prompt] * len(batch),
                return_tensors="pt",
                do_resize=False,
            ).to(device=device, dtype=torch.float16)
        else:
            inputs = processor(
                images=batch,
                text=[prompt] * len(batch),
                return_tensors="pt",
            ).to(device=device)

        if "florence" in model.config.name_or_path:
            generated_ids = model.generate(
                input_ids=inputs["input_ids"],
                pixel_values=inputs["pixel_values"],
                max_new_tokens=20,
                num_beams=1,
                do_sample=False,
            )
        else:
            generated_ids = model.generate(
                **inputs,
                max_length=100,
                num_beams=5,
                no_repeat_ngram_size=2,
                early_stopping=True,
                num_return_sequences=1,
            )
        decoded = processor.batch_decode(generated_ids, skip_special_tokens=True)
        generated_texts.extend(t.strip() for t in decoded)

    return generated_texts


def get_parsed_content_icon_phi3v(
    filtered_boxes, ocr_bbox, image_source, caption_model_processor
):
    """Caption icons using the Phi-3-Vision model."""
    to_pil = ToPILImage()
    non_ocr_boxes = filtered_boxes[len(ocr_bbox) :] if ocr_bbox else filtered_boxes
    cropped_pil_images: list[Image.Image] = []

    for coord in non_ocr_boxes:
        xmin = int(coord[0] * image_source.shape[1])
        xmax = int(coord[2] * image_source.shape[1])
        ymin = int(coord[1] * image_source.shape[0])
        ymax = int(coord[3] * image_source.shape[0])
        cropped = image_source[ymin:ymax, xmin:xmax, :]
        cropped_pil_images.append(to_pil(cropped))

    model = caption_model_processor["model"]
    processor = caption_model_processor["processor"]
    device = model.device

    messages = [
        {
            "role": "user",
            "content": "<|image_1|>\ndescribe the icon in one sentence",
        }
    ]
    prompt = processor.tokenizer.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True
    )

    batch_size = 5
    generated_texts: list[str] = []

    for i in range(0, len(cropped_pil_images), batch_size):
        images = cropped_pil_images[i : i + batch_size]
        image_inputs = [processor.image_processor(x, return_tensors="pt") for x in images]
        inputs: dict = {
            "input_ids": [],
            "attention_mask": [],
            "pixel_values": [],
            "image_sizes": [],
        }
        texts = [prompt] * len(images)
        for j, txt in enumerate(texts):
            inp = processor._convert_images_texts_to_inputs(
                image_inputs[j], txt, return_tensors="pt"
            )
            inputs["input_ids"].append(inp["input_ids"])
            inputs["attention_mask"].append(inp["attention_mask"])
            inputs["pixel_values"].append(inp["pixel_values"])
            inputs["image_sizes"].append(inp["image_sizes"])

        max_len = max(x.shape[1] for x in inputs["input_ids"])
        for j, v in enumerate(inputs["input_ids"]):
            pad_len = max_len - v.shape[1]
            inputs["input_ids"][j] = torch.cat(
                [
                    processor.tokenizer.pad_token_id
                    * torch.ones(1, pad_len, dtype=torch.long),
                    v,
                ],
                dim=1,
            )
            inputs["attention_mask"][j] = torch.cat(
                [
                    torch.zeros(1, pad_len, dtype=torch.long),
                    inputs["attention_mask"][j],
                ],
                dim=1,
            )
        inputs_cat = {k: torch.concatenate(v).to(device) for k, v in inputs.items()}

        generate_ids = model.generate(
            **inputs_cat,
            eos_token_id=processor.tokenizer.eos_token_id,
            max_new_tokens=25,
            temperature=0.01,
            do_sample=False,
        )
        generate_ids = generate_ids[:, inputs_cat["input_ids"].shape[1] :]
        response = processor.batch_decode(
            generate_ids, skip_special_tokens=True, clean_up_tokenization_spaces=False
        )
        generated_texts.extend(res.strip("\n").strip() for res in response)

    return generated_texts


# ===================================================================
# Overlap removal
# ===================================================================


def remove_overlap_new(
    boxes: list[dict], iou_threshold: float, ocr_bbox: list[dict] | None = None
) -> list[dict]:
    """Remove overlapping boxes, prioritising OCR labels over YOLO detections.

    Args:
        boxes: list of ``{'type':'icon', 'bbox':[x1,y1,x2,y2], ...}``
        iou_threshold: IoU threshold for overlap
        ocr_bbox: list of ``{'type':'text', 'bbox':[x1,y1,x2,y2], 'content':str, ...}``
    """
    assert ocr_bbox is None or isinstance(ocr_bbox, list)

    def _box_area(box):
        return (box[2] - box[0]) * (box[3] - box[1])

    def _intersection_area(box1, box2):
        x1 = max(box1[0], box2[0])
        y1 = max(box1[1], box2[1])
        x2 = min(box1[2], box2[2])
        y2 = min(box1[3], box2[3])
        return max(0, x2 - x1) * max(0, y2 - y1)

    def _iou(box1, box2):
        intersection = _intersection_area(box1, box2)
        union = _box_area(box1) + _box_area(box2) - intersection + 1e-6
        area1, area2 = _box_area(box1), _box_area(box2)
        ratio1 = intersection / area1 if area1 > 0 else 0
        ratio2 = intersection / area2 if area2 > 0 else 0
        return max(intersection / union, ratio1, ratio2)

    def _is_inside(box1, box2):
        intersection = _intersection_area(box1, box2)
        ratio1 = intersection / _box_area(box1)
        return ratio1 > 0.80

    filtered_boxes: list[dict] = []
    if ocr_bbox:
        filtered_boxes.extend(ocr_bbox)

    for i, box1_elem in enumerate(boxes):
        box1 = box1_elem["bbox"]
        is_valid = True
        for j, box2_elem in enumerate(boxes):
            box2 = box2_elem["bbox"]
            if i != j and _iou(box1, box2) > iou_threshold and _box_area(box1) > _box_area(box2):
                is_valid = False
                break
        if is_valid:
            if ocr_bbox:
                box_added = False
                ocr_labels = ""
                for box3_elem in ocr_bbox:
                    if not box_added:
                        box3 = box3_elem["bbox"]
                        if _is_inside(box3, box1):
                            try:
                                ocr_labels += box3_elem["content"] + " "
                                filtered_boxes.remove(box3_elem)
                            except Exception:
                                continue
                        elif _is_inside(box1, box3):
                            box_added = True
                            break
                if not box_added:
                    if ocr_labels:
                        filtered_boxes.append({
                            "type": "icon",
                            "bbox": box1_elem["bbox"],
                            "interactivity": True,
                            "content": ocr_labels,
                            "source": "box_yolo_content_ocr",
                        })
                    else:
                        filtered_boxes.append({
                            "type": "icon",
                            "bbox": box1_elem["bbox"],
                            "interactivity": True,
                            "content": None,
                            "source": "box_yolo_content_yolo",
                        })
            else:
                filtered_boxes.append(box1)
    return filtered_boxes


# ===================================================================
# YOLO prediction
# ===================================================================


def predict_yolo(model, image, box_threshold, imgsz, scale_img, iou_threshold=0.7):
    """Run YOLO prediction on *image*."""
    if scale_img:
        result = model.predict(
            source=image, conf=box_threshold, imgsz=imgsz, iou=iou_threshold
        )
    else:
        result = model.predict(
            source=image, conf=box_threshold, iou=iou_threshold
        )
    boxes = result[0].boxes.xyxy
    conf = result[0].boxes.conf
    phrases = [str(i) for i in range(len(boxes))]
    return boxes, conf, phrases


# ===================================================================
# Annotation helpers
# ===================================================================


def int_box_area(box, w, h):
    """Compute pixel-area of a normalised box."""
    x1, y1, x2, y2 = box
    int_box = [int(x1 * w), int(y1 * h), int(x2 * w), int(y2 * h)]
    return (int_box[2] - int_box[0]) * (int_box[3] - int_box[1])


def get_xywh(input):
    x, y, w, h = input[0][0], input[0][1], input[2][0] - input[0][0], input[2][1] - input[0][1]
    return int(x), int(y), int(w), int(h)


def get_xyxy(input):
    x, y, xp, yp = input[0][0], input[0][1], input[2][0], input[2][1]
    return int(x), int(y), int(xp), int(yp)


def annotate(
    image_source: np.ndarray,
    boxes: torch.Tensor,
    logits: torch.Tensor,
    phrases: list,
    text_scale: float,
    text_padding: int = 5,
    text_thickness: int = 2,
    thickness: int = 3,
) -> Tuple[np.ndarray, dict]:
    """Annotate *image_source* with bounding boxes and numeric labels."""
    h, w, _ = image_source.shape
    boxes = boxes * torch.Tensor([w, h, w, h])
    xyxy = box_convert(boxes=boxes, in_fmt="cxcywh", out_fmt="xyxy").numpy()
    xywh = box_convert(boxes=boxes, in_fmt="cxcywh", out_fmt="xywh").numpy()
    detections = sv.Detections(xyxy=xyxy)

    labels = [f"{i}" for i in range(boxes.shape[0])]

    box_annotator = BoxAnnotator(
        text_scale=text_scale,
        text_padding=text_padding,
        text_thickness=text_thickness,
        thickness=thickness,
    )
    annotated_frame = image_source.copy()
    annotated_frame = box_annotator.annotate(
        scene=annotated_frame, detections=detections, labels=labels, image_size=(w, h)
    )

    label_coordinates = {f"{phrase}": v for phrase, v in zip(phrases, xywh)}
    return annotated_frame, label_coordinates


# ===================================================================
# OCR
# ===================================================================


def check_ocr_box(
    image_source: Union[str, Image.Image],
    display_img: bool = True,
    output_bb_format: str = "xywh",
    goal_filtering=None,
    easyocr_args: dict | None = None,
    use_paddleocr: bool = False,
):
    """Run OCR on *image_source* and return ``((texts, bboxes), goal_filtering)``."""
    if isinstance(image_source, str):
        image_source = Image.open(image_source)
    if image_source.mode == "RGBA":
        image_source = image_source.convert("RGB")

    image_np = np.array(image_source)
    w, h = image_source.size

    if use_paddleocr:
        if _paddle_ocr is None:
            raise ImportError(
                "paddleocr is required when use_paddleocr=True. "
                "Install with: pip install paddleocr paddlepaddle"
            )
        text_threshold = easyocr_args.get("text_threshold", 0.5) if easyocr_args else 0.5
        result = _paddle_ocr.ocr(image_np, cls=False)[0]
        coord = [item[0] for item in result if item[1][1] > text_threshold]
        text = [item[1][0] for item in result if item[1][1] > text_threshold]
    else:
        reader = _get_easyocr_reader()
        if easyocr_args is None:
            easyocr_args = {}
        result = reader.readtext(image_np, **easyocr_args)
        coord = [item[0] for item in result]
        text = [item[1] for item in result]

    if display_img:
        from matplotlib import pyplot as plt

        opencv_img = cv2.cvtColor(image_np, cv2.COLOR_RGB2BGR)
        bb = []
        for item in coord:
            x, y, a, b = get_xywh(item)
            bb.append((x, y, a, b))
            cv2.rectangle(opencv_img, (x, y), (x + a, y + b), (0, 255, 0), 2)
        plt.imshow(cv2.cvtColor(opencv_img, cv2.COLOR_BGR2RGB))
    else:
        if output_bb_format == "xywh":
            bb = [get_xywh(item) for item in coord]
        elif output_bb_format == "xyxy":
            bb = [get_xyxy(item) for item in coord]

    return (text, bb), goal_filtering


# ===================================================================
# Main pipeline — SoM labeled image
# ===================================================================


def get_som_labeled_img(
    image_source: Union[str, Image.Image],
    model=None,
    BOX_TRESHOLD: float = 0.01,
    output_coord_in_ratio: bool = False,
    ocr_bbox=None,
    text_scale: float = 0.4,
    text_padding: int = 5,
    draw_bbox_config: dict | None = None,
    caption_model_processor=None,
    ocr_text: list | None = None,
    use_local_semantics: bool = True,
    iou_threshold: float = 0.9,
    prompt=None,
    scale_img: bool = False,
    imgsz=None,
    batch_size: int = 128,
):
    """Generate a Set-of-Marks labeled image with element annotations.

    Returns:
        (encoded_image_b64, label_coordinates, filtered_boxes_elem)
    """
    if ocr_text is None:
        ocr_text = []

    if isinstance(image_source, str):
        image_source = Image.open(image_source)
    image_source = image_source.convert("RGB")
    w, h = image_source.size
    if not imgsz:
        imgsz = (h, w)

    # YOLO detection
    xyxy, logits, phrases = predict_yolo(
        model=model,
        image=image_source,
        box_threshold=BOX_TRESHOLD,
        imgsz=imgsz,
        scale_img=scale_img,
        iou_threshold=0.1,
    )
    xyxy = xyxy / torch.Tensor([w, h, w, h]).to(xyxy.device)
    image_source = np.asarray(image_source)
    phrases = [str(i) for i in range(len(phrases))]

    # Combine OCR + YOLO boxes
    if ocr_bbox:
        ocr_bbox = torch.tensor(ocr_bbox) / torch.Tensor([w, h, w, h])
        ocr_bbox = ocr_bbox.tolist()
    else:
        ocr_bbox = None

    ocr_bbox_elem = [
        {
            "type": "text",
            "bbox": box,
            "interactivity": False,
            "content": txt,
            "source": "box_ocr_content_ocr",
        }
        for box, txt in zip(ocr_bbox or [], ocr_text)
        if int_box_area(box, w, h) > 0
    ]
    xyxy_elem = [
        {"type": "icon", "bbox": box, "interactivity": True, "content": None}
        for box in xyxy.tolist()
        if int_box_area(box, w, h) > 0
    ]
    filtered_boxes = remove_overlap_new(
        boxes=xyxy_elem, iou_threshold=iou_threshold, ocr_bbox=ocr_bbox_elem
    )

    # Sort: content-bearing elements first, None-content at end
    filtered_boxes_elem = sorted(filtered_boxes, key=lambda x: x["content"] is None)
    starting_idx = next(
        (i for i, box in enumerate(filtered_boxes_elem) if box["content"] is None), -1
    )
    filtered_boxes_t = torch.tensor([box["bbox"] for box in filtered_boxes_elem])
    log.debug(
        "filtered_boxes=%d starting_idx=%d", len(filtered_boxes_t), starting_idx
    )

    # Icon captioning (local semantics)
    t0 = time.time()
    if use_local_semantics:
        caption_model = caption_model_processor["model"]
        if "phi3_v" in caption_model.config.model_type:
            parsed_content_icon = get_parsed_content_icon_phi3v(
                filtered_boxes_t, ocr_bbox, image_source, caption_model_processor
            )
        else:
            parsed_content_icon = get_parsed_content_icon(
                filtered_boxes_t,
                starting_idx,
                image_source,
                caption_model_processor,
                prompt=prompt,
                batch_size=batch_size,
            )
        ocr_text = [f"Text Box ID {i}: {txt}" for i, txt in enumerate(ocr_text)]
        icon_start = len(ocr_text)
        parsed_content_icon_ls = []
        for box in filtered_boxes_elem:
            if box["content"] is None:
                box["content"] = parsed_content_icon.pop(0)
        for i, txt in enumerate(parsed_content_icon):
            parsed_content_icon_ls.append(f"Icon Box ID {i + icon_start}: {txt}")
        parsed_content_merged = ocr_text + parsed_content_icon_ls
    else:
        ocr_text = [f"Text Box ID {i}: {txt}" for i, txt in enumerate(ocr_text)]
        parsed_content_merged = ocr_text

    log.debug("icon captioning took %.2fs", time.time() - t0)

    filtered_boxes_t = box_convert(
        boxes=filtered_boxes_t, in_fmt="xyxy", out_fmt="cxcywh"
    )
    phrases = list(range(len(filtered_boxes_t)))

    # Draw annotated image
    if draw_bbox_config:
        annotated_frame, label_coordinates = annotate(
            image_source=image_source,
            boxes=filtered_boxes_t,
            logits=logits,
            phrases=phrases,
            **draw_bbox_config,
        )
    else:
        annotated_frame, label_coordinates = annotate(
            image_source=image_source,
            boxes=filtered_boxes_t,
            logits=logits,
            phrases=phrases,
            text_scale=text_scale,
            text_padding=text_padding,
        )

    # Encode as base64 PNG
    pil_img = Image.fromarray(annotated_frame)
    buffered = io.BytesIO()
    pil_img.save(buffered, format="PNG")
    encoded_image = base64.b64encode(buffered.getvalue()).decode("ascii")

    if output_coord_in_ratio:
        label_coordinates = {
            k: [v[0] / w, v[1] / h, v[2] / w, v[3] / h]
            for k, v in label_coordinates.items()
        }
        assert w == annotated_frame.shape[1] and h == annotated_frame.shape[0]

    return encoded_image, label_coordinates, filtered_boxes_elem
