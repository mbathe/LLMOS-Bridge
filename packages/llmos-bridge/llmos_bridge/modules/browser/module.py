"""Browser module — Implementation.

Covers:
  - Browser lifecycle (open/close) with session management
  - Navigation (navigate, wait, back, forward, reload)
  - Element interaction (click, fill, submit, select, get text)
  - Content extraction (page HTML/text/markdown, screenshot)
  - JavaScript execution in page context
  - File downloads
  - Wait for elements (attached, visible, hidden, detached)

Requires ``playwright`` (optional extra):
  pip install 'playwright' && python -m playwright install chromium

All operations are natively async — no ``asyncio.to_thread`` needed.
Sessions are protected by ``asyncio.Lock`` per session_id.
"""

from __future__ import annotations

import asyncio
import base64
import uuid
from pathlib import Path
from typing import Any

from llmos_bridge.exceptions import ActionExecutionError
from llmos_bridge.modules.base import BaseModule, Platform
from llmos_bridge.modules.manifest import ActionSpec, ModuleManifest, ParamSpec
from llmos_bridge.security.decorators import (
    audit_trail,
    requires_permission,
    sensitive_action,
)
from llmos_bridge.security.models import Permission, RiskLevel
from llmos_bridge.protocol.params.browser import (
    ClickElementParams,
    CloseBrowserParams,
    DownloadFileParams,
    ExecuteScriptParams,
    FillInputParams,
    GetElementTextParams,
    GetPageContentParams,
    NavigateToParams,
    OpenBrowserParams,
    SelectOptionParams,
    SubmitFormParams,
    TakeScreenshotParams,
    WaitForElementParams,
)

# Lazy playwright reference.
_playwright_mod: Any = None


