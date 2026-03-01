"""Multi-strategy text input engine — layout-agnostic, Wayland-aware.

Strategy chain (tried in order for AUTO):
  1. clipboard_paste  — xclip/xsel/wl-copy + Ctrl+V (most reliable)
  2. xdotool          — X11 native typing (respects XKB layout)
  3. wtype            — Wayland native typing
  4. ydotool          — Kernel-level (/dev/uinput), X11+Wayland
  5. pyautogui        — US-QWERTY fallback (last resort)

The engine auto-detects the display server (X11 vs Wayland) at init time
and selects the best available strategy.  Override via ``method`` param.

Every agent using ``gui__type_text`` gets correct keyboard layout handling
automatically — this is a daemon-level improvement, not SDK-specific.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class DisplayServer(str, Enum):
    """Detected display server type."""

    X11 = "x11"
    WAYLAND = "wayland"
    MACOS = "macos"
    WINDOWS = "windows"
    UNKNOWN = "unknown"


class InputMethod(str, Enum):
    """Text input method."""

    AUTO = "auto"
    CLIPBOARD = "clipboard"
    XDOTOOL = "xdotool"
    WTYPE = "wtype"
    YDOTOOL = "ydotool"
    PYAUTOGUI = "pyautogui"


@dataclass
class InputCapabilities:
    """Detected input capabilities for the current environment."""

    display_server: DisplayServer
    has_xdotool: bool = False
    has_wtype: bool = False
    has_ydotool: bool = False
    has_xclip: bool = False
    has_xsel: bool = False
    has_wl_copy: bool = False
    has_pyautogui: bool = False


class TextInputEngine:
    """Layout-agnostic, Wayland-aware text input.

    Usage::

        engine = TextInputEngine()
        method = engine.type_text("Bonjour AZERTY!")
        # method == InputMethod.CLIPBOARD (or whatever worked)

        # Force a specific method:
        engine.type_text("hello", method=InputMethod.XDOTOOL)
    """

    def __init__(self, pyautogui_module: Any = None) -> None:
        self._pyautogui = pyautogui_module
        self._capabilities = self._detect_capabilities()
        self._strategy_order = self._build_strategy_order()

    @property
    def capabilities(self) -> InputCapabilities:
        """Return detected input capabilities."""
        return self._capabilities

    @property
    def strategy_order(self) -> list[InputMethod]:
        """Return the ordered list of strategies that will be tried."""
        return list(self._strategy_order)

    def type_text(
        self,
        text: str,
        interval: float = 0.05,
        method: InputMethod = InputMethod.AUTO,
    ) -> InputMethod:
        """Type text using the best available method.

        Returns the method actually used.

        Raises:
            RuntimeError: If all strategies fail.
        """
        if not text:
            return method if method != InputMethod.AUTO else InputMethod.CLIPBOARD

        if method != InputMethod.AUTO:
            self._dispatch(method, text, interval)
            return method

        # AUTO: try strategies in order until one succeeds.
        errors: list[str] = []
        for strategy in self._strategy_order:
            try:
                self._dispatch(strategy, text, interval)
                return strategy
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{strategy.value}: {exc}")

        raise RuntimeError(
            f"All text input strategies failed: {'; '.join(errors)}"
        )

    # ------------------------------------------------------------------
    # Environment detection
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_capabilities() -> InputCapabilities:
        """Detect display server and available tools."""
        system = platform.system()

        if system == "Darwin":
            return InputCapabilities(display_server=DisplayServer.MACOS)
        if system == "Windows":
            return InputCapabilities(display_server=DisplayServer.WINDOWS)
        if system != "Linux":
            return InputCapabilities(display_server=DisplayServer.UNKNOWN)

        # Detect display server on Linux.
        wayland_display = os.environ.get("WAYLAND_DISPLAY", "")
        x11_display = os.environ.get("DISPLAY", "")

        if wayland_display:
            ds = DisplayServer.WAYLAND
        elif x11_display:
            ds = DisplayServer.X11
        else:
            ds = DisplayServer.UNKNOWN

        return InputCapabilities(
            display_server=ds,
            has_xdotool=shutil.which("xdotool") is not None,
            has_wtype=shutil.which("wtype") is not None,
            has_ydotool=shutil.which("ydotool") is not None,
            has_xclip=shutil.which("xclip") is not None,
            has_xsel=shutil.which("xsel") is not None,
            has_wl_copy=shutil.which("wl-copy") is not None,
        )

    def _build_strategy_order(self) -> list[InputMethod]:
        """Build ordered list of strategies based on capabilities."""
        cap = self._capabilities
        strategies: list[InputMethod] = []

        # 1. Clipboard paste — most reliable, layout-agnostic.
        if cap.display_server == DisplayServer.X11 and (cap.has_xclip or cap.has_xsel):
            strategies.append(InputMethod.CLIPBOARD)
        elif cap.display_server == DisplayServer.WAYLAND and cap.has_wl_copy:
            strategies.append(InputMethod.CLIPBOARD)

        # 2. Native display server tools.
        if cap.display_server == DisplayServer.X11 and cap.has_xdotool:
            strategies.append(InputMethod.XDOTOOL)
        if cap.display_server == DisplayServer.WAYLAND and cap.has_wtype:
            strategies.append(InputMethod.WTYPE)

        # 3. ydotool (kernel-level, works on both).
        if cap.has_ydotool:
            strategies.append(InputMethod.YDOTOOL)

        # 4. xdotool on Wayland (may work via XWayland).
        if cap.display_server == DisplayServer.WAYLAND and cap.has_xdotool:
            if InputMethod.XDOTOOL not in strategies:
                strategies.append(InputMethod.XDOTOOL)

        # 5. pyautogui as last resort.
        strategies.append(InputMethod.PYAUTOGUI)

        return strategies

    # ------------------------------------------------------------------
    # Strategy dispatch
    # ------------------------------------------------------------------

    def _dispatch(self, method: InputMethod, text: str, interval: float) -> None:
        """Dispatch to the appropriate typing strategy."""
        dispatch_map = {
            InputMethod.CLIPBOARD: self._type_clipboard,
            InputMethod.XDOTOOL: self._type_xdotool,
            InputMethod.WTYPE: self._type_wtype,
            InputMethod.YDOTOOL: self._type_ydotool,
            InputMethod.PYAUTOGUI: self._type_pyautogui,
        }
        fn = dispatch_map.get(method)
        if fn is None:
            raise ValueError(f"Unknown input method: {method}")
        fn(text, interval)

    # ------------------------------------------------------------------
    # Strategy implementations
    # ------------------------------------------------------------------

    def _type_clipboard(self, text: str, interval: float) -> None:
        """Copy to clipboard, paste with Ctrl+V, clear clipboard.

        Works on both X11 and Wayland. Layout-agnostic because the
        clipboard contains the actual Unicode text, not keycodes.
        """
        cap = self._capabilities

        # Step 1: Copy text to clipboard.
        if cap.display_server == DisplayServer.WAYLAND and cap.has_wl_copy:
            subprocess.run(
                ["wl-copy", "--", text],
                check=True, timeout=5,
            )
        elif cap.has_xclip:
            subprocess.run(
                ["xclip", "-selection", "clipboard"],
                input=text.encode("utf-8"),
                check=True, timeout=5,
            )
        elif cap.has_xsel:
            subprocess.run(
                ["xsel", "--clipboard", "--input"],
                input=text.encode("utf-8"),
                check=True, timeout=5,
            )
        else:
            raise RuntimeError("No clipboard tool available (xclip, xsel, wl-copy)")

        # Step 2: Small delay for clipboard to propagate.
        time.sleep(0.05)

        # Step 3: Paste with Ctrl+V.
        if self._pyautogui is not None:
            self._pyautogui.hotkey("ctrl", "v")
        elif cap.has_xdotool:
            subprocess.run(
                ["xdotool", "key", "--clearmodifiers", "ctrl+v"],
                check=True, timeout=5,
            )
        else:
            raise RuntimeError("Cannot send Ctrl+V (no pyautogui or xdotool)")

        # Step 4: Small delay for paste to complete.
        time.sleep(0.1)

        # Step 5: Clear clipboard (security — don't leave text in clipboard).
        try:
            if cap.display_server == DisplayServer.WAYLAND and cap.has_wl_copy:
                subprocess.run(
                    ["wl-copy", "--clear"],
                    timeout=2, check=False,
                )
            elif cap.has_xclip:
                subprocess.run(
                    ["xclip", "-selection", "clipboard"],
                    input=b"",
                    timeout=2, check=False,
                )
        except Exception:  # noqa: BLE001
            pass  # Non-critical — don't fail on clipboard clear.

    def _type_xdotool(self, text: str, interval: float) -> None:
        """Type via xdotool (X11 native, respects XKB layout)."""
        delay_ms = max(1, int(interval * 1000))
        subprocess.run(
            ["xdotool", "type", "--clearmodifiers",
             "--delay", str(delay_ms), "--", text],
            check=True, timeout=max(30, len(text) * interval + 5),
        )

    def _type_wtype(self, text: str, interval: float) -> None:
        """Type via wtype (Wayland native)."""
        delay_ms = max(1, int(interval * 1000))
        subprocess.run(
            ["wtype", "-d", str(delay_ms), "--", text],
            check=True, timeout=max(30, len(text) * interval + 5),
        )

    def _type_ydotool(self, text: str, interval: float) -> None:
        """Type via ydotool (kernel-level uinput, works on X11+Wayland)."""
        delay_ms = max(1, int(interval * 1000))
        subprocess.run(
            ["ydotool", "type", "--key-delay", str(delay_ms), "--", text],
            check=True, timeout=max(30, len(text) * interval + 5),
        )

    def _type_pyautogui(self, text: str, interval: float) -> None:
        """Type via pyautogui (US-QWERTY fallback, last resort)."""
        if self._pyautogui is None:
            raise RuntimeError("pyautogui not available")
        self._pyautogui.typewrite(text, interval=interval)
