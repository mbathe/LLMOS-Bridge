"""Visual perception module layer — Abstract interface.

``BaseVisionModule`` defines the contract that all GUI perception backends
must satisfy.  The default implementation is ``OmniParserModule`` (built on
Microsoft OmniParser).  Community developers can swap it by registering any
subclass that sets ``MODULE_ID = "vision"``.

Data flow:
    screenshot bytes / path
        → BaseVisionModule.parse_screen()
        → VisionParseResult (list[VisionElement] + labeled image)
        → injected back into execution_results under _perception key
        → available as {{result.<action_id>._perception.elements}}
"""

from __future__ import annotations

import time
from abc import abstractmethod
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from llmos_bridge.modules.base import BaseModule, Platform


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class VisionElement(BaseModel):
    """A single parsed UI element detected in a screenshot.

    Coordinates are normalised to [0, 1] relative to the image dimensions
    so that results remain valid after any rescaling.
    """

    element_id: str = Field(description="Stable element ID within this parse result.")
    label: str = Field(description="Human-readable label / caption for the element.")
    element_type: str = Field(
        description="Semantic type: 'icon', 'text', 'button', 'input', 'checkbox', 'link', 'other'."
    )
    # Bounding box — normalised [0, 1] coordinates: (x1, y1, x2, y2)
    bbox: tuple[float, float, float, float] = Field(
        description="Normalised bounding box (x1, y1, x2, y2) in [0, 1]."
    )
    confidence: float = Field(ge=0.0, le=1.0, description="Detection confidence score.")
    text: str | None = Field(default=None, description="OCR text inside the element, if any.")
    interactable: bool = Field(
        default=True,
        description="Whether the element can be clicked or interacted with.",
    )
    extra: dict[str, Any] = Field(
        default_factory=dict,
        description="Backend-specific metadata (logits, feature vectors, etc.).",
    )

    def center(self) -> tuple[float, float]:
        """Return the normalised (cx, cy) centre of this element."""
        x1, y1, x2, y2 = self.bbox
        return ((x1 + x2) / 2, (y1 + y2) / 2)

    def pixel_center(self, width: int, height: int) -> tuple[int, int]:
        """Return the absolute pixel centre given the screenshot dimensions."""
        cx, cy = self.center()
        return (round(cx * width), round(cy * height))


class VisionParseResult(BaseModel):
    """Structured result from parsing a screenshot with a vision module."""

    elements: list[VisionElement] = Field(default_factory=list)
    width: int = Field(description="Screenshot width in pixels.")
    height: int = Field(description="Screenshot height in pixels.")
    raw_ocr: str | None = Field(
        default=None,
        description="Full OCR text extracted from the screenshot (concatenated).",
    )
    labeled_image_b64: str | None = Field(
        default=None,
        description="Base64-encoded annotated screenshot with drawn bounding boxes.",
    )
    parse_time_ms: float = Field(description="Wall-clock time for the parse call, in ms.")
    model_id: str = Field(description="Identifier of the model that produced this result.")
    error: str | None = Field(
        default=None,
        description="Non-fatal error message if parsing partially failed.",
    )

    def find_by_label(self, query: str, case_sensitive: bool = False) -> list[VisionElement]:
        """Return elements whose label contains *query* (substring match)."""
        q = query if case_sensitive else query.lower()
        return [
            e for e in self.elements
            if (e.label if case_sensitive else e.label.lower()).find(q) != -1
        ]

    def find_by_type(self, element_type: str) -> list[VisionElement]:
        """Return all elements of the given *element_type*."""
        return [e for e in self.elements if e.element_type == element_type]

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


# ---------------------------------------------------------------------------
# Abstract base class
# ---------------------------------------------------------------------------


class BaseVisionModule(BaseModule):
    """Abstract base for all visual perception modules.

    Every vision backend must:
      1. Set ``MODULE_ID = "vision"`` (or a custom ID for multi-backend use)
      2. Implement :meth:`parse_screen`
      3. Implement :meth:`get_manifest`
      4. Implement ``_action_parse_screen``, ``_action_find_element``,
         ``_action_capture_and_parse`` (routing to ``parse_screen``)

    The default implementation (``OmniParserModule``) wraps Microsoft OmniParser.
    Replace it by registering a subclass — no other code changes needed.
    """

    MODULE_ID: str = "vision"
    VERSION: str = "0.0.0"
    SUPPORTED_PLATFORMS: list[Platform] = [Platform.LINUX, Platform.WINDOWS, Platform.MACOS]

    @abstractmethod
    async def parse_screen(
        self,
        screenshot_path: str | None = None,
        screenshot_bytes: bytes | None = None,
        *,
        width: int | None = None,
        height: int | None = None,
    ) -> VisionParseResult:
        """Parse a screenshot and return structured UI elements.

        Exactly one of *screenshot_path* or *screenshot_bytes* must be provided.

        Args:
            screenshot_path:  Absolute path to the screenshot file (PNG/JPEG).
            screenshot_bytes: Raw image bytes (PNG/JPEG).
            width:            Image width in pixels (optional, read from image if not given).
            height:           Image height in pixels (optional, read from image if not given).

        Returns:
            :class:`VisionParseResult` with detected UI elements.
        """
        ...
