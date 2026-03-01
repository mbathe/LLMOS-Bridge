"""Unit tests — GUIModule (all PyAutoGUI/pytesseract calls mocked)."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch, PropertyMock

import pytest


# ---------------------------------------------------------------------------
# Helpers — mock pyautogui globally so GUIModule can be imported.
# ---------------------------------------------------------------------------

_mock_pyautogui = MagicMock()
_mock_pyautogui.FAILSAFE = True
_mock_pyautogui.position.return_value = (100, 200)


@pytest.fixture(autouse=True)
def _patch_pyautogui():
    """Patch pyautogui so GUIModule can be loaded without a real display.

    Also resets the TextInputEngine singleton so each test starts fresh,
    and patches text_input environment detection to use pyautogui fallback.
    """
    with patch.dict("sys.modules", {"pyautogui": _mock_pyautogui}):
        with patch("llmos_bridge.modules.gui.module.shutil") as mock_shutil:
            mock_shutil.which.return_value = None
            import llmos_bridge.modules.gui.module as gui_mod
            gui_mod._pyautogui = _mock_pyautogui
            gui_mod._text_input_engine = None  # Reset singleton
            # Patch text_input env detection so TextInputEngine sees no tools.
            with patch("llmos_bridge.modules.gui.text_input.shutil") as ti_shutil, \
                 patch("llmos_bridge.modules.gui.text_input.platform") as ti_platform, \
                 patch("llmos_bridge.modules.gui.text_input.os") as ti_os:
                ti_shutil.which = lambda cmd: None
                ti_platform.system.return_value = "Linux"
                ti_os.environ.get = lambda k, d="": d
                yield
    _mock_pyautogui.reset_mock()


def _make_module():
    from llmos_bridge.modules.gui import GUIModule
    m = GUIModule()
    return m


# ---------------------------------------------------------------------------
# Tests — Module basics
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestModuleBasics:
    def test_module_id(self) -> None:
        m = _make_module()
        assert m.MODULE_ID == "gui"

    def test_version(self) -> None:
        m = _make_module()
        assert m.VERSION == "1.0.0"

    def test_supported_platforms(self) -> None:
        from llmos_bridge.modules.base import Platform
        m = _make_module()
        assert Platform.LINUX in m.SUPPORTED_PLATFORMS


# ---------------------------------------------------------------------------
# Tests — Manifest
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestManifest:
    def test_manifest_module_id(self) -> None:
        m = _make_module()
        manifest = m.get_manifest()
        assert manifest.module_id == "gui"

    def test_manifest_action_count(self) -> None:
        m = _make_module()
        manifest = m.get_manifest()
        assert len(manifest.actions) == 13

    def test_manifest_action_names(self) -> None:
        m = _make_module()
        manifest = m.get_manifest()
        names = {a.name for a in manifest.actions}
        expected = {
            "click_position", "click_image", "double_click", "right_click",
            "type_text", "key_press", "scroll", "drag_drop",
            "find_on_screen", "get_screen_text",
            "get_window_info", "focus_window", "take_screenshot",
        }
        assert names == expected

    def test_manifest_has_dependencies(self) -> None:
        m = _make_module()
        manifest = m.get_manifest()
        assert "pyautogui" in manifest.dependencies

    def test_manifest_has_permissions(self) -> None:
        m = _make_module()
        manifest = m.get_manifest()
        assert "gui_control" in manifest.declared_permissions
        assert "screen_capture" in manifest.declared_permissions


# ---------------------------------------------------------------------------
# Tests — click_position
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestClickPosition:
    @pytest.mark.asyncio
    async def test_click_basic(self) -> None:
        m = _make_module()
        result = await m._action_click_position({"x": 100, "y": 200})

        _mock_pyautogui.click.assert_called_once_with(
            x=100, y=200, button="left", clicks=1, interval=0.1,
        )
        assert result["clicked"] is True
        assert result["x"] == 100
        assert result["y"] == 200

    @pytest.mark.asyncio
    async def test_click_right_button(self) -> None:
        m = _make_module()
        result = await m._action_click_position({
            "x": 50, "y": 60, "button": "right", "clicks": 2,
        })

        _mock_pyautogui.click.assert_called_once_with(
            x=50, y=60, button="right", clicks=2, interval=0.1,
        )
        assert result["button"] == "right"
        assert result["clicks"] == 2


# ---------------------------------------------------------------------------
# Tests — click_image
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestClickImage:
    @pytest.mark.asyncio
    async def test_click_image_found(self) -> None:
        m = _make_module()
        _mock_pyautogui.locateCenterOnScreen.return_value = (150, 250)

        result = await m._action_click_image({
            "image_path": "/tmp/button.png",
            "confidence": 0.9,
        })

        assert result["clicked"] is True
        assert result["x"] == 150
        assert result["y"] == 250
        _mock_pyautogui.click.assert_called_once_with(x=150, y=250, button="left")

    @pytest.mark.asyncio
    async def test_click_image_not_found(self) -> None:
        m = _make_module()
        _mock_pyautogui.locateCenterOnScreen.return_value = None

        with pytest.raises(RuntimeError, match="not found on screen"):
            await m._action_click_image({
                "image_path": "/tmp/missing.png",
                "timeout": 1,
            })


# ---------------------------------------------------------------------------
# Tests — double_click
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDoubleClick:
    @pytest.mark.asyncio
    async def test_double_click_coords(self) -> None:
        m = _make_module()
        result = await m._action_double_click({"x": 10, "y": 20})

        _mock_pyautogui.doubleClick.assert_called_once_with(x=10, y=20)
        assert result["double_clicked"] is True

    @pytest.mark.asyncio
    async def test_double_click_current_position(self) -> None:
        m = _make_module()
        _mock_pyautogui.position.return_value = (100, 200)

        result = await m._action_double_click({})

        _mock_pyautogui.doubleClick.assert_called_once_with(x=100, y=200)

    @pytest.mark.asyncio
    async def test_double_click_image(self) -> None:
        m = _make_module()
        _mock_pyautogui.locateCenterOnScreen.return_value = (300, 400)

        result = await m._action_double_click({"image_path": "/tmp/icon.png"})

        _mock_pyautogui.doubleClick.assert_called_once_with(x=300, y=400)
        assert result["x"] == 300


# ---------------------------------------------------------------------------
# Tests — right_click
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRightClick:
    @pytest.mark.asyncio
    async def test_right_click_coords(self) -> None:
        m = _make_module()
        result = await m._action_right_click({"x": 50, "y": 60})

        _mock_pyautogui.rightClick.assert_called_once_with(x=50, y=60)
        assert result["right_clicked"] is True

    @pytest.mark.asyncio
    async def test_right_click_current_position(self) -> None:
        m = _make_module()
        _mock_pyautogui.position.return_value = (99, 88)

        result = await m._action_right_click({})

        _mock_pyautogui.rightClick.assert_called_once_with(x=99, y=88)


# ---------------------------------------------------------------------------
# Tests — type_text
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTypeText:
    @pytest.mark.asyncio
    async def test_type_basic(self) -> None:
        m = _make_module()
        result = await m._action_type_text({"text": "Hello World"})

        # TextInputEngine uses pyautogui.typewrite as last resort (no xdotool in tests).
        _mock_pyautogui.typewrite.assert_called_once_with("Hello World", interval=0.05)
        assert result["typed"] is True
        assert result["length"] == 11
        assert "method" in result

    @pytest.mark.asyncio
    async def test_type_with_clear(self) -> None:
        m = _make_module()
        result = await m._action_type_text({
            "text": "replacement",
            "clear_first": True,
        })

        _mock_pyautogui.hotkey.assert_called_once_with("ctrl", "a")
        _mock_pyautogui.press.assert_called_once_with("delete")
        _mock_pyautogui.typewrite.assert_called_once_with("replacement", interval=0.05)


# ---------------------------------------------------------------------------
# Tests — _type_text_native (keyboard layout fix)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTextInputEngineIntegration:
    """Tests for TextInputEngine integration in GUIModule."""

    def test_uses_xdotool_on_linux(self) -> None:
        """When xdotool is available on Linux, TextInputEngine uses it."""
        from llmos_bridge.modules.gui.text_input import TextInputEngine, InputMethod

        with patch("llmos_bridge.modules.gui.text_input.platform") as mock_platform, \
             patch("llmos_bridge.modules.gui.text_input.shutil") as mock_shutil, \
             patch("llmos_bridge.modules.gui.text_input.subprocess") as mock_subprocess, \
             patch("llmos_bridge.modules.gui.text_input.os") as mock_os:
            mock_platform.system.return_value = "Linux"
            mock_os.environ.get = lambda k, d="": {"DISPLAY": ":0"}.get(k, d)
            mock_shutil.which = lambda cmd: f"/usr/bin/{cmd}" if cmd == "xdotool" else None
            mock_subprocess.run.return_value = MagicMock(returncode=0)

            engine = TextInputEngine()
            method = engine.type_text("bonjour", interval=0.05)

            assert method == InputMethod.XDOTOOL
            mock_subprocess.run.assert_called_once()
            args = mock_subprocess.run.call_args[0][0]
            assert args[0] == "xdotool"
            assert "bonjour" in args

    def test_falls_back_to_pyautogui_no_xdotool(self) -> None:
        """When no native tools available, TextInputEngine uses pyautogui."""
        from llmos_bridge.modules.gui.text_input import TextInputEngine, InputMethod

        mock_pag = MagicMock()
        engine = TextInputEngine(pyautogui_module=mock_pag)
        method = engine.type_text("hello", method=InputMethod.PYAUTOGUI)

        assert method == InputMethod.PYAUTOGUI
        mock_pag.typewrite.assert_called_once_with("hello", interval=0.05)

    def test_engine_method_returned_in_result(self) -> None:
        """type_text result includes the method used."""
        from llmos_bridge.modules.gui.text_input import TextInputEngine, InputMethod

        mock_pag = MagicMock()
        engine = TextInputEngine(pyautogui_module=mock_pag)
        method = engine.type_text("test", method=InputMethod.PYAUTOGUI)
        assert method == InputMethod.PYAUTOGUI


# ---------------------------------------------------------------------------
# Tests — key_press
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestKeyPress:
    @pytest.mark.asyncio
    async def test_single_key(self) -> None:
        m = _make_module()
        result = await m._action_key_press({"keys": ["enter"]})

        _mock_pyautogui.press.assert_called_once_with("enter", presses=1, interval=0.1)
        assert result["pressed"] is True

    @pytest.mark.asyncio
    async def test_hotkey(self) -> None:
        m = _make_module()
        result = await m._action_key_press({"keys": ["ctrl", "c"]})

        _mock_pyautogui.hotkey.assert_called_once_with("ctrl", "c")

    @pytest.mark.asyncio
    async def test_multiple_presses(self) -> None:
        m = _make_module()
        result = await m._action_key_press({"keys": ["tab"], "presses": 3})

        _mock_pyautogui.press.assert_called_once_with("tab", presses=3, interval=0.1)


# ---------------------------------------------------------------------------
# Tests — scroll
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestScroll:
    @pytest.mark.asyncio
    async def test_scroll_up(self) -> None:
        m = _make_module()
        result = await m._action_scroll({"clicks": 5})

        _mock_pyautogui.scroll.assert_called_once_with(clicks=5)
        assert result["direction"] == "up"
        assert result["scrolled"] is True

    @pytest.mark.asyncio
    async def test_scroll_down(self) -> None:
        m = _make_module()
        result = await m._action_scroll({"clicks": -3})

        _mock_pyautogui.scroll.assert_called_once_with(clicks=-3)
        assert result["direction"] == "down"

    @pytest.mark.asyncio
    async def test_scroll_at_position(self) -> None:
        m = _make_module()
        result = await m._action_scroll({"clicks": 2, "x": 100, "y": 200})

        _mock_pyautogui.scroll.assert_called_once_with(clicks=2, x=100, y=200)


# ---------------------------------------------------------------------------
# Tests — drag_drop
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDragDrop:
    @pytest.mark.asyncio
    async def test_drag_basic(self) -> None:
        m = _make_module()
        result = await m._action_drag_drop({
            "from_x": 10, "from_y": 20,
            "to_x": 110, "to_y": 120,
        })

        _mock_pyautogui.moveTo.assert_called_once_with(10, 20)
        _mock_pyautogui.drag.assert_called_once_with(100, 100, duration=0.5)
        assert result["dragged"] is True
        assert result["from"] == {"x": 10, "y": 20}
        assert result["to"] == {"x": 110, "y": 120}


# ---------------------------------------------------------------------------
# Tests — find_on_screen
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFindOnScreen:
    @pytest.mark.asyncio
    async def test_found(self) -> None:
        m = _make_module()
        # locateOnScreen returns a Box(left, top, width, height)
        box = MagicMock()
        box.__getitem__ = lambda self, i: [50, 60, 100, 80][i]
        _mock_pyautogui.locateOnScreen.return_value = box
        _mock_pyautogui.center.return_value = (100, 100)

        result = await m._action_find_on_screen({
            "image_path": "/tmp/target.png",
            "timeout": 1,
        })

        assert result["found"] is True
        assert result["x"] == 100
        assert result["y"] == 100

    @pytest.mark.asyncio
    async def test_not_found(self) -> None:
        m = _make_module()
        _mock_pyautogui.locateOnScreen.return_value = None

        result = await m._action_find_on_screen({
            "image_path": "/tmp/ghost.png",
            "timeout": 1,
        })

        assert result["found"] is False


# ---------------------------------------------------------------------------
# Tests — get_screen_text
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetScreenText:
    @pytest.mark.asyncio
    async def test_ocr_basic(self) -> None:
        m = _make_module()
        mock_screenshot = MagicMock()
        _mock_pyautogui.screenshot.return_value = mock_screenshot

        mock_pytesseract = MagicMock()
        mock_pytesseract.image_to_string.return_value = "  Hello OCR  \n"

        with patch.dict("sys.modules", {"pytesseract": mock_pytesseract}):
            result = await m._action_get_screen_text({"lang": "eng"})

        assert result["text"] == "Hello OCR"
        assert result["lang"] == "eng"

    @pytest.mark.asyncio
    async def test_ocr_with_region(self) -> None:
        m = _make_module()
        mock_screenshot = MagicMock()
        _mock_pyautogui.screenshot.return_value = mock_screenshot

        mock_pytesseract = MagicMock()
        mock_pytesseract.image_to_string.return_value = "Region text"

        with patch.dict("sys.modules", {"pytesseract": mock_pytesseract}):
            result = await m._action_get_screen_text({
                "region": [10, 20, 300, 200],
                "lang": "fra",
            })

        assert result["lang"] == "fra"
        _mock_pyautogui.screenshot.assert_called_once()


# ---------------------------------------------------------------------------
# Tests — take_screenshot
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTakeScreenshot:
    @pytest.mark.asyncio
    async def test_screenshot_to_base64(self) -> None:
        m = _make_module()
        mock_img = MagicMock()
        mock_img.width = 1920
        mock_img.height = 1080

        def _save(buf, format=None):
            buf.write(b"\x89PNG" + b"\x00" * 100)

        mock_img.save = _save
        _mock_pyautogui.screenshot.return_value = mock_img

        result = await m._action_take_screenshot({})

        assert "base64" in result
        assert result["width"] == 1920
        assert result["height"] == 1080

    @pytest.mark.asyncio
    async def test_screenshot_to_file(self, tmp_path: Path) -> None:
        m = _make_module()
        mock_img = MagicMock()
        mock_img.width = 800
        mock_img.height = 600
        _mock_pyautogui.screenshot.return_value = mock_img

        out = str(tmp_path / "shot.png")
        result = await m._action_take_screenshot({"output_path": out})

        assert result["saved_to"] == out
        mock_img.save.assert_called_once_with(out)


# ---------------------------------------------------------------------------
# Tests — get_window_info
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetWindowInfo:
    @pytest.mark.asyncio
    async def test_get_active_window(self) -> None:
        m = _make_module()
        mock_gw = MagicMock()
        active = MagicMock()
        active.title = "Terminal"
        active.left = 0
        active.top = 0
        active.width = 800
        active.height = 600
        mock_gw.getActiveWindow.return_value = active

        with patch.dict("sys.modules", {"pygetwindow": mock_gw}):
            result = await m._action_get_window_info({})

        assert result["count"] == 1
        assert result["windows"][0]["title"] == "Terminal"

    @pytest.mark.asyncio
    async def test_get_all_windows(self) -> None:
        m = _make_module()
        mock_gw = MagicMock()
        w1 = MagicMock(title="App1", left=0, top=0, width=400, height=300)
        w2 = MagicMock(title="App2", left=400, top=0, width=400, height=300)
        mock_gw.getAllWindows.return_value = [w1, w2]

        with patch.dict("sys.modules", {"pygetwindow": mock_gw}):
            result = await m._action_get_window_info({"include_all": True})

        assert result["count"] == 2

    @pytest.mark.asyncio
    async def test_filter_by_title(self) -> None:
        m = _make_module()
        mock_gw = MagicMock()
        w1 = MagicMock(title="Firefox - Home", left=0, top=0, width=800, height=600)
        w2 = MagicMock(title="Terminal", left=0, top=0, width=400, height=300)
        mock_gw.getAllWindows.return_value = [w1, w2]

        with patch.dict("sys.modules", {"pygetwindow": mock_gw}):
            result = await m._action_get_window_info({"title_pattern": "Firefox"})

        assert result["count"] == 1
        assert result["windows"][0]["title"] == "Firefox - Home"

    @pytest.mark.asyncio
    async def test_pygetwindow_not_installed(self) -> None:
        m = _make_module()

        # Remove pygetwindow from sys.modules if present
        with patch.dict("sys.modules", {"pygetwindow": None}):
            result = await m._action_get_window_info({})

        assert result["windows"] == []
        assert "error" in result


# ---------------------------------------------------------------------------
# Tests — focus_window
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFocusWindow:
    @pytest.mark.asyncio
    async def test_focus_found(self) -> None:
        m = _make_module()
        mock_gw = MagicMock()
        target = MagicMock(title="VS Code")
        mock_gw.getAllWindows.return_value = [target]

        with patch.dict("sys.modules", {"pygetwindow": mock_gw}):
            result = await m._action_focus_window({
                "title_pattern": "VS Code",
                "timeout": 1,
            })

        assert result["focused"] is True
        assert result["title"] == "VS Code"
        target.activate.assert_called_once()

    @pytest.mark.asyncio
    async def test_focus_not_found(self) -> None:
        m = _make_module()
        mock_gw = MagicMock()
        mock_gw.getAllWindows.return_value = []

        with patch.dict("sys.modules", {"pygetwindow": mock_gw}):
            result = await m._action_focus_window({
                "title_pattern": "NonExistent",
                "timeout": 1,
            })

        assert result["focused"] is False


# ---------------------------------------------------------------------------
# Tests — BaseModule.execute() dispatch
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExecuteDispatch:
    @pytest.mark.asyncio
    async def test_execute_click_position(self) -> None:
        m = _make_module()
        result = await m.execute("click_position", {"x": 10, "y": 20})
        assert result["clicked"] is True

    @pytest.mark.asyncio
    async def test_execute_type_text(self) -> None:
        m = _make_module()
        result = await m.execute("type_text", {"text": "abc"})
        assert result["typed"] is True

    @pytest.mark.asyncio
    async def test_execute_key_press(self) -> None:
        m = _make_module()
        result = await m.execute("key_press", {"keys": ["enter"]})
        assert result["pressed"] is True
