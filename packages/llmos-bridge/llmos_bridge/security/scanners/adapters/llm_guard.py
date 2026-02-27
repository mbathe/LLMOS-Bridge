"""Adapter for the llm-guard library (DeBERTa-based, ONNX runtime).

Optional dependency: ``pip install llm-guard``

Usage::

    scanner = LLMGuardScanner(scanners=["PromptInjection"])
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


class LLMGuardScanner(InputScanner):
    """Wraps ``llm-guard`` library scanners for prompt injection detection."""

    scanner_id = "llm_guard"
    priority = 50
    version = "1.0.0"
    description = "DeBERTa-based prompt injection detection (llm-guard)"

    def __init__(
        self,
        *,
        scanners: list[str] | None = None,
        reject_threshold: float = 0.5,
        model_kwargs: dict[str, Any] | None = None,
    ) -> None:
        self._reject_threshold = reject_threshold
        self._scanner_names = scanners or ["PromptInjection"]
        self._model_kwargs = model_kwargs or {}
        self._scanners_initialized = False
        self._guard_scanners: list[Any] = []

    def _init_scanners(self) -> None:
        """Lazy-init llm-guard scanners on first scan call."""
        if self._scanners_initialized:
            return
        try:
            from llm_guard.input_scanners import PromptInjection  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "llm-guard is not installed. "
                "Install with: pip install llm-guard"
            ) from exc

        from llm_guard import input_scanners as is_mod  # type: ignore[import-untyped]

        for name in self._scanner_names:
            cls = getattr(is_mod, name, None)
            if cls is not None:
                self._guard_scanners.append(cls(**self._model_kwargs))
            else:
                log.warning("llm_guard_scanner_not_found", name=name)
        self._scanners_initialized = True

    async def scan(
        self, text: str, context: ScanContext | None = None
    ) -> ScanResult:
        try:
            self._init_scanners()
        except ImportError as exc:
            return ScanResult(
                scanner_id=self.scanner_id,
                verdict=ScanVerdict.WARN,
                details=str(exc),
            )

        try:
            result = await asyncio.get_running_loop().run_in_executor(
                None, self._scan_sync, text
            )
            return result
        except Exception as exc:
            log.error("llm_guard_scan_error", error=str(exc))
            return ScanResult(
                scanner_id=self.scanner_id,
                verdict=ScanVerdict.WARN,
                details=f"LLM Guard error: {exc}",
            )

    def _scan_sync(self, text: str) -> ScanResult:
        """Run llm-guard scanners synchronously."""
        max_score = 0.0
        threat_types: list[str] = []
        details_parts: list[str] = []

        for guard_scanner in self._guard_scanners:
            sanitized, is_valid, score = guard_scanner.scan(text)
            scanner_name = type(guard_scanner).__name__
            if not is_valid:
                threat_types.append(f"llm_guard.{scanner_name}")
                details_parts.append(f"{scanner_name}: score={score:.2f}")
            # llm-guard: 1.0 = safe, 0.0 = threat.
            risk = 1.0 - score
            max_score = max(max_score, risk)

        if max_score >= self._reject_threshold:
            verdict = ScanVerdict.REJECT
        elif max_score > 0.1:
            verdict = ScanVerdict.WARN
        else:
            verdict = ScanVerdict.ALLOW

        return ScanResult(
            scanner_id=self.scanner_id,
            verdict=verdict,
            risk_score=round(max_score, 3),
            threat_types=threat_types,
            details="; ".join(details_parts) if details_parts else "All clean",
        )

    async def close(self) -> None:
        self._guard_scanners.clear()
        self._scanners_initialized = False
