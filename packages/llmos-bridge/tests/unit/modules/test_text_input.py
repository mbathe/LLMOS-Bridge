"""Unit tests — TextInputEngine (multi-strategy keyboard input)."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch, call

import pytest

from llmos_bridge.modules.gui.text_input import (
    DisplayServer,
    InputCapabilities,
    InputMethod,
    TextInputEngine,
)


# ---------------------------------------------------------------------------
# Capability detection
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCapabilityDetection:
    @patch("llmos_bridge.modules.gui.text_input.platform")
    @patch("llmos_bridge.modules.gui.text_input.shutil")
    @patch("llmos_bridge.modules.gui.text_input.os")
    def test_linux_x11_full_tools(self, mock_os, mock_shutil, mock_platform):
        mock_platform.system.return_value = "Linux"
        mock_os.environ.get = lambda k, d="": {"DISPLAY": ":0"}.get(k, d)
        mock_shutil.which = lambda cmd: f"/usr/bin/{cmd}" if cmd in ("xdotool", "xclip") else None

        engine = TextInputEngine()
        cap = engine.capabilities

        assert cap.display_server == DisplayServer.X11
        assert cap.has_xdotool is True
        assert cap.has_xclip is True
        assert cap.has_wtype is False

    @patch("llmos_bridge.modules.gui.text_input.platform")
    @patch("llmos_bridge.modules.gui.text_input.shutil")
    @patch("llmos_bridge.modules.gui.text_input.os")
    def test_linux_wayland(self, mock_os, mock_shutil, mock_platform):
        mock_platform.system.return_value = "Linux"
        mock_os.environ.get = lambda k, d="": {"WAYLAND_DISPLAY": "wayland-0"}.get(k, d)
        mock_shutil.which = lambda cmd: f"/usr/bin/{cmd}" if cmd in ("wtype", "wl-copy") else None

        engine = TextInputEngine()
        cap = engine.capabilities

        assert cap.display_server == DisplayServer.WAYLAND
        assert cap.has_wtype is True
        assert cap.has_wl_copy is True

    @patch("llmos_bridge.modules.gui.text_input.platform")
    def test_macos(self, mock_platform):
        mock_platform.system.return_value = "Darwin"
        engine = TextInputEngine()
        assert engine.capabilities.display_server == DisplayServer.MACOS

    @patch("llmos_bridge.modules.gui.text_input.platform")
    def test_windows(self, mock_platform):
        mock_platform.system.return_value = "Windows"
        engine = TextInputEngine()
        assert engine.capabilities.display_server == DisplayServer.WINDOWS


# ---------------------------------------------------------------------------
# Strategy order
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStrategyOrder:
    @patch("llmos_bridge.modules.gui.text_input.platform")
    @patch("llmos_bridge.modules.gui.text_input.shutil")
    @patch("llmos_bridge.modules.gui.text_input.os")
    def test_x11_clipboard_first(self, mock_os, mock_shutil, mock_platform):
        """Clipboard should be first strategy when xclip available."""
        mock_platform.system.return_value = "Linux"
        mock_os.environ.get = lambda k, d="": {"DISPLAY": ":0"}.get(k, d)
        mock_shutil.which = lambda cmd: f"/usr/bin/{cmd}" if cmd in ("xdotool", "xclip") else None

        engine = TextInputEngine()
        order = engine.strategy_order

        assert order[0] == InputMethod.CLIPBOARD
        assert order[1] == InputMethod.XDOTOOL
        assert InputMethod.PYAUTOGUI in order

    @patch("llmos_bridge.modules.gui.text_input.platform")
    @patch("llmos_bridge.modules.gui.text_input.shutil")
    @patch("llmos_bridge.modules.gui.text_input.os")
    def test_x11_no_clipboard_xdotool_first(self, mock_os, mock_shutil, mock_platform):
        """Without clipboard tools, xdotool should be first."""
        mock_platform.system.return_value = "Linux"
        mock_os.environ.get = lambda k, d="": {"DISPLAY": ":0"}.get(k, d)
        mock_shutil.which = lambda cmd: f"/usr/bin/{cmd}" if cmd == "xdotool" else None

        engine = TextInputEngine()
        order = engine.strategy_order

        assert order[0] == InputMethod.XDOTOOL
        assert InputMethod.CLIPBOARD not in order

    @patch("llmos_bridge.modules.gui.text_input.platform")
    @patch("llmos_bridge.modules.gui.text_input.shutil")
    @patch("llmos_bridge.modules.gui.text_input.os")
    def test_wayland_strategy_order(self, mock_os, mock_shutil, mock_platform):
        mock_platform.system.return_value = "Linux"
        mock_os.environ.get = lambda k, d="": {"WAYLAND_DISPLAY": "wayland-0"}.get(k, d)
        mock_shutil.which = lambda cmd: f"/usr/bin/{cmd}" if cmd in ("wl-copy", "wtype") else None

        engine = TextInputEngine()
        order = engine.strategy_order

        assert order[0] == InputMethod.CLIPBOARD
        assert order[1] == InputMethod.WTYPE

    @patch("llmos_bridge.modules.gui.text_input.platform")
    @patch("llmos_bridge.modules.gui.text_input.shutil")
    @patch("llmos_bridge.modules.gui.text_input.os")
    def test_pyautogui_always_last(self, mock_os, mock_shutil, mock_platform):
        mock_platform.system.return_value = "Linux"
        mock_os.environ.get = lambda k, d="": {"DISPLAY": ":0"}.get(k, d)
        mock_shutil.which = lambda cmd: None

        engine = TextInputEngine()
        assert engine.strategy_order[-1] == InputMethod.PYAUTOGUI


# ---------------------------------------------------------------------------
# Typing strategies
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTypingStrategies:
    @patch("llmos_bridge.modules.gui.text_input.subprocess")
    @patch("llmos_bridge.modules.gui.text_input.platform")
    @patch("llmos_bridge.modules.gui.text_input.shutil")
    @patch("llmos_bridge.modules.gui.text_input.os")
    def test_xdotool_typing(self, mock_os, mock_shutil, mock_platform, mock_subprocess):
        mock_platform.system.return_value = "Linux"
        mock_os.environ.get = lambda k, d="": {"DISPLAY": ":0"}.get(k, d)
        mock_shutil.which = lambda cmd: f"/usr/bin/{cmd}" if cmd == "xdotool" else None
        mock_subprocess.run.return_value = MagicMock(returncode=0)

        engine = TextInputEngine()
        method = engine.type_text("hello", method=InputMethod.XDOTOOL)

        assert method == InputMethod.XDOTOOL
        mock_subprocess.run.assert_called_once()
        args = mock_subprocess.run.call_args[0][0]
        assert args[0] == "xdotool"
        assert "hello" in args

    @patch("llmos_bridge.modules.gui.text_input.subprocess")
    @patch("llmos_bridge.modules.gui.text_input.time")
    @patch("llmos_bridge.modules.gui.text_input.platform")
    @patch("llmos_bridge.modules.gui.text_input.shutil")
    @patch("llmos_bridge.modules.gui.text_input.os")
    def test_clipboard_typing_x11(self, mock_os, mock_shutil, mock_platform, mock_time, mock_subprocess):
        mock_platform.system.return_value = "Linux"
        mock_os.environ.get = lambda k, d="": {"DISPLAY": ":0"}.get(k, d)
        mock_shutil.which = lambda cmd: f"/usr/bin/{cmd}" if cmd in ("xclip", "xdotool") else None
        mock_subprocess.run.return_value = MagicMock(returncode=0)

        mock_pyautogui = MagicMock()
        engine = TextInputEngine(pyautogui_module=mock_pyautogui)
        method = engine.type_text("Bonjour", method=InputMethod.CLIPBOARD)

        assert method == InputMethod.CLIPBOARD
        # Verify xclip was called to copy text.
        calls = mock_subprocess.run.call_args_list
        assert any("xclip" in str(c) for c in calls)
        # Verify Ctrl+V was sent.
        mock_pyautogui.hotkey.assert_called_with("ctrl", "v")

    @patch("llmos_bridge.modules.gui.text_input.subprocess")
    @patch("llmos_bridge.modules.gui.text_input.platform")
    @patch("llmos_bridge.modules.gui.text_input.shutil")
    @patch("llmos_bridge.modules.gui.text_input.os")
    def test_wtype_typing(self, mock_os, mock_shutil, mock_platform, mock_subprocess):
        mock_platform.system.return_value = "Linux"
        mock_os.environ.get = lambda k, d="": {"WAYLAND_DISPLAY": "wayland-0"}.get(k, d)
        mock_shutil.which = lambda cmd: f"/usr/bin/{cmd}" if cmd == "wtype" else None
        mock_subprocess.run.return_value = MagicMock(returncode=0)

        engine = TextInputEngine()
        method = engine.type_text("hello", method=InputMethod.WTYPE)

        assert method == InputMethod.WTYPE
        args = mock_subprocess.run.call_args[0][0]
        assert args[0] == "wtype"

    def test_pyautogui_typing(self):
        mock_pyautogui = MagicMock()

        engine = TextInputEngine(pyautogui_module=mock_pyautogui)
        method = engine.type_text("hello", method=InputMethod.PYAUTOGUI)

        assert method == InputMethod.PYAUTOGUI
        mock_pyautogui.typewrite.assert_called_once_with("hello", interval=0.05)

    def test_pyautogui_not_available(self):
        engine = TextInputEngine(pyautogui_module=None)
        with pytest.raises(RuntimeError, match="pyautogui not available"):
            engine.type_text("hello", method=InputMethod.PYAUTOGUI)


# ---------------------------------------------------------------------------
# Auto fallback
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAutoFallback:
    @patch("llmos_bridge.modules.gui.text_input.subprocess")
    @patch("llmos_bridge.modules.gui.text_input.platform")
    @patch("llmos_bridge.modules.gui.text_input.shutil")
    @patch("llmos_bridge.modules.gui.text_input.os")
    def test_fallback_on_failure(self, mock_os, mock_shutil, mock_platform, mock_subprocess):
        """If clipboard fails, falls back to xdotool."""
        mock_platform.system.return_value = "Linux"
        mock_os.environ.get = lambda k, d="": {"DISPLAY": ":0"}.get(k, d)
        mock_shutil.which = lambda cmd: f"/usr/bin/{cmd}" if cmd in ("xclip", "xdotool") else None

        # First call (xclip) fails, second call (xdotool) succeeds.
        mock_subprocess.run.side_effect = [
            subprocess.CalledProcessError(1, "xclip"),  # clipboard copy fails
            MagicMock(returncode=0),  # xdotool succeeds
        ]

        engine = TextInputEngine()
        method = engine.type_text("hello")

        assert method == InputMethod.XDOTOOL

    def test_all_fail_raises(self):
        """If all strategies fail, raises RuntimeError."""
        engine = TextInputEngine(pyautogui_module=None)
        # Force empty strategy order to only have pyautogui (which is None).
        engine._strategy_order = [InputMethod.PYAUTOGUI]

        with pytest.raises(RuntimeError, match="All text input strategies failed"):
            engine.type_text("hello")

    def test_empty_text_returns_immediately(self):
        engine = TextInputEngine()
        method = engine.type_text("")
        assert method in (InputMethod.AUTO, InputMethod.CLIPBOARD)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEdgeCases:
    @patch("llmos_bridge.modules.gui.text_input.subprocess")
    @patch("llmos_bridge.modules.gui.text_input.time")
    @patch("llmos_bridge.modules.gui.text_input.platform")
    @patch("llmos_bridge.modules.gui.text_input.shutil")
    @patch("llmos_bridge.modules.gui.text_input.os")
    def test_clipboard_clears_after_paste(self, mock_os, mock_shutil, mock_platform, mock_time, mock_subprocess):
        """Clipboard is cleared after pasting (security)."""
        mock_platform.system.return_value = "Linux"
        mock_os.environ.get = lambda k, d="": {"DISPLAY": ":0"}.get(k, d)
        mock_shutil.which = lambda cmd: f"/usr/bin/{cmd}" if cmd in ("xclip", "xdotool") else None
        mock_subprocess.run.return_value = MagicMock(returncode=0)

        mock_pyautogui = MagicMock()
        engine = TextInputEngine(pyautogui_module=mock_pyautogui)
        engine.type_text("secret", method=InputMethod.CLIPBOARD)

        # Should have at least 3 subprocess calls: copy, paste(via pyautogui), clear.
        # Actually: copy (xclip), clear (xclip with empty input).
        xclip_calls = [
            c for c in mock_subprocess.run.call_args_list
            if "xclip" in str(c)
        ]
        assert len(xclip_calls) >= 2  # copy + clear

    @patch("llmos_bridge.modules.gui.text_input.subprocess")
    @patch("llmos_bridge.modules.gui.text_input.platform")
    @patch("llmos_bridge.modules.gui.text_input.shutil")
    @patch("llmos_bridge.modules.gui.text_input.os")
    def test_unicode_text(self, mock_os, mock_shutil, mock_platform, mock_subprocess):
        """Unicode text is passed correctly to xdotool."""
        mock_platform.system.return_value = "Linux"
        mock_os.environ.get = lambda k, d="": {"DISPLAY": ":0"}.get(k, d)
        mock_shutil.which = lambda cmd: f"/usr/bin/{cmd}" if cmd == "xdotool" else None
        mock_subprocess.run.return_value = MagicMock(returncode=0)

        engine = TextInputEngine()
        engine.type_text("Héllo àçé", method=InputMethod.XDOTOOL)

        args = mock_subprocess.run.call_args[0][0]
        assert "Héllo àçé" in args

    def test_input_capabilities_dataclass(self):
        cap = InputCapabilities(display_server=DisplayServer.X11)
        assert cap.has_xdotool is False
        assert cap.has_xclip is False

    def test_input_method_enum_values(self):
        assert InputMethod.AUTO.value == "auto"
        assert InputMethod.CLIPBOARD.value == "clipboard"
        assert InputMethod.XDOTOOL.value == "xdotool"
