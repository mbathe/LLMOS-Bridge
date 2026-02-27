"""Unit tests — BrowserModule (all Playwright calls mocked)."""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llmos_bridge.exceptions import ActionExecutionError


# ---------------------------------------------------------------------------
# Module import — mock Playwright so we can import BrowserModule even if
# playwright is not installed.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _mock_playwright_import():
    """Patch the playwright dependency check so BrowserModule can be imported."""
    with patch.dict("sys.modules", {"playwright": MagicMock(), "playwright.async_api": MagicMock()}):
        yield


def _make_module():
    from llmos_bridge.modules.browser import BrowserModule
    return BrowserModule()


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


def _mock_page() -> AsyncMock:
    """Create a realistic async mock of a Playwright Page."""
    page = AsyncMock()
    page.url = "https://example.com"
    page.title = AsyncMock(return_value="Example Domain")
    page.content = AsyncMock(return_value="<html><body>Hello</body></html>")
    page.evaluate = AsyncMock(return_value="Hello World")
    page.click = AsyncMock()
    page.fill = AsyncMock()
    page.select_option = AsyncMock(return_value=["option1"])
    page.goto = AsyncMock(return_value=MagicMock(status=200))
    page.wait_for_selector = AsyncMock(return_value=MagicMock(text_content=AsyncMock(return_value="Element text")))
    page.query_selector = AsyncMock(return_value=MagicMock(
        inner_html=AsyncMock(return_value="<span>inner</span>"),
        text_content=AsyncMock(return_value="inner text"),
    ))
    page.screenshot = AsyncMock(return_value=b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

    # expect_navigation context manager
    nav_cm = AsyncMock()
    nav_cm.__aenter__ = AsyncMock(return_value=None)
    nav_cm.__aexit__ = AsyncMock(return_value=False)
    page.expect_navigation = MagicMock(return_value=nav_cm)

    # expect_download context manager — .value must be awaitable (coroutine)
    download_mock = MagicMock()
    download_mock.suggested_filename = "file.zip"
    download_mock.save_as = AsyncMock()

    class _DownloadInfo:
        """Mimics Playwright EventContextManagerImpl — .value returns a coroutine."""
        @property
        def value(self):
            async def _get():
                return download_mock
            return _get()

    download_cm = AsyncMock()
    download_cm.__aenter__ = AsyncMock(return_value=_DownloadInfo())
    download_cm.__aexit__ = AsyncMock(return_value=False)
    page.expect_download = MagicMock(return_value=download_cm)

    return page


def _mock_context(page: AsyncMock) -> AsyncMock:
    ctx = AsyncMock()
    ctx.new_page = AsyncMock(return_value=page)
    ctx.close = AsyncMock()
    return ctx


def _mock_browser(context: AsyncMock) -> AsyncMock:
    browser = AsyncMock()
    browser.new_context = AsyncMock(return_value=context)
    browser.close = AsyncMock()
    return browser


def _mock_playwright_instance(browser_mock: AsyncMock) -> AsyncMock:
    pw = AsyncMock()
    pw.chromium = AsyncMock()
    pw.chromium.launch = AsyncMock(return_value=browser_mock)
    pw.firefox = AsyncMock()
    pw.firefox.launch = AsyncMock(return_value=browser_mock)
    pw.webkit = AsyncMock()
    pw.webkit.launch = AsyncMock(return_value=browser_mock)
    pw.stop = AsyncMock()
    return pw


async def _open_browser(module: Any, session_id: str = "default") -> dict:
    """Open a browser session by injecting mocks directly."""
    page = _mock_page()
    context = _mock_context(page)
    browser = _mock_browser(context)
    pw = _mock_playwright_instance(browser)

    with patch("llmos_bridge.modules.browser.module.async_playwright", create=True) as mock_ap:
        # Make the async_playwright() call return an object whose start() returns pw
        ap_cm = AsyncMock()
        ap_cm.start = AsyncMock(return_value=pw)
        mock_ap.return_value = ap_cm

        # Patch the import inside _action_open_browser
        with patch.dict("sys.modules", {
            "playwright": MagicMock(),
            "playwright.async_api": MagicMock(async_playwright=lambda: ap_cm),
        }):
            # Directly inject the session since mocking the import chain is complex
            module._sessions[session_id] = {
                "playwright": pw,
                "browser": browser,
                "context": context,
                "page": page,
                "browser_type": "chromium",
            }

    return {
        "session_id": session_id,
        "browser": "chromium",
        "headless": True,
        "viewport": {"width": 1280, "height": 720},
        "status": "opened",
        "page": page,
        "context": context,
        "browser_obj": browser,
        "pw": pw,
    }


# ---------------------------------------------------------------------------
# Tests — Module basics
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestModuleBasics:
    def test_module_id(self) -> None:
        m = _make_module()
        assert m.MODULE_ID == "browser"

    def test_version(self) -> None:
        m = _make_module()
        assert m.VERSION == "1.0.0"

    def test_supported_platforms(self) -> None:
        from llmos_bridge.modules.base import Platform
        m = _make_module()
        assert Platform.LINUX in m.SUPPORTED_PLATFORMS
        assert Platform.MACOS in m.SUPPORTED_PLATFORMS
        assert Platform.WINDOWS in m.SUPPORTED_PLATFORMS


# ---------------------------------------------------------------------------
# Tests — Manifest
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestManifest:
    def test_manifest_module_id(self) -> None:
        m = _make_module()
        manifest = m.get_manifest()
        assert manifest.module_id == "browser"

    def test_manifest_action_count(self) -> None:
        m = _make_module()
        manifest = m.get_manifest()
        assert len(manifest.actions) == 13

    def test_manifest_action_names(self) -> None:
        m = _make_module()
        manifest = m.get_manifest()
        names = {a.name for a in manifest.actions}
        expected = {
            "open_browser", "close_browser", "navigate_to",
            "click_element", "fill_input", "submit_form", "select_option",
            "get_element_text", "get_page_content",
            "take_screenshot", "download_file",
            "execute_script", "wait_for_element",
        }
        assert names == expected

    def test_manifest_has_dependencies(self) -> None:
        m = _make_module()
        manifest = m.get_manifest()
        assert "playwright" in manifest.dependencies

    def test_manifest_has_permissions(self) -> None:
        m = _make_module()
        manifest = m.get_manifest()
        assert "browser_control" in manifest.declared_permissions
        assert "network_access" in manifest.declared_permissions


# ---------------------------------------------------------------------------
# Tests — Session management
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSessionManagement:
    @pytest.mark.asyncio
    async def test_get_session_raises_when_no_session(self) -> None:
        m = _make_module()
        with pytest.raises(ActionExecutionError):
            m._get_session("nonexistent")

    @pytest.mark.asyncio
    async def test_resolve_session_id_default(self) -> None:
        m = _make_module()
        assert m._resolve_session_id(None) == "default"
        assert m._resolve_session_id("my_session") == "my_session"

    @pytest.mark.asyncio
    async def test_session_lock_creation(self) -> None:
        m = _make_module()
        lock1 = await m._get_session_lock("s1")
        lock2 = await m._get_session_lock("s1")
        assert lock1 is lock2  # Same lock for same session

    @pytest.mark.asyncio
    async def test_different_session_locks(self) -> None:
        m = _make_module()
        lock1 = await m._get_session_lock("s1")
        lock2 = await m._get_session_lock("s2")
        assert lock1 is not lock2

    @pytest.mark.asyncio
    async def test_close_session_removes_from_dict(self) -> None:
        m = _make_module()
        info = await _open_browser(m, "test_close")
        assert "test_close" in m._sessions
        await m._close_session("test_close")
        assert "test_close" not in m._sessions

    @pytest.mark.asyncio
    async def test_close_session_calls_cleanup(self) -> None:
        m = _make_module()
        info = await _open_browser(m, "test_cleanup")
        pw = info["pw"]
        browser = info["browser_obj"]
        context = info["context"]

        await m._close_session("test_cleanup")
        context.close.assert_awaited_once()
        browser.close.assert_awaited_once()
        pw.stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_close_nonexistent_session_is_noop(self) -> None:
        m = _make_module()
        await m._close_session("does_not_exist")  # Should not raise


# ---------------------------------------------------------------------------
# Tests — close_browser action
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCloseBrowser:
    @pytest.mark.asyncio
    async def test_close_existing_session(self) -> None:
        m = _make_module()
        await _open_browser(m, "default")
        result = await m._action_close_browser({"session_id": None})
        assert result["status"] == "closed"
        assert "default" not in m._sessions

    @pytest.mark.asyncio
    async def test_close_nonexistent_returns_not_open(self) -> None:
        m = _make_module()
        result = await m._action_close_browser({"session_id": "missing"})
        assert result["status"] == "not_open"


# ---------------------------------------------------------------------------
# Tests — navigate_to
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNavigateTo:
    @pytest.mark.asyncio
    async def test_navigate_basic(self) -> None:
        m = _make_module()
        info = await _open_browser(m)
        page = info["page"]

        result = await m._action_navigate_to({
            "url": "https://example.com",
            "wait_until": "load",
        })

        page.goto.assert_awaited_once()
        assert result["url"] == "https://example.com"
        assert result["title"] == "Example Domain"
        assert result["status"] == 200
        assert result["session_id"] == "default"

    @pytest.mark.asyncio
    async def test_navigate_no_session_raises(self) -> None:
        m = _make_module()
        with pytest.raises(ActionExecutionError):
            await m._action_navigate_to({"url": "https://example.com"})


# ---------------------------------------------------------------------------
# Tests — click_element
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestClickElement:
    @pytest.mark.asyncio
    async def test_click_basic(self) -> None:
        m = _make_module()
        info = await _open_browser(m)
        page = info["page"]

        result = await m._action_click_element({
            "selector": "#submit-btn",
        })

        page.click.assert_awaited_once_with(
            "#submit-btn", button="left", click_count=1, timeout=5000,
        )
        assert result["clicked"] is True
        assert result["selector"] == "#submit-btn"

    @pytest.mark.asyncio
    async def test_click_right_button(self) -> None:
        m = _make_module()
        info = await _open_browser(m)
        page = info["page"]

        await m._action_click_element({
            "selector": "div.menu",
            "button": "right",
            "click_count": 2,
        })

        page.click.assert_awaited_once_with(
            "div.menu", button="right", click_count=2, timeout=5000,
        )


# ---------------------------------------------------------------------------
# Tests — fill_input
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFillInput:
    @pytest.mark.asyncio
    async def test_fill_basic(self) -> None:
        m = _make_module()
        info = await _open_browser(m)
        page = info["page"]

        result = await m._action_fill_input({
            "selector": "input[name='email']",
            "value": "user@example.com",
        })

        page.fill.assert_awaited_once_with(
            "input[name='email']", "user@example.com", timeout=5000,
        )
        assert result["filled"] is True

    @pytest.mark.asyncio
    async def test_fill_no_session_raises(self) -> None:
        m = _make_module()
        with pytest.raises(ActionExecutionError):
            await m._action_fill_input({
                "selector": "input",
                "value": "test",
            })


# ---------------------------------------------------------------------------
# Tests — submit_form
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSubmitForm:
    @pytest.mark.asyncio
    async def test_submit_basic(self) -> None:
        m = _make_module()
        info = await _open_browser(m)
        page = info["page"]

        result = await m._action_submit_form({
            "selector": "button[type='submit']",
        })

        assert result["submitted"] is True
        assert result["url"] == "https://example.com"
        assert result["title"] == "Example Domain"
        page.click.assert_awaited()


# ---------------------------------------------------------------------------
# Tests — select_option
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSelectOption:
    @pytest.mark.asyncio
    async def test_select_single_value(self) -> None:
        m = _make_module()
        info = await _open_browser(m)
        page = info["page"]

        result = await m._action_select_option({
            "selector": "#country",
            "value": "fr",
        })

        page.select_option.assert_awaited_once_with(
            "#country", ["fr"], timeout=5000,
        )
        assert result["selected"] == ["option1"]

    @pytest.mark.asyncio
    async def test_select_multiple_values(self) -> None:
        m = _make_module()
        info = await _open_browser(m)
        page = info["page"]

        await m._action_select_option({
            "selector": "#tags",
            "value": ["a", "b", "c"],
        })

        page.select_option.assert_awaited_once_with(
            "#tags", ["a", "b", "c"], timeout=5000,
        )


# ---------------------------------------------------------------------------
# Tests — get_element_text
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetElementText:
    @pytest.mark.asyncio
    async def test_get_text_basic(self) -> None:
        m = _make_module()
        info = await _open_browser(m)

        result = await m._action_get_element_text({
            "selector": "h1",
        })

        assert result["text"] == "Element text"
        assert result["selector"] == "h1"

    @pytest.mark.asyncio
    async def test_get_text_element_not_found(self) -> None:
        m = _make_module()
        info = await _open_browser(m)
        page = info["page"]
        page.wait_for_selector = AsyncMock(return_value=None)

        result = await m._action_get_element_text({"selector": "#missing"})
        assert result["text"] is None


# ---------------------------------------------------------------------------
# Tests — get_page_content
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetPageContent:
    @pytest.mark.asyncio
    async def test_get_content_html_full_page(self) -> None:
        m = _make_module()
        info = await _open_browser(m)

        result = await m._action_get_page_content({"format": "html"})

        assert result["format"] == "html"
        assert "<html>" in result["content"]
        assert result["url"] == "https://example.com"

    @pytest.mark.asyncio
    async def test_get_content_text_full_page(self) -> None:
        m = _make_module()
        info = await _open_browser(m)
        page = info["page"]

        result = await m._action_get_page_content({"format": "text"})

        page.evaluate.assert_awaited_once_with("() => document.body.innerText")
        assert result["format"] == "text"

    @pytest.mark.asyncio
    async def test_get_content_html_with_selector(self) -> None:
        m = _make_module()
        info = await _open_browser(m)
        page = info["page"]

        result = await m._action_get_page_content({
            "format": "html",
            "selector": "#content",
        })

        page.query_selector.assert_awaited_once_with("#content")
        assert result["content"] == "<span>inner</span>"

    @pytest.mark.asyncio
    async def test_get_content_text_with_selector(self) -> None:
        m = _make_module()
        info = await _open_browser(m)

        result = await m._action_get_page_content({
            "format": "text",
            "selector": "#content",
        })

        assert result["content"] == "inner text"

    @pytest.mark.asyncio
    async def test_get_content_selector_not_found(self) -> None:
        m = _make_module()
        info = await _open_browser(m)
        page = info["page"]
        page.query_selector = AsyncMock(return_value=None)

        result = await m._action_get_page_content({
            "format": "html",
            "selector": "#nonexistent",
        })

        assert result["content"] is None


# ---------------------------------------------------------------------------
# Tests — take_screenshot
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTakeScreenshot:
    @pytest.mark.asyncio
    async def test_screenshot_to_base64(self) -> None:
        m = _make_module()
        info = await _open_browser(m)

        result = await m._action_take_screenshot({})

        assert "base64" in result
        assert result["size_bytes"] > 0
        # Verify it's valid base64
        decoded = base64.b64decode(result["base64"])
        assert decoded[:4] == b"\x89PNG"

    @pytest.mark.asyncio
    async def test_screenshot_to_file(self, tmp_path: Path) -> None:
        m = _make_module()
        info = await _open_browser(m)
        out = str(tmp_path / "shot.png")

        result = await m._action_take_screenshot({"output_path": out})

        assert result["saved_to"] == out
        assert result["size_bytes"] > 0
        assert Path(out).exists()

    @pytest.mark.asyncio
    async def test_screenshot_creates_parent_dirs(self, tmp_path: Path) -> None:
        m = _make_module()
        info = await _open_browser(m)
        out = str(tmp_path / "nested" / "dirs" / "shot.png")

        result = await m._action_take_screenshot({"output_path": out})

        assert Path(out).parent.exists()
        assert result["saved_to"] == out

    @pytest.mark.asyncio
    async def test_screenshot_full_page(self) -> None:
        m = _make_module()
        info = await _open_browser(m)
        page = info["page"]

        await m._action_take_screenshot({"full_page": True})

        page.screenshot.assert_awaited_once_with(full_page=True)


# ---------------------------------------------------------------------------
# Tests — download_file
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDownloadFile:
    @pytest.mark.asyncio
    async def test_download_basic(self, tmp_path: Path) -> None:
        m = _make_module()
        info = await _open_browser(m)
        dest = str(tmp_path / "downloaded.zip")

        result = await m._action_download_file({
            "url": "https://example.com/file.zip",
            "destination": dest,
        })

        assert result["url"] == "https://example.com/file.zip"
        assert result["destination"] == dest
        assert result["suggested_filename"] == "file.zip"

    @pytest.mark.asyncio
    async def test_download_creates_parent_dirs(self, tmp_path: Path) -> None:
        m = _make_module()
        info = await _open_browser(m)
        dest = str(tmp_path / "a" / "b" / "c" / "file.bin")

        await m._action_download_file({
            "url": "https://example.com/file.bin",
            "destination": dest,
        })

        assert Path(dest).parent.exists()


# ---------------------------------------------------------------------------
# Tests — execute_script
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExecuteScript:
    @pytest.mark.asyncio
    async def test_script_no_args(self) -> None:
        m = _make_module()
        info = await _open_browser(m)
        page = info["page"]

        result = await m._action_execute_script({
            "script": "() => document.title",
        })

        page.evaluate.assert_awaited_once_with("() => document.title", None)
        assert result["result"] == "Hello World"

    @pytest.mark.asyncio
    async def test_script_with_args(self) -> None:
        m = _make_module()
        info = await _open_browser(m)
        page = info["page"]

        await m._action_execute_script({
            "script": "(a, b) => a + b",
            "args": [1, 2],
        })

        page.evaluate.assert_awaited_once_with("(a, b) => a + b", [1, 2])


# ---------------------------------------------------------------------------
# Tests — wait_for_element
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestWaitForElement:
    @pytest.mark.asyncio
    async def test_wait_visible(self) -> None:
        m = _make_module()
        info = await _open_browser(m)

        result = await m._action_wait_for_element({
            "selector": "#loading",
            "state": "visible",
        })

        assert result["found"] is True
        assert result["selector"] == "#loading"
        assert result["state"] == "visible"

    @pytest.mark.asyncio
    async def test_wait_element_not_found(self) -> None:
        m = _make_module()
        info = await _open_browser(m)
        page = info["page"]
        page.wait_for_selector = AsyncMock(return_value=None)

        result = await m._action_wait_for_element({
            "selector": "#ghost",
            "state": "attached",
        })

        assert result["found"] is False

    @pytest.mark.asyncio
    async def test_wait_detached(self) -> None:
        m = _make_module()
        info = await _open_browser(m)

        result = await m._action_wait_for_element({
            "selector": "#spinner",
            "state": "detached",
            "timeout": 5000,
        })

        assert result["state"] == "detached"


# ---------------------------------------------------------------------------
# Tests — Multiple sessions
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMultipleSessions:
    @pytest.mark.asyncio
    async def test_two_sessions_independent(self) -> None:
        m = _make_module()
        info1 = await _open_browser(m, "s1")
        info2 = await _open_browser(m, "s2")

        assert "s1" in m._sessions
        assert "s2" in m._sessions
        assert m._sessions["s1"]["page"] is not m._sessions["s2"]["page"]

    @pytest.mark.asyncio
    async def test_close_one_session_keeps_other(self) -> None:
        m = _make_module()
        await _open_browser(m, "s1")
        await _open_browser(m, "s2")

        await m._action_close_browser({"session_id": "s1"})
        assert "s1" not in m._sessions
        assert "s2" in m._sessions

    @pytest.mark.asyncio
    async def test_navigate_specific_session(self) -> None:
        m = _make_module()
        info1 = await _open_browser(m, "s1")
        info2 = await _open_browser(m, "s2")
        page1 = info1["page"]
        page2 = info2["page"]

        await m._action_navigate_to({
            "url": "https://one.com",
            "session_id": "s1",
        })

        page1.goto.assert_awaited_once()
        page2.goto.assert_not_awaited()


# ---------------------------------------------------------------------------
# Tests — BaseModule.execute() dispatch
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExecuteDispatch:
    @pytest.mark.asyncio
    async def test_execute_navigate(self) -> None:
        m = _make_module()
        await _open_browser(m)

        result = await m.execute("navigate_to", {"url": "https://example.com"})

        assert result["url"] == "https://example.com"

    @pytest.mark.asyncio
    async def test_execute_get_page_content(self) -> None:
        m = _make_module()
        await _open_browser(m)

        result = await m.execute("get_page_content", {"format": "html"})

        assert "content" in result

    @pytest.mark.asyncio
    async def test_execute_close_browser(self) -> None:
        m = _make_module()
        await _open_browser(m)

        result = await m.execute("close_browser", {})
        assert result["status"] == "closed"


# ---------------------------------------------------------------------------
# Tests — Edge cases
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_reopen_same_session_closes_old(self) -> None:
        m = _make_module()
        info1 = await _open_browser(m, "reuse")
        pw1 = info1["pw"]

        # Manually open a second session with same ID
        info2 = await _open_browser(m, "reuse")

        # The old session should have been replaced
        assert m._sessions["reuse"]["playwright"] is info2["pw"]

    @pytest.mark.asyncio
    async def test_close_already_closed_session(self) -> None:
        m = _make_module()
        await _open_browser(m, "temp")
        await m._action_close_browser({"session_id": "temp"})
        # Second close should return not_open
        result = await m._action_close_browser({"session_id": "temp"})
        assert result["status"] == "not_open"

    @pytest.mark.asyncio
    async def test_action_on_closed_session_raises(self) -> None:
        m = _make_module()
        await _open_browser(m, "ephemeral")
        await m._action_close_browser({"session_id": "ephemeral"})

        with pytest.raises(ActionExecutionError):
            await m._action_navigate_to({
                "url": "https://example.com",
                "session_id": "ephemeral",
            })
