"""Typed parameter models for the ``gui`` module (PyAutoGUI + Tesseract)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field


class ClickPositionParams(BaseModel):
    x: Annotated[int, Field(ge=0)]
    y: Annotated[int, Field(ge=0)]
    button: Literal["left", "right", "middle"] = "left"
    clicks: Annotated[int, Field(ge=1, le=3)] = 1
    interval: float = Field(
        default=0.1, ge=0.0, le=5.0, description="Seconds between clicks."
    )


class ClickImageParams(BaseModel):
    image_path: str = Field(description="Path to the template image to find on screen.")
    confidence: Annotated[float, Field(ge=0.5, le=1.0)] = 0.8
    button: Literal["left", "right", "middle"] = "left"
    timeout: Annotated[int, Field(ge=1, le=60)] = 10


class DoubleClickParams(BaseModel):
    x: Annotated[int, Field(ge=0)] | None = None
    y: Annotated[int, Field(ge=0)] | None = None
    image_path: str | None = None
    confidence: Annotated[float, Field(ge=0.5, le=1.0)] = 0.8


class RightClickParams(BaseModel):
    x: Annotated[int, Field(ge=0)] | None = None
    y: Annotated[int, Field(ge=0)] | None = None
    image_path: str | None = None


class TypeTextParams(BaseModel):
    text: str = Field(description="Text to type.")
    interval: float = Field(
        default=0.05, ge=0.0, le=1.0, description="Seconds between key presses."
    )
    clear_first: bool = Field(
        default=False, description="Send Ctrl+A then Delete before typing."
    )
    method: Literal[
        "auto", "clipboard", "xdotool", "wtype", "ydotool", "pyautogui"
    ] = Field(
        default="auto",
        description=(
            "Input method. 'auto' selects the best available for the current "
            "environment. 'clipboard' is most reliable for non-US layouts."
        ),
    )


class KeyPressParams(BaseModel):
    keys: list[str] = Field(
        description=(
            "Key names as accepted by PyAutoGUI, e.g. ['ctrl', 'c'] or ['enter']. "
            "Multiple keys are pressed simultaneously (hotkey)."
        )
    )
    presses: Annotated[int, Field(ge=1, le=100)] = 1
    interval: float = Field(default=0.1, ge=0.0, le=2.0)


class ScrollParams(BaseModel):
    x: Annotated[int, Field(ge=0)] | None = None
    y: Annotated[int, Field(ge=0)] | None = None
    clicks: int = Field(
        description="Positive = scroll up, negative = scroll down.", default=3
    )


class DragDropParams(BaseModel):
    from_x: Annotated[int, Field(ge=0)]
    from_y: Annotated[int, Field(ge=0)]
    to_x: Annotated[int, Field(ge=0)]
    to_y: Annotated[int, Field(ge=0)]
    duration: float = Field(default=0.5, ge=0.1, le=5.0)


class FindOnScreenParams(BaseModel):
    image_path: str
    confidence: Annotated[float, Field(ge=0.5, le=1.0)] = 0.8
    grayscale: bool = True
    timeout: Annotated[int, Field(ge=1, le=60)] = 10


class GetScreenTextParams(BaseModel):
    region: tuple[int, int, int, int] | None = Field(
        default=None,
        description="(left, top, width, height) crop region. Full screen if None.",
    )
    lang: str = Field(default="eng", description="Tesseract language code.")


class GetWindowInfoParams(BaseModel):
    title_pattern: str | None = Field(
        default=None, description="Regex pattern to match the window title."
    )
    include_all: bool = Field(
        default=False, description="Return all windows, not just the focused one."
    )


class FocusWindowParams(BaseModel):
    title_pattern: str = Field(description="Regex pattern to match the window title.")
    timeout: Annotated[int, Field(ge=1, le=30)] = 10


class TakeScreenshotParams(BaseModel):
    output_path: str | None = None
    region: tuple[int, int, int, int] | None = None


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

PARAMS_MAP: dict[str, type[BaseModel]] = {
    "click_position": ClickPositionParams,
    "click_image": ClickImageParams,
    "double_click": DoubleClickParams,
    "right_click": RightClickParams,
    "type_text": TypeTextParams,
    "key_press": KeyPressParams,
    "scroll": ScrollParams,
    "drag_drop": DragDropParams,
    "find_on_screen": FindOnScreenParams,
    "get_screen_text": GetScreenTextParams,
    "get_window_info": GetWindowInfoParams,
    "focus_window": FocusWindowParams,
    "take_screenshot": TakeScreenshotParams,
}
