"""Perception layer — Screen capture.

Captures screenshots using ``mss`` (cross-platform, fast).
Falls back to a stub if ``mss`` is not installed.

Phase 1: mss-based capture, PNG output.
Phase 3: Add region selection, multi-monitor support, format options.
"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from llmos_bridge.exceptions import ScreenCaptureError
from llmos_bridge.logging import get_logger

log = get_logger(__name__)


@dataclass
class Screenshot:
    """A captured screenshot."""

    width: int
    height: int
    format: str  # "png" or "jpeg"
    data: bytes  # Raw image bytes
    region: tuple[int, int, int, int] | None = None  # left, top, width, height

    def to_base64(self) -> str:
        return base64.b64encode(self.data).decode()

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.data)


class ScreenCapture:
    """Captures screenshots using mss.

    Usage::

        capture = ScreenCapture()
        screenshot = await capture.capture()
        screenshot.save(Path("/tmp/before.png"))
    """

    def __init__(self, format: str = "png", quality: int = 85) -> None:
        self._format = format
        self._quality = quality
        self._available = self._check_available()

    @staticmethod
    def _check_available() -> bool:
        try:
            import mss  # noqa: F401

            return True
        except ImportError:
            log.warning("mss_not_installed", hint="pip install mss")
            return False

    async def capture(
        self, region: tuple[int, int, int, int] | None = None
    ) -> Screenshot:
        """Capture a screenshot asynchronously.

        Args:
            region: Optional (left, top, width, height) crop region.

        Returns:
            A :class:`Screenshot` instance.

        Raises:
            ScreenCaptureError: mss is not installed or capture failed.
        """
        if not self._available:
            raise ScreenCaptureError(
                "Screen capture unavailable. Install: pip install llmos-bridge[gui]"
            )
        return await asyncio.to_thread(self._capture_sync, region)

    def _capture_sync(
        self, region: tuple[int, int, int, int] | None
    ) -> Screenshot:
        import mss
        import mss.tools

        with mss.mss() as sct:
            monitor = sct.monitors[0]  # All screens combined
            if region:
                left, top, width, height = region
                monitor = {"left": left, "top": top, "width": width, "height": height}

            img = sct.grab(monitor)
            data = mss.tools.to_png(img.rgb, img.size)

        return Screenshot(
            width=img.width,
            height=img.height,
            format="png",
            data=data,
            region=region,
        )

    async def capture_to_file(
        self, path: Path, region: tuple[int, int, int, int] | None = None
    ) -> Screenshot:
        screenshot = await self.capture(region)
        await asyncio.to_thread(screenshot.save, path)
        return screenshot
