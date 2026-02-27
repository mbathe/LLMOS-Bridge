"""Adapter for Meta's Prompt Guard 86M model (HuggingFace transformers).

Optional dependency: ``pip install transformers torch``

Usage::

    scanner = PromptGuardScanner(
        model_name="meta-llama/Prompt-Guard-86M",
    )
    result = await scanner.scan(text)
"""

from __future__ import annotations

import asyncio
from typing import Any

from llmos_bridge.logging import get_logger
from llmos_bridge.security.scanners.base import (
    InputScanner,
    ScanContext,
    ScanResult,
    ScanVerdict,
)

log = get_logger(__name__)


class PromptGuardScanner(InputScanner):
    """Wraps Meta's Prompt Guard 86M classifier via HuggingFace."""

    scanner_id = "prompt_guard"
    priority = 55
    version = "1.0.0"
    description = "Meta Prompt Guard 86M classifier (transformers)"

    def __init__(
        self,
        *,
        model_name: str = "meta-llama/Prompt-Guard-86M",
        reject_threshold: float = 0.5,
        device: str = "cpu",
        max_length: int = 512,
    ) -> None:
        self._model_name = model_name
        self._reject_threshold = reject_threshold
        self._device = device
        self._max_length = max_length
        self._pipeline: Any = None

    def _init_model(self) -> None:
        """Lazy-init the transformers pipeline on first use."""
        if self._pipeline is not None:
            return
        try:
            from transformers import pipeline as hf_pipeline  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "transformers is not installed. "
                "Install with: pip install transformers torch"
            ) from exc

        self._pipeline = hf_pipeline(
            "text-classification",
            model=self._model_name,
            device=self._device,
            truncation=True,
            max_length=self._max_length,
        )

    async def scan(
        self, text: str, context: ScanContext | None = None
    ) -> ScanResult:
        try:
            self._init_model()
        except ImportError as exc:
            return ScanResult(
                scanner_id=self.scanner_id,
                verdict=ScanVerdict.WARN,
                details=str(exc),
            )

        try:
            result = await asyncio.get_running_loop().run_in_executor(
                None, self._classify_sync, text
            )
            return result
        except Exception as exc:
            log.error("prompt_guard_scan_error", error=str(exc))
            return ScanResult(
                scanner_id=self.scanner_id,
                verdict=ScanVerdict.WARN,
                details=f"Prompt Guard error: {exc}",
            )

    def _classify_sync(self, text: str) -> ScanResult:
        """Run classification synchronously."""
        outputs = self._pipeline(text)
        label = outputs[0]["label"]
        score = outputs[0]["score"]

        is_injection = label.upper() in ("INJECTION", "JAILBREAK", "MALICIOUS")
        risk_score = score if is_injection else (1.0 - score)

        if risk_score >= self._reject_threshold:
            verdict = ScanVerdict.REJECT
        elif risk_score > 0.2:
            verdict = ScanVerdict.WARN
        else:
            verdict = ScanVerdict.ALLOW

        return ScanResult(
            scanner_id=self.scanner_id,
            verdict=verdict,
            risk_score=round(risk_score, 3),
            threat_types=["prompt_injection"] if is_injection else [],
            details=f"Label={label}, confidence={score:.3f}",
            metadata={"label": label, "confidence": score},
        )

    async def close(self) -> None:
        self._pipeline = None