class BrowserModule(BaseModule):
    MODULE_ID = "browser"
    VERSION = "1.0.0"
    SUPPORTED_PLATFORMS = [Platform.LINUX, Platform.MACOS, Platform.WINDOWS]

    def __init__(self) -> None:
        # session_id -> {playwright, browser, context, page}
        self._sessions: dict[str, dict[str, Any]] = {}
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._meta_lock = asyncio.Lock()
        super().__init__()

    def _check_dependencies(self) -> None:
        global _playwright_mod
        try:
            import playwright  # noqa: PLC0415
            _playwright_mod = playwright
        except ImportError as exc:
            from llmos_bridge.exceptions import ModuleLoadError  # noqa: PLC0415
            raise ModuleLoadError(
                "browser",
                "playwright is required: pip install 'playwright' && "
                "python -m playwright install chromium",
            ) from exc

    async def _get_session_lock(self, session_id: str) -> asyncio.Lock:
        async with self._meta_lock:
            if session_id not in self._session_locks:
                self._session_locks[session_id] = asyncio.Lock()
            return self._session_locks[session_id]

    def _get_session(self, session_id: str | None) -> dict[str, Any]:
        sid = session_id or "default"
        session = self._sessions.get(sid)
        if session is None:
            raise ActionExecutionError(
                module_id=self.MODULE_ID,
                action="",
                cause=RuntimeError(
                    f"No active browser session '{sid}'. "
                    "Use the 'open_browser' action first."
                ),
            )
        return session

    def _resolve_session_id(self, session_id: str | None) -> str:
        return session_id or "default"

    # ------------------------------------------------------------------
    # Actions — Lifecycle
    # ------------------------------------------------------------------

    @requires_permission(Permission.BROWSER, reason="Launches web browser")
    @audit_trail("standard")
    async def _action_open_browser(self, params: dict[str, Any]) -> dict[str, Any]:
        p = OpenBrowserParams.model_validate(params)
        sid = self._resolve_session_id(p.session_id)
        lock = await self._get_session_lock(sid)

        async with lock:
            # Close existing session if any.
            if sid in self._sessions:
                await self._close_session(sid)

            from playwright.async_api import async_playwright  # noqa: PLC0415

            pw = await async_playwright().start()

            launch_kwargs: dict[str, Any] = {"headless": p.headless}
            if p.proxy:
                launch_kwargs["proxy"] = {"server": p.proxy}

            if p.browser == "chromium":
                browser = await pw.chromium.launch(**launch_kwargs)
            elif p.browser == "firefox":
                browser = await pw.firefox.launch(**launch_kwargs)
            elif p.browser == "webkit":
                browser = await pw.webkit.launch(**launch_kwargs)
            else:
                await pw.stop()
                raise ValueError(f"Unsupported browser: {p.browser}")

            context_kwargs: dict[str, Any] = {
                "viewport": {"width": p.viewport_width, "height": p.viewport_height},
                "locale": p.locale,
            }
            if p.timezone:
                context_kwargs["timezone_id"] = p.timezone

            context = await browser.new_context(**context_kwargs)
            page = await context.new_page()

            self._sessions[sid] = {
                "playwright": pw,
                "browser": browser,
                "context": context,
                "page": page,
                "browser_type": p.browser,
            }

            return {
                "session_id": sid,
                "browser": p.browser,
                "headless": p.headless,
                "viewport": {"width": p.viewport_width, "height": p.viewport_height},
                "status": "opened",
            }

    async def _action_close_browser(self, params: dict[str, Any]) -> dict[str, Any]:
        p = CloseBrowserParams.model_validate(params)
        sid = self._resolve_session_id(p.session_id)
        lock = await self._get_session_lock(sid)

        async with lock:
            if sid not in self._sessions:
                return {"session_id": sid, "status": "not_open"}
            await self._close_session(sid)
            return {"session_id": sid, "status": "closed"}

    async def _close_session(self, sid: str) -> None:
        session = self._sessions.pop(sid, None)
        if session is None:
            return
        try:
            await session["context"].close()
        except Exception:
            pass
        try:
            await session["browser"].close()
        except Exception:
            pass
        try:
            await session["playwright"].stop()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Actions — Navigation
    # ------------------------------------------------------------------

    @requires_permission(Permission.BROWSER, reason="Navigates to URL")
    async def _action_navigate_to(self, params: dict[str, Any]) -> dict[str, Any]:
        p = NavigateToParams.model_validate(params)
        sid = self._resolve_session_id(p.session_id)
        lock = await self._get_session_lock(sid)

        async with lock:
            session = self._get_session(sid)
            page = session["page"]
            response = await page.goto(p.url, wait_until=p.wait_until, timeout=p.timeout)
            return {
                "url": page.url,
                "title": await page.title(),
                "status": response.status if response else None,
                "session_id": sid,
            }

    # ------------------------------------------------------------------
    # Actions — Element interaction
    # ------------------------------------------------------------------

    @requires_permission(Permission.BROWSER, reason="Interacts with web page")
    async def _action_click_element(self, params: dict[str, Any]) -> dict[str, Any]:
        p = ClickElementParams.model_validate(params)
        sid = self._resolve_session_id(p.session_id)
        lock = await self._get_session_lock(sid)

        async with lock:
            session = self._get_session(sid)
            page = session["page"]
            await page.click(
                p.selector,
                button=p.button,
                click_count=p.click_count,
                timeout=p.timeout,
            )
            return {
                "selector": p.selector,
                "clicked": True,
                "url": page.url,
                "session_id": sid,
            }

    @requires_permission(Permission.BROWSER, reason="Interacts with web page")
    async def _action_fill_input(self, params: dict[str, Any]) -> dict[str, Any]:
        p = FillInputParams.model_validate(params)
        sid = self._resolve_session_id(p.session_id)
        lock = await self._get_session_lock(sid)

        async with lock:
            session = self._get_session(sid)
            page = session["page"]
            await page.fill(p.selector, p.value, timeout=p.timeout)
            return {
                "selector": p.selector,
                "filled": True,
                "session_id": sid,
            }

    @requires_permission(Permission.BROWSER, reason="Interacts with web page")
    async def _action_submit_form(self, params: dict[str, Any]) -> dict[str, Any]:
        p = SubmitFormParams.model_validate(params)
        sid = self._resolve_session_id(p.session_id)
        lock = await self._get_session_lock(sid)

        async with lock:
            session = self._get_session(sid)
            page = session["page"]
            # Click the submit element and wait for navigation.
            async with page.expect_navigation(timeout=p.timeout):
                await page.click(p.selector, timeout=p.timeout)
            return {
                "submitted": True,
                "url": page.url,
                "title": await page.title(),
                "session_id": sid,
            }

    @requires_permission(Permission.BROWSER, reason="Interacts with web page")
    async def _action_select_option(self, params: dict[str, Any]) -> dict[str, Any]:
        p = SelectOptionParams.model_validate(params)
        sid = self._resolve_session_id(p.session_id)
        lock = await self._get_session_lock(sid)

        async with lock:
            session = self._get_session(sid)
            page = session["page"]
            values = p.value if isinstance(p.value, list) else [p.value]
            selected = await page.select_option(p.selector, values, timeout=p.timeout)
            return {
                "selector": p.selector,
                "selected": selected,
                "session_id": sid,
            }

    async def _action_get_element_text(self, params: dict[str, Any]) -> dict[str, Any]:
        p = GetElementTextParams.model_validate(params)
        sid = self._resolve_session_id(p.session_id)
        lock = await self._get_session_lock(sid)

        async with lock:
            session = self._get_session(sid)
            page = session["page"]
            element = await page.wait_for_selector(p.selector, timeout=p.timeout)
            text = await element.text_content() if element else None
            return {
                "selector": p.selector,
                "text": text,
                "session_id": sid,
            }

    # ------------------------------------------------------------------
    # Actions — Content extraction
    # ------------------------------------------------------------------

    async def _action_get_page_content(self, params: dict[str, Any]) -> dict[str, Any]:
        p = GetPageContentParams.model_validate(params)
        sid = self._resolve_session_id(p.session_id)
        lock = await self._get_session_lock(sid)

        async with lock:
            session = self._get_session(sid)
            page = session["page"]

            if p.selector:
                element = await page.query_selector(p.selector)
                if element is None:
                    return {"content": None, "format": p.format, "selector": p.selector, "session_id": sid}

                if p.format == "html":
                    content = await element.inner_html()
                else:
                    content = await element.text_content()
            else:
                if p.format == "html":
                    content = await page.content()
                else:
                    content = await page.evaluate("() => document.body.innerText")

            return {
                "content": content,
                "format": p.format,
                "url": page.url,
                "title": await page.title(),
                "session_id": sid,
            }

    async def _action_take_screenshot(self, params: dict[str, Any]) -> dict[str, Any]:
        p = TakeScreenshotParams.model_validate(params)
        sid = self._resolve_session_id(p.session_id)
        lock = await self._get_session_lock(sid)

        async with lock:
            session = self._get_session(sid)
            page = session["page"]

            screenshot_bytes = await page.screenshot(full_page=p.full_page)

            if p.output_path:
                path = Path(p.output_path)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(screenshot_bytes)
                return {
                    "saved_to": str(path),
                    "size_bytes": len(screenshot_bytes),
                    "session_id": sid,
                }
            else:
                b64 = base64.b64encode(screenshot_bytes).decode("ascii")
                return {
                    "base64": b64,
                    "size_bytes": len(screenshot_bytes),
                    "session_id": sid,
                }

    async def _action_download_file(self, params: dict[str, Any]) -> dict[str, Any]:
        p = DownloadFileParams.model_validate(params)
        sid = self._resolve_session_id(p.session_id)
        lock = await self._get_session_lock(sid)

        async with lock:
            session = self._get_session(sid)
            page = session["page"]
            dest = Path(p.destination)
            dest.parent.mkdir(parents=True, exist_ok=True)

            # Navigate to URL and capture download.
            async with page.expect_download(timeout=p.timeout) as download_info:
                await page.goto(p.url)
            download = await download_info.value
            await download.save_as(str(dest))

            return {
                "url": p.url,
                "destination": str(dest),
                "suggested_filename": download.suggested_filename,
                "session_id": sid,
            }

    # ------------------------------------------------------------------
    # Actions — JavaScript
    # ------------------------------------------------------------------

    @requires_permission(Permission.BROWSER, reason="Executes JavaScript in page context")
    @sensitive_action(RiskLevel.HIGH)
    @audit_trail("detailed")
    async def _action_execute_script(self, params: dict[str, Any]) -> dict[str, Any]:
        p = ExecuteScriptParams.model_validate(params)
        sid = self._resolve_session_id(p.session_id)
        lock = await self._get_session_lock(sid)

        async with lock:
            session = self._get_session(sid)
            page = session["page"]
            result = await page.evaluate(p.script, p.args if p.args else None)
            return {
                "result": result,
                "session_id": sid,
            }

    # ------------------------------------------------------------------
    # Actions — Wait
    # ------------------------------------------------------------------

    async def _action_wait_for_element(self, params: dict[str, Any]) -> dict[str, Any]:
        p = WaitForElementParams.model_validate(params)
        sid = self._resolve_session_id(p.session_id)
        lock = await self._get_session_lock(sid)

        async with lock:
            session = self._get_session(sid)
            page = session["page"]
            element = await page.wait_for_selector(
                p.selector, state=p.state, timeout=p.timeout
            )
            found = element is not None
            return {
                "selector": p.selector,
                "state": p.state,
                "found": found,
                "session_id": sid,
            }

    # ------------------------------------------------------------------
    # Manifest
    # ------------------------------------------------------------------

    def get_manifest(self) -> ModuleManifest:
        return ModuleManifest(
            module_id=self.MODULE_ID,
            version=self.VERSION,
            description=(
                "Web browser automation via Playwright — navigate, click, fill forms, "
                "extract content, take screenshots, and execute JavaScript."
            ),
            platforms=["linux", "macos", "windows"],
            declared_permissions=["browser_control", "network_access"],
            dependencies=["playwright"],
            tags=["browser", "web", "automation", "playwright", "scraping"],
            actions=[
                ActionSpec(
                    name="open_browser",
                    description="Launch a browser instance (Chromium, Firefox, or WebKit).",
                    params=[
                        ParamSpec(name="browser", type="string", description="Browser engine.", required=False, default="chromium", enum=["chromium", "firefox", "webkit"]),
                        ParamSpec(name="headless", type="boolean", description="Run in headless mode.", required=False, default=True),
                        ParamSpec(name="viewport_width", type="integer", description="Viewport width.", required=False, default=1280),
                        ParamSpec(name="viewport_height", type="integer", description="Viewport height.", required=False, default=720),
                        ParamSpec(name="session_id", type="string", description="Session identifier.", required=False),
                    ],
                    returns="object",
                    permission_required="power_user",
                    examples=[{"description": "Open headless Chromium", "params": {"browser": "chromium", "headless": True}}],
                ),
                ActionSpec(
                    name="navigate_to",
                    description="Navigate to a URL and wait for page load.",
                    params=[
                        ParamSpec(name="url", type="string", description="Target URL."),
                        ParamSpec(name="wait_until", type="string", description="Load event to wait for.", required=False, default="load", enum=["load", "domcontentloaded", "networkidle", "commit"]),
                        ParamSpec(name="timeout", type="integer", description="Navigation timeout (ms).", required=False, default=30000),
                        ParamSpec(name="session_id", type="string", description="Session identifier.", required=False),
                    ],
                    returns="object",
                    returns_description="URL, page title, and HTTP status.",
                    permission_required="power_user",
                    examples=[{"description": "Navigate to a website", "params": {"url": "https://example.com"}}],
                ),
                ActionSpec(
                    name="click_element",
                    description="Click an element matching a CSS selector or XPath.",
                    params=[
                        ParamSpec(name="selector", type="string", description="CSS selector or XPath."),
                        ParamSpec(name="button", type="string", description="Mouse button.", required=False, default="left", enum=["left", "right", "middle"]),
                        ParamSpec(name="click_count", type="integer", description="Number of clicks.", required=False, default=1),
                        ParamSpec(name="session_id", type="string", description="Session identifier.", required=False),
                    ],
                    returns="object",
                    permission_required="power_user",
                ),
                ActionSpec(
                    name="fill_input",
                    description="Fill a text input or textarea with a value.",
                    params=[
                        ParamSpec(name="selector", type="string", description="CSS selector of the input."),
                        ParamSpec(name="value", type="string", description="Text to fill in."),
                        ParamSpec(name="session_id", type="string", description="Session identifier.", required=False),
                    ],
                    returns="object",
                    permission_required="power_user",
                ),
                ActionSpec(
                    name="submit_form",
                    description="Click a submit button and wait for navigation.",
                    params=[
                        ParamSpec(name="selector", type="string", description="Selector of the submit element."),
                        ParamSpec(name="session_id", type="string", description="Session identifier.", required=False),
                    ],
                    returns="object",
                    permission_required="power_user",
                ),
                ActionSpec(
                    name="get_page_content",
                    description="Get the page content as HTML, text, or within a specific selector.",
                    params=[
                        ParamSpec(name="format", type="string", description="Output format.", required=False, default="text", enum=["html", "text", "markdown"]),
                        ParamSpec(name="selector", type="string", description="Limit to this CSS selector.", required=False),
                        ParamSpec(name="session_id", type="string", description="Session identifier.", required=False),
                    ],
                    returns="object",
                    returns_description="Page content, URL, and title.",
                    permission_required="readonly",
                ),
                ActionSpec(
                    name="take_screenshot",
                    description="Take a screenshot of the current page.",
                    params=[
                        ParamSpec(name="output_path", type="string", description="Save path. Returns base64 if omitted.", required=False),
                        ParamSpec(name="full_page", type="boolean", description="Capture full scrollable page.", required=False, default=False),
                        ParamSpec(name="session_id", type="string", description="Session identifier.", required=False),
                    ],
                    returns="object",
                    permission_required="readonly",
                ),
                ActionSpec(
                    name="download_file",
                    description="Download a file from a URL via the browser.",
                    params=[
                        ParamSpec(name="url", type="string", description="URL to download."),
                        ParamSpec(name="destination", type="string", description="Local save path."),
                        ParamSpec(name="timeout", type="integer", description="Download timeout (ms).", required=False, default=60000),
                        ParamSpec(name="session_id", type="string", description="Session identifier.", required=False),
                    ],
                    returns="object",
                    permission_required="power_user",
                ),
                ActionSpec(
                    name="execute_script",
                    description="Execute JavaScript in the page context.",
                    params=[
                        ParamSpec(name="script", type="string", description="JavaScript code to execute."),
                        ParamSpec(name="args", type="array", description="Arguments passed to the script.", required=False),
                        ParamSpec(name="session_id", type="string", description="Session identifier.", required=False),
                    ],
                    returns="object",
                    permission_required="power_user",
                    examples=[{"description": "Get page title", "params": {"script": "() => document.title"}}],
                ),
                ActionSpec(
                    name="wait_for_element",
                    description="Wait for an element to reach a specific state.",
                    params=[
                        ParamSpec(name="selector", type="string", description="CSS selector."),
                        ParamSpec(name="state", type="string", description="Target state.", required=False, default="visible", enum=["attached", "detached", "visible", "hidden"]),
                        ParamSpec(name="timeout", type="integer", description="Wait timeout (ms).", required=False, default=10000),
                        ParamSpec(name="session_id", type="string", description="Session identifier.", required=False),
                    ],
                    returns="object",
                    permission_required="readonly",
                ),
                ActionSpec(
                    name="get_element_text",
                    description="Get the text content of an element.",
                    params=[
                        ParamSpec(name="selector", type="string", description="CSS selector."),
                        ParamSpec(name="session_id", type="string", description="Session identifier.", required=False),
                    ],
                    returns="object",
                    permission_required="readonly",
                ),
                ActionSpec(
                    name="close_browser",
                    description="Close the browser and free resources.",
                    params=[
                        ParamSpec(name="session_id", type="string", description="Session to close.", required=False),
                    ],
                    returns="object",
                    permission_required="power_user",
                ),
                ActionSpec(
                    name="select_option",
                    description="Select option(s) in a <select> element.",
                    params=[
                        ParamSpec(name="selector", type="string", description="CSS selector of the <select>."),
                        ParamSpec(name="value", type="string", description="Value(s) to select."),
                        ParamSpec(name="session_id", type="string", description="Session identifier.", required=False),
                    ],
                    returns="object",
                    permission_required="power_user",
                ),
            ],
        )
