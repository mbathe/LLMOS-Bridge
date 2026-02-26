"""Typed parameter models — vision (OmniParser) module actions."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ParseScreenParams(BaseModel):
    """Parameters for ``vision.parse_screen``."""

    screenshot_path: str | None = Field(
        default=None,
        description="Absolute path to a PNG/JPEG screenshot file to parse.",
    )
    box_threshold: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Override the detection confidence threshold (default: 0.05).",
    )


class CaptureAndParseParams(BaseModel):
    """Parameters for ``vision.capture_and_parse``."""

    monitor: int = Field(
        default=0,
        ge=0,
        description="Monitor index to capture (0 = primary monitor).",
    )
    region: dict[str, int] | None = Field(
        default=None,
        description="Optional crop region with keys: left, top, width, height (pixels).",
    )
    box_threshold: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Override the detection confidence threshold.",
    )


class FindElementParams(BaseModel):
    """Parameters for ``vision.find_element``."""

    query: str = Field(
        description="Label substring or description of the element to find.",
    )
    element_type: str | None = Field(
        default=None,
        description="Filter by element type: icon, button, text, input, link.",
    )
    screenshot_path: str | None = Field(
        default=None,
        description="Optional path to an existing screenshot (captures screen if omitted).",
    )


class GetScreenTextParams(BaseModel):
    """Parameters for ``vision.get_screen_text``."""

    screenshot_path: str | None = Field(
        default=None,
        description="Optional path to an existing screenshot (captures screen if omitted).",
    )


PARAMS_MAP: dict[str, type[BaseModel]] = {
    "parse_screen": ParseScreenParams,
    "capture_and_parse": CaptureAndParseParams,
    "find_element": FindElementParams,
    "get_screen_text": GetScreenTextParams,
}
