"""OCR backend — abstract interface + PP-OCRv5 / EasyOCR implementations.

PP-OCRv5 (PaddlePaddle) is the primary OCR engine — 106 languages, SOTA
on screen text, CPU-only.  EasyOCR serves as a fallback when paddleocr
is not installed (it is already a dependency from OmniParser).
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class OCRBox:
    """A single OCR text detection with position."""

    text: str
    """Recognised text content."""

    bbox: tuple[float, float, float, float]
    """Normalised bounding box (x1, y1, x2, y2) in [0, 1]."""

    confidence: float
    """Recognition confidence score in [0, 1]."""

    language: str = "en"
    """Detected language code."""


@dataclass
class OCROutput:
    """Full output from an OCR engine run."""

    boxes: list[OCRBox]
    full_text: str
    """All text concatenated top-to-bottom, left-to-right."""

    inference_time_ms: float
    engine_id: str


class BaseOCR(ABC):
    """Abstract interface for OCR engines."""

    @abstractmethod
    def load(self) -> None:
        """Initialise the OCR engine (download models if needed)."""
        ...

    @abstractmethod
    def recognize(self, image: Any) -> OCROutput:
        """Run OCR on an image.

        Args:
            image: PIL Image (RGB).

        Returns:
            OCROutput with text boxes and full concatenated text.
        """
        ...

    @abstractmethod
    def unload(self) -> None:
        """Release engine resources."""
        ...

    @property
    @abstractmethod
    def is_loaded(self) -> bool:
        """Whether the engine is initialised."""
        ...


# ---------------------------------------------------------------------------
# Concrete: PP-OCRv5 (PaddlePaddle)
# ---------------------------------------------------------------------------


class PPOCRv5Engine(BaseOCR):
    """OCR engine using PaddlePaddle PP-OCRv5.

    CPU-only, 106 languages, SOTA accuracy on screen/document text.

    Requires: paddleocr, paddlepaddle.
    """

    def __init__(self, lang: str = "en", use_angle_cls: bool = True) -> None:
        self._lang = lang
        self._use_angle_cls = use_angle_cls
        self._engine: Any = None

    def load(self) -> None:
        from paddleocr import PaddleOCR  # noqa: PLC0415

        # PaddleOCR v3+ removed use_gpu, use_angle_cls, show_log params.
        # Try minimal new API first, then fall back to legacy.
        try:
            self._engine = PaddleOCR(lang=self._lang)
        except (TypeError, ValueError):
            try:
                self._engine = PaddleOCR(
                    use_angle_cls=self._use_angle_cls,
                    lang=self._lang,
                    use_gpu=False,
                )
            except (TypeError, ValueError):
                self._engine = PaddleOCR(
                    use_angle_cls=self._use_angle_cls,
                    lang=self._lang,
                    use_gpu=False,
                    show_log=False,
                )

    def recognize(self, image: Any) -> OCROutput:
        if self._engine is None:
            raise RuntimeError("PPOCRv5 not loaded. Call load() first.")

        import numpy as np  # noqa: PLC0415

        t0 = time.perf_counter()
        img_array = np.array(image)
        w, h = image.size

        try:
            result = self._engine.ocr(img_array, cls=self._use_angle_cls)
        except TypeError:
            # PaddleOCR v3+ removed cls parameter.
            result = self._engine.ocr(img_array)

        boxes: list[OCRBox] = []
        texts: list[str] = []

        if result and result[0]:
            for line in result[0]:
                polygon = line[0]  # [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]
                text_info = line[1]  # (text, confidence)
                text = text_info[0]
                confidence = float(text_info[1])

                # Convert polygon to axis-aligned bbox normalised to [0,1].
                xs = [p[0] for p in polygon]
                ys = [p[1] for p in polygon]
                x1, y1, x2, y2 = min(xs) / w, min(ys) / h, max(xs) / w, max(ys) / h

                boxes.append(OCRBox(
                    text=text,
                    bbox=(x1, y1, x2, y2),
                    confidence=confidence,
                    language=self._lang,
                ))
                texts.append(text)

        # Sort top-to-bottom, left-to-right.
        boxes.sort(key=lambda b: (b.bbox[1], b.bbox[0]))
        texts_sorted = [b.text for b in boxes]

        elapsed = (time.perf_counter() - t0) * 1000
        return OCROutput(
            boxes=boxes,
            full_text=" ".join(texts_sorted),
            inference_time_ms=elapsed,
            engine_id="ppocr-v5",
        )

    def unload(self) -> None:
        self._engine = None

    @property
    def is_loaded(self) -> bool:
        return self._engine is not None


# ---------------------------------------------------------------------------
# Concrete: EasyOCR fallback
# ---------------------------------------------------------------------------


class EasyOCRFallback(BaseOCR):
    """OCR engine using EasyOCR (fallback when PaddleOCR is unavailable).

    Already a dependency from OmniParser — no extra install needed.
    """

    def __init__(self, languages: list[str] | None = None) -> None:
        self._languages = languages or ["en"]
        self._reader: Any = None

    def load(self) -> None:
        import easyocr  # noqa: PLC0415

        self._reader = easyocr.Reader(self._languages, gpu=False, verbose=False)

    def recognize(self, image: Any) -> OCROutput:
        if self._reader is None:
            raise RuntimeError("EasyOCR not loaded. Call load() first.")

        import numpy as np  # noqa: PLC0415

        t0 = time.perf_counter()
        img_array = np.array(image)
        h_px, w_px = img_array.shape[:2]

        results = self._reader.readtext(img_array)

        boxes: list[OCRBox] = []
        for bbox_pts, text, confidence in results:
            # bbox_pts: [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]
            xs = [p[0] for p in bbox_pts]
            ys = [p[1] for p in bbox_pts]
            x1, y1, x2, y2 = min(xs) / w_px, min(ys) / h_px, max(xs) / w_px, max(ys) / h_px

            boxes.append(OCRBox(
                text=text,
                bbox=(x1, y1, x2, y2),
                confidence=float(confidence),
            ))

        boxes.sort(key=lambda b: (b.bbox[1], b.bbox[0]))
        texts = [b.text for b in boxes]

        elapsed = (time.perf_counter() - t0) * 1000
        return OCROutput(
            boxes=boxes,
            full_text=" ".join(texts),
            inference_time_ms=elapsed,
            engine_id="easyocr",
        )

    def unload(self) -> None:
        self._reader = None

    @property
    def is_loaded(self) -> bool:
        return self._reader is not None
