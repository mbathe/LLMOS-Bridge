"""GUI element detection backend — abstract interface + UI-DETR-1 implementation.

UI-DETR-1 (racineai/UI-DETR-1) is a class-agnostic GUI element detector
based on the RF-DETR-M architecture, trained specifically on GUI screenshots.
It achieves 70.8% on WebClick — purpose-built for detecting buttons, inputs,
icons, and other interactive widgets on screen.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class DetectionResult:
    """A single detected bounding box from a GUI element detector."""

    bbox: tuple[float, float, float, float]
    """Normalised bounding box (x1, y1, x2, y2) in [0, 1]."""

    confidence: float
    """Detection confidence score in [0, 1]."""

    class_id: int | None = None
    """Class ID if the detector provides one (None for class-agnostic)."""


@dataclass
class DetectionOutput:
    """Full output from a detection model run."""

    detections: list[DetectionResult]
    image_width: int
    image_height: int
    model_id: str
    inference_time_ms: float


class BaseDetector(ABC):
    """Abstract interface for GUI element detection models.

    Implementations must provide:
      - ``load(device)`` — load model weights
      - ``detect(image, threshold)`` — run inference
      - ``unload()`` — free GPU/CPU memory
    """

    @abstractmethod
    def load(self, device: str = "auto") -> None:
        """Load model weights onto the specified device."""
        ...

    @abstractmethod
    def detect(
        self,
        image: Any,  # PIL.Image.Image
        confidence_threshold: float = 0.3,
    ) -> DetectionOutput:
        """Detect GUI elements in an image.

        Args:
            image: PIL Image (RGB).
            confidence_threshold: Minimum confidence to keep a detection.

        Returns:
            DetectionOutput with normalised bounding boxes.
        """
        ...

    @abstractmethod
    def unload(self) -> None:
        """Release model from GPU/CPU memory."""
        ...

    @property
    @abstractmethod
    def is_loaded(self) -> bool:
        """Whether the model weights are currently in memory."""
        ...

    @property
    @abstractmethod
    def vram_estimate_mb(self) -> int:
        """Estimated VRAM usage in megabytes when loaded."""
        ...


# ---------------------------------------------------------------------------
# Concrete: UI-DETR-1 (racineai/UI-DETR-1)
# ---------------------------------------------------------------------------


class UIDetrDetector(BaseDetector):
    """GUI element detector using UI-DETR-1 (RF-DETR-M architecture).

    Trained on GUI screenshots — class-agnostic detection of all
    interactable UI elements (buttons, inputs, icons, links, etc.).

    Requires: torch, transformers (or rfdetr), Pillow.
    VRAM: ~500MB at FP16.
    """

    VRAM_ESTIMATE_MB = 500

    def __init__(self, model_path: str | None = None, repo_id: str = "racineai/UI-DETR-1") -> None:
        self._model_path = model_path
        self._repo_id = repo_id
        self._model: Any = None
        self._processor: Any = None
        self._device: str = "cpu"

    def load(self, device: str = "auto") -> None:
        import torch  # noqa: PLC0415

        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self._device = device

        model_source = self._model_path or self._repo_id

        # Strategy 1: Try rfdetr package (recommended for UI-DETR-1).
        try:
            from rfdetr.detr import RFDETRMedium  # noqa: PLC0415
            import os as _os  # noqa: PLC0415

            # Resolve weights path: direct .pth file, or model.pth inside directory.
            if model_source.endswith(".pth"):
                weights_path = model_source
            else:
                weights_path = _os.path.join(model_source, "model.pth")

            if not _os.path.exists(weights_path):
                raise FileNotFoundError(f"RF-DETR weights not found at {weights_path}")

            self._model = RFDETRMedium(
                pretrain_weights=weights_path,
                resolution=1600,
            )
            self._processor = None
            return
        except ImportError:
            pass
        except FileNotFoundError:
            pass

        # Strategy 2: Try transformers AutoModel.
        try:
            from transformers import AutoImageProcessor, AutoModelForObjectDetection  # noqa: PLC0415

            self._processor = AutoImageProcessor.from_pretrained(model_source)
            self._model = AutoModelForObjectDetection.from_pretrained(model_source)
            self._model.to(device)
            self._model.eval()
            return
        except (ImportError, Exception):
            pass

        # Strategy 3: Fallback to YOLO if available (OmniParser's model).
        try:
            from ultralytics import YOLO  # noqa: PLC0415

            yolo_path = model_source
            if not yolo_path.endswith(".pt"):
                import os  # noqa: PLC0415
                # Try OmniParser's YOLO as last resort.
                omni_yolo = os.path.expanduser("~/.llmos/models/omniparser/icon_detect/model.pt")
                if os.path.exists(omni_yolo):
                    yolo_path = omni_yolo
                else:
                    raise FileNotFoundError(f"No YOLO model at {omni_yolo}")
            self._model = YOLO(yolo_path)
            self._processor = "yolo"
            return
        except (ImportError, Exception) as exc:
            raise RuntimeError(
                f"Cannot load UI element detector. Install one of: "
                f"rfdetr, transformers, or ultralytics. Error: {exc}"
            ) from exc

    def detect(
        self,
        image: Any,
        confidence_threshold: float = 0.3,
    ) -> DetectionOutput:
        if self._model is None:
            raise RuntimeError("Detector not loaded. Call load() first.")

        t0 = time.perf_counter()
        w, h = image.size

        if self._processor == "yolo":
            return self._detect_yolo(image, confidence_threshold, w, h, t0)
        elif self._processor is not None:
            return self._detect_transformers(image, confidence_threshold, w, h, t0)
        else:
            return self._detect_rfdetr(image, confidence_threshold, w, h, t0)

    def _detect_rfdetr(
        self, image: Any, threshold: float, w: int, h: int, t0: float
    ) -> DetectionOutput:
        import numpy as np  # noqa: PLC0415

        results = self._model.predict(image, threshold=threshold)

        detections = []
        if hasattr(results, "xyxy"):
            # rfdetr returns numpy arrays directly (not torch tensors).
            raw_boxes = results.xyxy
            boxes = raw_boxes.cpu().numpy() if hasattr(raw_boxes, "cpu") else np.asarray(raw_boxes)
            raw_scores = results.confidence if hasattr(results, "confidence") else None
            if raw_scores is not None:
                scores = raw_scores.cpu().numpy() if hasattr(raw_scores, "cpu") else np.asarray(raw_scores)
            else:
                scores = np.ones(len(boxes))
            for box, score in zip(boxes, scores):
                if score >= threshold:
                    detections.append(DetectionResult(
                        bbox=(float(box[0] / w), float(box[1] / h), float(box[2] / w), float(box[3] / h)),
                        confidence=float(score),
                    ))

        elapsed = (time.perf_counter() - t0) * 1000
        return DetectionOutput(
            detections=detections, image_width=w, image_height=h,
            model_id="ui-detr-1-rfdetr", inference_time_ms=elapsed,
        )

    def _detect_transformers(
        self, image: Any, threshold: float, w: int, h: int, t0: float
    ) -> DetectionOutput:
        import torch  # noqa: PLC0415

        inputs = self._processor(images=image, return_tensors="pt").to(self._device)
        with torch.inference_mode():
            outputs = self._model(**inputs)

        target_sizes = torch.tensor([[h, w]], device=self._device)
        results = self._processor.post_process_object_detection(
            outputs, threshold=threshold, target_sizes=target_sizes
        )[0]

        detections = []
        for score, box in zip(results["scores"], results["boxes"]):
            s = float(score)
            if s >= threshold:
                b = box.cpu().tolist()
                detections.append(DetectionResult(
                    bbox=(b[0] / w, b[1] / h, b[2] / w, b[3] / h),
                    confidence=s,
                ))

        elapsed = (time.perf_counter() - t0) * 1000
        return DetectionOutput(
            detections=detections, image_width=w, image_height=h,
            model_id="ui-detr-1-transformers", inference_time_ms=elapsed,
        )

    def _detect_yolo(
        self, image: Any, threshold: float, w: int, h: int, t0: float
    ) -> DetectionOutput:
        results = self._model(image, conf=threshold, verbose=False)
        detections = []
        for r in results:
            boxes = r.boxes
            if boxes is not None:
                for i in range(len(boxes)):
                    xyxy = boxes.xyxy[i].cpu().tolist()
                    conf = float(boxes.conf[i])
                    if conf >= threshold:
                        detections.append(DetectionResult(
                            bbox=(xyxy[0] / w, xyxy[1] / h, xyxy[2] / w, xyxy[3] / h),
                            confidence=conf,
                        ))

        elapsed = (time.perf_counter() - t0) * 1000
        return DetectionOutput(
            detections=detections, image_width=w, image_height=h,
            model_id="yolo-v8-fallback", inference_time_ms=elapsed,
        )

    def unload(self) -> None:
        if self._model is not None:
            del self._model
            del self._processor
            self._model = None
            self._processor = None
            try:
                import torch  # noqa: PLC0415
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except ImportError:
                pass

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    @property
    def vram_estimate_mb(self) -> int:
        return self.VRAM_ESTIMATE_MB
