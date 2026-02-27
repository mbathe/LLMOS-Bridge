"""Perception layer — Structured pipeline with re-injection protocol.

The PerceptionPipeline orchestrates capture, OCR, diff detection, validation,
and result packaging for a single action execution.  Its output is a
structured dict that is injected back into ``execution_results`` under the
reserved ``_perception`` key, making perception data available to downstream
LLM templates:

    {{result.<action_id>._perception.after_text}}
    {{result.<action_id>._perception.diff_detected}}
    {{result.<action_id>._perception.before_text}}
    {{result.<action_id>._perception.ocr_confidence}}

Design decisions:
  - The pipeline is fully async and non-blocking.
  - All failures are soft (logged, result is partial) — a perception error
    must never abort an action that otherwise succeeded.
  - The pipeline is injected into PlanExecutor as an optional dependency,
    so deployments without mss/pytesseract simply omit it and perception
    fields are absent from results.
  - The structured dict format (not the PerceptionReport dataclass) is what
    gets stored in execution_results so it is JSON-serialisable and
    template-accessible without any custom deserialisation.

ActionPerceptionResult schema (all fields optional):
  {
    "action_id": str,
    "captured": bool,         # Whether at least one screenshot was taken
    "before_text": str | null,# OCR text of the before screenshot
    "after_text": str | null, # OCR text of the after screenshot
    "diff_detected": bool,    # True if before/after images differ
    "ocr_confidence": float | null,  # Mean confidence from pytesseract (0-100)
    "validation_passed": bool | null,  # Result of validate_output check
    "error": str | null       # Error message if partial failure occurred
  }
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from llmos_bridge.logging import get_logger
from llmos_bridge.perception.ocr import OCREngine
from llmos_bridge.perception.screen import ScreenCapture, Screenshot
from llmos_bridge.protocol.models import PerceptionConfig

log = get_logger(__name__)


@dataclass
class ActionPerceptionResult:
    """Structured perception result for a single action execution.

    This is the canonical type produced by the pipeline.  Its ``to_dict``
    output is what gets embedded in ``execution_results[action_id]`` under
    the ``_perception`` key.
    """

    action_id: str
    captured: bool = False
    before_text: str | None = None
    after_text: str | None = None
    diff_detected: bool = False
    ocr_confidence: float | None = None
    validation_passed: bool | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    # Vision module fields (populated when vision_module is available).
    vision_elements: list[dict[str, Any]] | None = None
    vision_element_count: int | None = None
    vision_interactable_count: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dict for embedding in execution_results."""
        d: dict[str, Any] = {
            "action_id": self.action_id,
            "captured": self.captured,
            "before_text": self.before_text,
            "after_text": self.after_text,
            "diff_detected": self.diff_detected,
            "ocr_confidence": self.ocr_confidence,
            "validation_passed": self.validation_passed,
            "error": self.error,
        }
        if self.vision_elements is not None:
            d["vision_elements"] = self.vision_elements
            d["vision_element_count"] = self.vision_element_count
            d["vision_interactable_count"] = self.vision_interactable_count
        return d


