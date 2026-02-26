"""Typed parameter models for the ``browser`` module (Playwright)."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field


class OpenBrowserParams(BaseModel):
    browser: Literal["chromium", "firefox", "webkit"] = "chromium"
    headless: bool = True
    viewport_width: Annotated[int, Field(ge=320, le=3840)] = 1280
    viewport_height: Annotated[int, Field(ge=240, le=2160)] = 720
    locale: str = "en-US"
    timezone: str | None = None
    proxy: str | None = Field(
        default=None, description="Proxy URL, e.g. 'http://proxy:8080'."
    )
    session_id: str | None = Field(
        default=None,
        description="Reuse an existing browser session instead of opening a new one.",
    )


class NavigateToParams(BaseModel):
    url: str
    wait_until: Literal["load", "domcontentloaded", "networkidle", "commit"] = "load"
    timeout: Annotated[int, Field(ge=1000, le=120_000)] = 30_000
    session_id: str | None = None


class ClickElementParams(BaseModel):
    selector: str = Field(description="CSS selector or XPath expression.")
    timeout: Annotated[int, Field(ge=500, le=30_000)] = 5_000
    button: Literal["left", "right", "middle"] = "left"
    click_count: Annotated[int, Field(ge=1, le=3)] = 1
    session_id: str | None = None


class FillInputParams(BaseModel):
    selector: str
    value: str
    timeout: Annotated[int, Field(ge=500, le=30_000)] = 5_000
    session_id: str | None = None


class SubmitFormParams(BaseModel):
    selector: str = Field(description="Selector of the form or submit button.")
    timeout: Annotated[int, Field(ge=500, le=30_000)] = 5_000
    session_id: str | None = None


class GetPageContentParams(BaseModel):
    format: Literal["html", "text", "markdown"] = "text"
    selector: str | None = Field(
        default=None, description="Limit content to this CSS selector."
    )
    session_id: str | None = None


class TakeScreenshotParams(BaseModel):
    output_path: str | None = Field(
        default=None, description="Save screenshot to this path. Returns base64 if None."
    )
    full_page: bool = False
    session_id: str | None = None


class DownloadFileParams(BaseModel):
    url: str
    destination: str = Field(description="Local path where the file will be saved.")
    timeout: Annotated[int, Field(ge=1000, le=300_000)] = 60_000
    session_id: str | None = None


class ExecuteScriptParams(BaseModel):
    script: str = Field(description="JavaScript to execute in the page context.")
    args: list[Any] = Field(default_factory=list)
    session_id: str | None = None


class WaitForElementParams(BaseModel):
    selector: str
    state: Literal["attached", "detached", "visible", "hidden"] = "visible"
    timeout: Annotated[int, Field(ge=500, le=60_000)] = 10_000
    session_id: str | None = None


class GetElementTextParams(BaseModel):
    selector: str
    timeout: Annotated[int, Field(ge=500, le=30_000)] = 5_000
    session_id: str | None = None


class CloseBrowserParams(BaseModel):
    session_id: str | None = None


class SelectOptionParams(BaseModel):
    selector: str
    value: str | list[str] = Field(description="Value(s) to select.")
    timeout: Annotated[int, Field(ge=500, le=30_000)] = 5_000
    session_id: str | None = None


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

PARAMS_MAP: dict[str, type[BaseModel]] = {
    "open_browser": OpenBrowserParams,
    "navigate_to": NavigateToParams,
    "click_element": ClickElementParams,
    "fill_input": FillInputParams,
    "submit_form": SubmitFormParams,
    "get_page_content": GetPageContentParams,
    "take_screenshot": TakeScreenshotParams,
    "download_file": DownloadFileParams,
    "execute_script": ExecuteScriptParams,
    "wait_for_element": WaitForElementParams,
    "get_element_text": GetElementTextParams,
    "close_browser": CloseBrowserParams,
    "select_option": SelectOptionParams,
}
