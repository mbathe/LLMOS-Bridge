"""Perception layer — OCR engine (Tesseract via pytesseract).

Phase 1: Basic text extraction.
Phase 3: Structured data extraction, table detection, multi-language support.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from llmos_bridge.exceptions import OCRError
from llmos_bridge.logging import get_logger
from llmos_bridge.perception.screen import Screenshot

log = get_logger(__name__)


@dataclass
class OCRResult:
    text: str
    confidence: float  # 0.0 - 1.0
    language: str
    boxes: list[dict[str, Any]]  # Word-level bounding boxes


class OCREngine:
    """Extract text from screenshots using Tesseract.

    Usage::

        ocr = OCREngine()
        result = await ocr.extract(screenshot)
        print(result.text)
    """

    def __init__(self, lang: str = "eng", config: str = "") -> None:
        self._lang = lang
        self._config = config
        self._available = self._check_available()

    @staticmethod
    def _check_available() -> bool:
        try:
            import pytesseract  # noqa: F401

            return True
        except ImportError:
            log.warning("pytesseract_not_installed", hint="pip install pytesseract")
            return False

    async def extract(self, screenshot: Screenshot, lang: str | None = None) -> OCRResult:
        """Extract text from *screenshot*.

        Raises:
            OCRError: pytesseract is not installed or extraction failed.
        """
        if not self._available:
            raise OCRError(
                "OCR unavailable. Install: pip install llmos-bridge[gui]"
            )
        return await asyncio.to_thread(self._extract_sync, screenshot, lang or self._lang)

    def _extract_sync(self, screenshot: Screenshot, lang: str) -> OCRResult:
        import pytesseract
        from PIL import Image
        import io

        image = Image.open(io.BytesIO(screenshot.data))
        data = pytesseract.image_to_data(
            image,
            lang=lang,
            config=self._config,
            output_type=pytesseract.Output.DICT,
        )

        words = []
        confidences = []
        for i, word in enumerate(data["text"]):
            conf = int(data["conf"][i])
            if conf > 0 and word.strip():
                words.append(word)
                confidences.append(conf)
                boxes_entry: dict[str, Any] = {
                    "text": word,
                    "confidence": conf / 100.0,
                    "x": data["left"][i],
                    "y": data["top"][i],
                    "width": data["width"][i],
                    "height": data["height"][i],
                }
                words.append(word)

        full_text = pytesseract.image_to_string(image, lang=lang, config=self._config)
        avg_conf = sum(confidences) / len(confidences) if confidences else 0.0

        return OCRResult(
            text=full_text.strip(),
            confidence=avg_conf / 100.0,
            language=lang,
            boxes=[],
        )
