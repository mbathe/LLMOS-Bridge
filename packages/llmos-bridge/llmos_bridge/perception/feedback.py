"""Perception layer — Legacy feedback helpers (kept for backward compatibility).

The main perception entry point is now :mod:`llmos_bridge.perception.pipeline`
which provides the full :class:`~llmos_bridge.perception.pipeline.PerceptionPipeline`
with structured re-injection.

This module retains the :class:`PerceptionReport` dataclass (used by tests and
external callers) and the :class:`PerceptionFeedback` helper for standalone
before/after capture without the full pipeline.  New code should prefer
:class:`~llmos_bridge.perception.pipeline.PerceptionPipeline`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from llmos_bridge.logging import get_logger
from llmos_bridge.perception.ocr import OCREngine, OCRResult
from llmos_bridge.perception.screen import ScreenCapture, Screenshot
from llmos_bridge.protocol.models import PerceptionConfig

log = get_logger(__name__)


@dataclass
class PerceptionReport:
    """Before/after perception data for a single action.

    This is the legacy report type.  The canonical type for pipeline-based
    perception is :class:`~llmos_bridge.perception.pipeline.ActionPerceptionResult`.
    """

    action_id: str
    before: Screenshot | None = None
    after: Screenshot | None = None
    before_text: OCRResult | None = None
    after_text: OCRResult | None = None
    diff_detected: bool = False
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation.

        The ``_perception`` template key exposes this dict so downstream
        actions can use ``{{result.<id>._perception.after_text}}``.
        """
        return {
            "action_id": self.action_id,
            "before_text": self.before_text.text if self.before_text else None,
            "after_text": self.after_text.text if self.after_text else None,
            "diff_detected": self.diff_detected,
            "error": self.error,
        }


class PerceptionFeedback:
    """Standalone capture + OCR helper for non-pipeline callers.

    Prefer :class:`~llmos_bridge.perception.pipeline.PerceptionPipeline` for
    all new integrations.  This class is retained for backward compatibility
    and for callers that need fine-grained control over the capture phases.

    Usage::

        feedback = PerceptionFeedback()
        before = await feedback.capture_before(action_id, config)
        # ... run action ...
        after = await feedback.capture_after(action_id, config)
        after_text = await feedback.extract_text(after, config)
        report = PerceptionReport(
            action_id=action_id,
            before=before,
            after=after,
            after_text=after_text,
        )
    """

    def __init__(
        self,
        capture: ScreenCapture | None = None,
        ocr: OCREngine | None = None,
        save_dir: Path | None = None,
    ) -> None:
        self._capture = capture or ScreenCapture()
        self._ocr = ocr or OCREngine()
        self._save_dir = save_dir

    async def capture_before(
        self, action_id: str, config: PerceptionConfig
    ) -> Screenshot | None:
        if not config.capture_before:
            return None
        try:
            screenshot = await self._capture.capture()
            if self._save_dir:
                path = self._save_dir / f"{action_id}_before.png"
                screenshot.save(path)
            return screenshot
        except Exception as exc:
            log.warning("perception_before_failed", action_id=action_id, error=str(exc))
            return None

    async def capture_after(
        self, action_id: str, config: PerceptionConfig
    ) -> Screenshot | None:
        if not config.capture_after:
            return None
        try:
            screenshot = await self._capture.capture()
            if self._save_dir:
                path = self._save_dir / f"{action_id}_after.png"
                screenshot.save(path)
            return screenshot
        except Exception as exc:
            log.warning("perception_after_failed", action_id=action_id, error=str(exc))
            return None

    async def extract_text(
        self, screenshot: Screenshot, config: PerceptionConfig
    ) -> OCRResult | None:
        if not config.ocr_enabled:
            return None
        try:
            return await self._ocr.extract(screenshot)
        except Exception as exc:
            log.warning("perception_ocr_failed", error=str(exc))
            return None
