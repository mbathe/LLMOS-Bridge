"""Typed parameter models for the ``window_tracker`` module."""

from __future__ import annotations

from pydantic import BaseModel, Field


class GetActiveWindowParams(BaseModel):
    """No parameters — reads the currently focused window."""


class ListWindowsParams(BaseModel):
    """No parameters — lists all visible windows."""


class StartTrackingParams(BaseModel):
    title_pattern: str | None = Field(
        default=None,
        description="Regex pattern to match the target window title.",
    )
    window_id: str | None = Field(
        default=None,
        description="Window ID to track directly.",
    )


class StopTrackingParams(BaseModel):
    """No parameters — stops active tracking."""


class GetTrackingStatusParams(BaseModel):
    """No parameters — returns tracking status."""


class RecoverFocusParams(BaseModel):
    """No parameters — re-focuses the tracked window."""


class FocusWindowParams(BaseModel):
    window_id: str | None = Field(
        default=None,
        description="Window ID to focus.",
    )
    title_pattern: str | None = Field(
        default=None,
        description="Title pattern to match and focus.",
    )


class DetectContextSwitchParams(BaseModel):
    """No parameters — checks for context change."""


PARAMS_MAP: dict[str, type[BaseModel]] = {
    "get_active_window": GetActiveWindowParams,
    "list_windows": ListWindowsParams,
    "start_tracking": StartTrackingParams,
    "stop_tracking": StopTrackingParams,
    "get_tracking_status": GetTrackingStatusParams,
    "recover_focus": RecoverFocusParams,
    "focus_window": FocusWindowParams,
    "detect_context_switch": DetectContextSwitchParams,
}