class PerceptionPipeline:
    """Full perception pipeline: capture → OCR → diff → validate → package.

    Usage in PlanExecutor::

        pipeline = PerceptionPipeline()

        # Before action:
        before = await pipeline.capture_before(action_id, action.perception)

        # Run action here…

        # After action:
        result = await pipeline.run_after(action_id, action.perception, before)
        execution_results[action_id]["_perception"] = result.to_dict()

    Or use the convenience method :meth:`run` which wraps the action coroutine::

        result, perception = await pipeline.run(
            action_id, action.perception,
            coro=module.execute(action.action, params),
        )
    """

    def __init__(
        self,
        capture: ScreenCapture | None = None,
        ocr: OCREngine | None = None,
        vision_module: Any | None = None,
        save_screenshots: bool = False,
        save_dir: str | None = None,
    ) -> None:
        self._capture = capture or ScreenCapture()
        self._ocr = ocr or OCREngine()
        self._vision = vision_module
        self._save_screenshots = save_screenshots
        self._save_dir = save_dir

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def capture_before(
        self, action_id: str, config: PerceptionConfig
    ) -> Screenshot | None:
        """Take a screenshot before the action if ``capture_before`` is set."""
        if not config.capture_before:
            return None
        return await self._safe_capture(action_id, "before")

    async def run_after(
        self,
        action_id: str,
        config: PerceptionConfig,
        before: Screenshot | None = None,
    ) -> ActionPerceptionResult:
        """Run the full post-action pipeline and return a structured result.

        Args:
            action_id: ID of the action being observed.
            config:    PerceptionConfig declared on the action.
            before:    Screenshot taken before the action (may be None).

        Returns:
            An :class:`ActionPerceptionResult` ready to embed in results.
        """
        result = ActionPerceptionResult(action_id=action_id)

        # 1. Capture after screenshot.
        after_screenshot: Screenshot | None = None
        if config.capture_after:
            after_screenshot = await self._safe_capture(action_id, "after")
            if after_screenshot is not None:
                result.captured = True

        # 2. OCR on before and after (if enabled).
        before_ocr_text: str | None = None
        after_ocr_text: str | None = None
        ocr_confidence: float | None = None

        if config.ocr_enabled:
            if before is not None:
                before_ocr = await self._safe_ocr(action_id, before, "before")
                if before_ocr:
                    before_ocr_text = before_ocr.text
            if after_screenshot is not None:
                after_ocr = await self._safe_ocr(action_id, after_screenshot, "after")
                if after_ocr:
                    after_ocr_text = after_ocr.text
                    ocr_confidence = getattr(after_ocr, "confidence", None)

        result.before_text = before_ocr_text
        result.after_text = after_ocr_text
        result.ocr_confidence = ocr_confidence

        # 2.5. Vision parse (if vision module available and after screenshot exists).
        if self._vision is not None and after_screenshot is not None:
            try:
                vision_result = await asyncio.wait_for(
                    self._vision.parse_screen(screenshot_bytes=after_screenshot.data),
                    timeout=15.0,
                )
                result.vision_elements = [
                    e.model_dump() for e in vision_result.elements[:50]
                ]
                result.vision_element_count = len(vision_result.elements)
                result.vision_interactable_count = sum(
                    1 for e in vision_result.elements if e.interactable
                )
                # Enrich OCR text if vision gives better text.
                if not result.after_text and vision_result.raw_ocr:
                    result.after_text = vision_result.raw_ocr
            except Exception as exc:
                log.warning(
                    "perception_vision_failed",
                    action_id=action_id,
                    error=str(exc),
                )

        # 3. Diff detection: compare raw pixel data if both screenshots exist.
        if before is not None and after_screenshot is not None:
            result.diff_detected = self._detect_diff(before, after_screenshot)

        # 4. Validate output using JSONPath expression if configured.
        if config.validate_output:
            result.validation_passed = self._validate_output(
                action_id, after_ocr_text or "", config.validate_output
            )

        return result

    async def run(
        self,
        action_id: str,
        config: PerceptionConfig,
        coro: Any,
    ) -> tuple[Any, ActionPerceptionResult]:
        """Execute *coro* with before/after perception.

        This is a convenience wrapper for callers that want the full pipeline
        in a single call.

        Returns:
            Tuple of (action_result, ActionPerceptionResult).
        """
        before = await self.capture_before(action_id, config)
        action_result = await coro
        perception = await self.run_after(action_id, config, before)
        return action_result, perception

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _safe_capture(
        self, action_id: str, phase: str
    ) -> Screenshot | None:
        """Capture a screenshot, swallowing errors to avoid breaking execution."""
        try:
            screenshot = await asyncio.wait_for(
                self._capture.capture(), timeout=5.0
            )
            if self._save_screenshots and self._save_dir:
                from pathlib import Path

                path = Path(self._save_dir) / f"{action_id}_{phase}.png"
                screenshot.save(path)
            return screenshot
        except Exception as exc:
            log.warning(
                "perception_capture_failed",
                action_id=action_id,
                phase=phase,
                error=str(exc),
            )
            return None

    async def _safe_ocr(
        self, action_id: str, screenshot: Screenshot, phase: str
    ) -> Any | None:
        """Run OCR, swallowing errors."""
        try:
            return await asyncio.wait_for(
                self._ocr.extract(screenshot), timeout=10.0
            )
        except Exception as exc:
            log.warning(
                "perception_ocr_failed",
                action_id=action_id,
                phase=phase,
                error=str(exc),
            )
            return None

    @staticmethod
    def _detect_diff(before: Screenshot, after: Screenshot) -> bool:
        """Return True if the two screenshots differ.

        Uses a fast pixel-count comparison.  A difference of more than 1% of
        pixels (above a threshold of 30/255 brightness change) indicates a
        meaningful visual change.
        """
        try:
            import numpy as np

            before_arr = np.array(before.image)  # type: ignore[attr-defined]
            after_arr = np.array(after.image)  # type: ignore[attr-defined]
            if before_arr.shape != after_arr.shape:
                return True
            diff = np.abs(before_arr.astype(int) - after_arr.astype(int))
            changed_pixels = np.sum(diff > 30)
            total_pixels = before_arr.size
            return bool(changed_pixels / total_pixels > 0.01)
        except Exception:
            # numpy unavailable or images incompatible — fall back to a simple
            # byte-level comparison.
            try:
                before_bytes = before.image.tobytes()  # type: ignore[attr-defined]
                after_bytes = after.image.tobytes()  # type: ignore[attr-defined]
                return before_bytes != after_bytes
            except Exception:
                return False

    @staticmethod
    def _validate_output(
        action_id: str, text: str, validate_expr: str
    ) -> bool:
        """Evaluate *validate_expr* against OCR *text*.

        Currently supports simple substring checks (``contains:<substr>``) and
        regex checks (``regex:<pattern>``).  JSONPath support requires an
        optional dependency and is deferred until a structured output format
        is established for OCR results.
        """
        import re

        if validate_expr.startswith("contains:"):
            needle = validate_expr[len("contains:"):]
            return needle in text

        if validate_expr.startswith("regex:"):
            pattern = validate_expr[len("regex:"):]
            try:
                return bool(re.search(pattern, text))
            except re.error:
                log.warning(
                    "perception_invalid_regex",
                    action_id=action_id,
                    pattern=pattern,
                )
                return False

        # Default: treat as substring.
        return validate_expr in text
