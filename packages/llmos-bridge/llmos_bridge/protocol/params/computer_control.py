"""Typed parameter models for the ``computer_control`` module."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field


class ClickElementParams(BaseModel):
    target_description: str = Field(
        description=(
            "Natural language description of the element to click "
            "(e.g. 'Submit button', 'email input field')."
        ),
    )
    click_type: Literal["single", "double", "right"] = Field(
        default="single",
        description="Type of click to perform.",
    )
    element_type: str | None = Field(
        default=None,
        description="Filter by element type: button, input, link, icon, text, checkbox.",
    )
    timeout: Annotated[float, Field(ge=1.0, le=30.0)] = Field(
        default=5.0,
        description="Max seconds to wait for screen capture and parsing.",
    )


class TypeIntoElementParams(BaseModel):
    target_description: str = Field(
        description="Description of the input field to type into.",
    )
    text: str = Field(description="Text to type.")
    clear_first: bool = Field(
        default=True,
        description="Clear the field before typing (Ctrl+A, Delete).",
    )
    element_type: str | None = Field(
        default=None,
        description="Filter by element type (usually 'input').",
    )


class WaitForElementParams(BaseModel):
    target_description: str = Field(
        description="Description of the element to wait for.",
    )
    timeout: Annotated[float, Field(ge=1.0, le=120.0)] = Field(
        default=30.0,
        description="Max seconds to wait before giving up.",
    )
    poll_interval: Annotated[float, Field(ge=0.5, le=10.0)] = Field(
        default=2.0,
        description="Seconds between screen captures.",
    )
    element_type: str | None = Field(default=None)


class ReadScreenParams(BaseModel):
    monitor: int = Field(default=0, ge=0)
    region: dict[str, int] | None = Field(
        default=None,
        description="Optional crop region: {left, top, width, height}.",
    )
    include_screenshot: bool = Field(
        default=False,
        description=(
            "If true, include the annotated screenshot as a base64-encoded PNG "
            "in the response under 'screenshot_b64'. The image has bounding boxes "
            "drawn around detected UI elements. Adds ~200-500KB to the response."
        ),
    )


class FindAndInteractParams(BaseModel):
    target_description: str = Field(description="Element description.")
    interaction: Literal["click", "double_click", "right_click", "hover"] = "click"
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional params for the interaction.",
    )


class GetElementInfoParams(BaseModel):
    target_description: str = Field(description="Element description.")
    element_type: str | None = None


class ExecuteGuiSequenceParams(BaseModel):
    steps: list[dict[str, Any]] = Field(
        description=(
            "List of steps: [{action: 'click_element', target: '...', params: {...}}, ...]"
        ),
    )
    stop_on_failure: bool = Field(
        default=True,
        description="Stop the sequence if any step fails.",
    )


class MoveToElementParams(BaseModel):
    target_description: str = Field(description="Element description.")
    element_type: str | None = None


class ScrollToElementParams(BaseModel):
    target_description: str = Field(description="Element description.")
    max_scrolls: Annotated[int, Field(ge=1, le=50)] = 10
    direction: Literal["down", "up"] = "down"


PARAMS_MAP: dict[str, type[BaseModel]] = {
    "click_element": ClickElementParams,
    "type_into_element": TypeIntoElementParams,
    "wait_for_element": WaitForElementParams,
    "read_screen": ReadScreenParams,
    "find_and_interact": FindAndInteractParams,
    "get_element_info": GetElementInfoParams,
    "execute_gui_sequence": ExecuteGuiSequenceParams,
    "move_to_element": MoveToElementParams,
    "scroll_to_element": ScrollToElementParams,
}
