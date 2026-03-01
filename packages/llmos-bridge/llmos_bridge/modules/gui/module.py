"""GUI automation module — Implementation.

Covers:
  - Mouse actions: click, double-click, right-click, drag & drop, scroll
  - Keyboard actions: type text, key press / hotkey
  - Image-based interaction: find on screen, click on image match
  - Screen: take screenshot, get text via OCR
  - Window management: get window info, focus window

Requires ``pyautogui`` (optional extra):
  pip install pyautogui

Optional for OCR: ``pytesseract`` + Tesseract binary
Optional for image matching: ``opencv-python``

All operations are blocking → wrapped with ``asyncio.to_thread()``.
"""

from __future__ import annotations

import asyncio
import base64
import platform
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from llmos_bridge.exceptions import ActionExecutionError
from llmos_bridge.modules.base import BaseModule, Platform
from llmos_bridge.modules.manifest import ActionSpec, ModuleManifest, ParamSpec
from llmos_bridge.security.decorators import rate_limited, requires_permission
from llmos_bridge.security.models import Permission
from llmos_bridge.protocol.params.gui import (
    ClickImageParams,
    ClickPositionParams,
    DoubleClickParams,
    DragDropParams,
    FindOnScreenParams,
    FocusWindowParams,
    GetScreenTextParams,
    GetWindowInfoParams,
    KeyPressParams,
    RightClickParams,
    ScrollParams,
    TakeScreenshotParams,
    TypeTextParams,
)

# Lazy imports — set by _check_dependencies
_pyautogui: Any = None
_text_input_engine: Any = None


def _get_text_engine() -> Any:
    """Get or create the TextInputEngine singleton."""
    global _text_input_engine  # noqa: PLW0603
    if _text_input_engine is None:
        from llmos_bridge.modules.gui.text_input import TextInputEngine

        _text_input_engine = TextInputEngine(pyautogui_module=_pyautogui)
    return _text_input_engine


class GUIModule(BaseModule):
    MODULE_ID = "gui"
    VERSION = "1.0.0"
    SUPPORTED_PLATFORMS = [Platform.LINUX, Platform.MACOS, Platform.WINDOWS]

    def _check_dependencies(self) -> None:
        global _pyautogui
        try:
            import pyautogui  # noqa: PLC0415

            _pyautogui = pyautogui
            # Disable PyAutoGUI's fail-safe (move to top-left to abort).
            # The daemon already has its own safety mechanisms.
            pyautogui.FAILSAFE = False
        except ImportError as exc:
            from llmos_bridge.exceptions import ModuleLoadError  # noqa: PLC0415

            raise ModuleLoadError(
                "gui",
                "pyautogui is required: pip install pyautogui",
            ) from exc

    # ------------------------------------------------------------------
    # Actions — Mouse
    # ------------------------------------------------------------------

    @requires_permission(Permission.KEYBOARD, reason="Simulates mouse click")
    @rate_limited(calls_per_minute=120)
    async def _action_click_position(self, params: dict[str, Any]) -> dict[str, Any]:
        p = ClickPositionParams.model_validate(params)

        def _inner() -> dict[str, Any]:
            _pyautogui.click(
                x=p.x, y=p.y, button=p.button,
                clicks=p.clicks, interval=p.interval,
            )
            return {
                "x": p.x,
                "y": p.y,
                "button": p.button,
                "clicks": p.clicks,
                "clicked": True,
            }

        return await asyncio.to_thread(_inner)

    @requires_permission(Permission.KEYBOARD, reason="Simulates mouse click")
    async def _action_click_image(self, params: dict[str, Any]) -> dict[str, Any]:
        p = ClickImageParams.model_validate(params)

        def _inner() -> dict[str, Any]:
            location = None
            deadline = time.monotonic() + p.timeout
            while time.monotonic() < deadline:
                try:
                    location = _pyautogui.locateCenterOnScreen(
                        p.image_path, confidence=p.confidence,
                    )
                except Exception:
                    pass
                if location is not None:
                    break
                time.sleep(0.5)

            if location is None:
                raise RuntimeError(
                    f"Image '{p.image_path}' not found on screen within {p.timeout}s."
                )

            _pyautogui.click(x=location[0], y=location[1], button=p.button)
            return {
                "image_path": p.image_path,
                "x": location[0],
                "y": location[1],
                "button": p.button,
                "clicked": True,
            }

        return await asyncio.to_thread(_inner)

    @requires_permission(Permission.KEYBOARD, reason="Simulates mouse click")
    async def _action_double_click(self, params: dict[str, Any]) -> dict[str, Any]:
        p = DoubleClickParams.model_validate(params)

        def _inner() -> dict[str, Any]:
            if p.image_path:
                location = _pyautogui.locateCenterOnScreen(
                    p.image_path, confidence=p.confidence,
                )
                if location is None:
                    raise RuntimeError(f"Image '{p.image_path}' not found on screen.")
                x, y = location[0], location[1]
            elif p.x is not None and p.y is not None:
                x, y = p.x, p.y
            else:
                pos = _pyautogui.position()
                x, y = pos[0], pos[1]

            _pyautogui.doubleClick(x=x, y=y)
            return {"x": x, "y": y, "double_clicked": True}

        return await asyncio.to_thread(_inner)

    @requires_permission(Permission.KEYBOARD, reason="Simulates mouse click")
    async def _action_right_click(self, params: dict[str, Any]) -> dict[str, Any]:
        p = RightClickParams.model_validate(params)

        def _inner() -> dict[str, Any]:
            if p.image_path:
                location = _pyautogui.locateCenterOnScreen(p.image_path)
                if location is None:
                    raise RuntimeError(f"Image '{p.image_path}' not found on screen.")
                x, y = location[0], location[1]
            elif p.x is not None and p.y is not None:
                x, y = p.x, p.y
            else:
                pos = _pyautogui.position()
                x, y = pos[0], pos[1]

            _pyautogui.rightClick(x=x, y=y)
            return {"x": x, "y": y, "right_clicked": True}

        return await asyncio.to_thread(_inner)

    @requires_permission(Permission.KEYBOARD, reason="Simulates scroll")
    async def _action_scroll(self, params: dict[str, Any]) -> dict[str, Any]:
        p = ScrollParams.model_validate(params)

        def _inner() -> dict[str, Any]:
            kwargs: dict[str, Any] = {"clicks": p.clicks}
            if p.x is not None and p.y is not None:
                kwargs["x"] = p.x
                kwargs["y"] = p.y
            _pyautogui.scroll(**kwargs)
            return {
                "clicks": p.clicks,
                "direction": "up" if p.clicks > 0 else "down",
                "scrolled": True,
            }

        return await asyncio.to_thread(_inner)

    @requires_permission(Permission.KEYBOARD, reason="Simulates mouse drag")
    async def _action_drag_drop(self, params: dict[str, Any]) -> dict[str, Any]:
        p = DragDropParams.model_validate(params)

        def _inner() -> dict[str, Any]:
            _pyautogui.moveTo(p.from_x, p.from_y)
            _pyautogui.drag(
                p.to_x - p.from_x,
                p.to_y - p.from_y,
                duration=p.duration,
            )
            return {
                "from": {"x": p.from_x, "y": p.from_y},
                "to": {"x": p.to_x, "y": p.to_y},
                "duration": p.duration,
                "dragged": True,
            }

        return await asyncio.to_thread(_inner)

    # ------------------------------------------------------------------
    # Actions — Keyboard
    # ------------------------------------------------------------------

    @requires_permission(Permission.KEYBOARD, reason="Simulates keyboard input")
    @rate_limited(calls_per_minute=120)
    async def _action_type_text(self, params: dict[str, Any]) -> dict[str, Any]:
        p = TypeTextParams.model_validate(params)

        def _inner() -> dict[str, Any]:
            if p.clear_first:
                _pyautogui.hotkey("ctrl", "a")
                _pyautogui.press("delete")

            from llmos_bridge.modules.gui.text_input import InputMethod

            engine = _get_text_engine()
            method_used = engine.type_text(
                p.text, interval=p.interval, method=InputMethod(p.method),
            )
            return {
                "text": p.text,
                "length": len(p.text),
                "typed": True,
                "method": method_used.value,
            }

        return await asyncio.to_thread(_inner)

    @requires_permission(Permission.KEYBOARD, reason="Simulates key press")
    @rate_limited(calls_per_minute=120)
    async def _action_key_press(self, params: dict[str, Any]) -> dict[str, Any]:
        p = KeyPressParams.model_validate(params)

        def _inner() -> dict[str, Any]:
            if len(p.keys) == 1:
                _pyautogui.press(p.keys[0], presses=p.presses, interval=p.interval)
            else:
                for _ in range(p.presses):
                    _pyautogui.hotkey(*p.keys)
            return {
                "keys": p.keys,
                "presses": p.presses,
                "pressed": True,
            }

        return await asyncio.to_thread(_inner)

    # ------------------------------------------------------------------
    # Actions — Screen / Vision
    # ------------------------------------------------------------------

    async def _action_find_on_screen(self, params: dict[str, Any]) -> dict[str, Any]:
        p = FindOnScreenParams.model_validate(params)

        def _inner() -> dict[str, Any]:
            location = None
            deadline = time.monotonic() + p.timeout
            while time.monotonic() < deadline:
                try:
                    location = _pyautogui.locateOnScreen(
                        p.image_path,
                        confidence=p.confidence,
                        grayscale=p.grayscale,
                    )
                except Exception:
                    pass
                if location is not None:
                    break
                time.sleep(0.5)

            if location is None:
                return {
                    "found": False,
                    "image_path": p.image_path,
                }

            center = _pyautogui.center(location)
            return {
                "found": True,
                "image_path": p.image_path,
                "x": center[0],
                "y": center[1],
                "region": {
                    "left": location[0],
                    "top": location[1],
                    "width": location[2],
                    "height": location[3],
                },
            }

        return await asyncio.to_thread(_inner)

    async def _action_get_screen_text(self, params: dict[str, Any]) -> dict[str, Any]:
        p = GetScreenTextParams.model_validate(params)

        def _inner() -> dict[str, Any]:
            try:
                import pytesseract  # noqa: PLC0415
            except ImportError as exc:
                raise RuntimeError(
                    "pytesseract is required for OCR: pip install pytesseract"
                ) from exc

            screenshot = _pyautogui.screenshot(region=p.region)
            text = pytesseract.image_to_string(screenshot, lang=p.lang)
            return {
                "text": text.strip(),
                "region": p.region,
                "lang": p.lang,
            }

        return await asyncio.to_thread(_inner)

    @requires_permission(Permission.SCREEN_CAPTURE, reason="Captures screen content")
    async def _action_take_screenshot(self, params: dict[str, Any]) -> dict[str, Any]:
        p = TakeScreenshotParams.model_validate(params)

        def _inner() -> dict[str, Any]:
            screenshot = _pyautogui.screenshot(region=p.region)

            if p.output_path:
                path = Path(p.output_path)
                path.parent.mkdir(parents=True, exist_ok=True)
                screenshot.save(str(path))
                return {
                    "saved_to": str(path),
                    "width": screenshot.width,
                    "height": screenshot.height,
                }
            else:
                import io  # noqa: PLC0415

                buf = io.BytesIO()
                screenshot.save(buf, format="PNG")
                b64 = base64.b64encode(buf.getvalue()).decode("ascii")
                return {
                    "base64": b64,
                    "width": screenshot.width,
                    "height": screenshot.height,
                }

        return await asyncio.to_thread(_inner)

    # ------------------------------------------------------------------
    # Actions — Window management
    # ------------------------------------------------------------------

    async def _action_get_window_info(self, params: dict[str, Any]) -> dict[str, Any]:
        p = GetWindowInfoParams.model_validate(params)

        def _inner() -> dict[str, Any]:
            try:
                import pygetwindow as gw  # noqa: PLC0415
            except ImportError:
                return {
                    "error": "pygetwindow is required for window management",
                    "windows": [],
                }

            if p.include_all:
                windows = gw.getAllWindows()
            elif p.title_pattern:
                pattern = re.compile(p.title_pattern, re.IGNORECASE)
                windows = [w for w in gw.getAllWindows() if pattern.search(w.title)]
            else:
                try:
                    active = gw.getActiveWindow()
                    windows = [active] if active else []
                except Exception:
                    windows = []

            result = []
            for w in windows:
                try:
                    result.append({
                        "title": w.title,
                        "left": w.left,
                        "top": w.top,
                        "width": w.width,
                        "height": w.height,
                        "visible": w.visible if hasattr(w, "visible") else True,
                        "minimized": w.isMinimized if hasattr(w, "isMinimized") else False,
                    })
                except Exception:
                    continue

            return {"windows": result, "count": len(result)}

        return await asyncio.to_thread(_inner)

    async def _action_focus_window(self, params: dict[str, Any]) -> dict[str, Any]:
        p = FocusWindowParams.model_validate(params)

        def _inner() -> dict[str, Any]:
            try:
                import pygetwindow as gw  # noqa: PLC0415
            except ImportError as exc:
                raise RuntimeError(
                    "pygetwindow is required: pip install pygetwindow"
                ) from exc

            pattern = re.compile(p.title_pattern, re.IGNORECASE)
            deadline = time.monotonic() + p.timeout
            target = None

            while time.monotonic() < deadline:
                for w in gw.getAllWindows():
                    if pattern.search(w.title):
                        target = w
                        break
                if target is not None:
                    break
                time.sleep(0.5)

            if target is None:
                return {
                    "focused": False,
                    "title_pattern": p.title_pattern,
                    "error": f"No window matching '{p.title_pattern}' found within {p.timeout}s.",
                }

            try:
                target.activate()
            except Exception:
                try:
                    target.minimize()
                    target.restore()
                except Exception:
                    pass

            return {
                "focused": True,
                "title": target.title,
                "title_pattern": p.title_pattern,
            }

        return await asyncio.to_thread(_inner)

    # ------------------------------------------------------------------
    # Manifest
    # ------------------------------------------------------------------

    def get_manifest(self) -> ModuleManifest:
        return ModuleManifest(
            module_id=self.MODULE_ID,
            version=self.VERSION,
            description=(
                "Desktop GUI automation via PyAutoGUI — mouse clicks, keyboard input, "
                "image matching, screenshots, OCR, and window management."
            ),
            platforms=["linux", "macos", "windows"],
            declared_permissions=["gui_control", "screen_capture"],
            dependencies=["pyautogui"],
            tags=["gui", "automation", "desktop", "mouse", "keyboard", "screenshot"],
            actions=[
                ActionSpec(
                    name="click_position",
                    description="Click at specific screen coordinates.",
                    params=[
                        ParamSpec(name="x", type="integer", description="X coordinate."),
                        ParamSpec(name="y", type="integer", description="Y coordinate."),
                        ParamSpec(name="button", type="string", description="Mouse button.", required=False, default="left", enum=["left", "right", "middle"]),
                        ParamSpec(name="clicks", type="integer", description="Number of clicks.", required=False, default=1),
                    ],
                    returns="object",
                    permission_required="power_user",
                ),
                ActionSpec(
                    name="click_image",
                    description="Find a template image on screen and click its center.",
                    params=[
                        ParamSpec(name="image_path", type="string", description="Path to template image."),
                        ParamSpec(name="confidence", type="number", description="Match confidence (0.5-1.0).", required=False, default=0.8),
                        ParamSpec(name="button", type="string", description="Mouse button.", required=False, default="left"),
                        ParamSpec(name="timeout", type="integer", description="Search timeout (seconds).", required=False, default=10),
                    ],
                    returns="object",
                    permission_required="power_user",
                ),
                ActionSpec(
                    name="double_click",
                    description="Double-click at coordinates or on an image.",
                    params=[
                        ParamSpec(name="x", type="integer", description="X coordinate.", required=False),
                        ParamSpec(name="y", type="integer", description="Y coordinate.", required=False),
                        ParamSpec(name="image_path", type="string", description="Template image path.", required=False),
                    ],
                    returns="object",
                    permission_required="power_user",
                ),
                ActionSpec(
                    name="right_click",
                    description="Right-click at coordinates or on an image.",
                    params=[
                        ParamSpec(name="x", type="integer", description="X coordinate.", required=False),
                        ParamSpec(name="y", type="integer", description="Y coordinate.", required=False),
                        ParamSpec(name="image_path", type="string", description="Template image path.", required=False),
                    ],
                    returns="object",
                    permission_required="power_user",
                ),
                ActionSpec(
                    name="type_text",
                    description="Type text as keyboard input.",
                    params=[
                        ParamSpec(name="text", type="string", description="Text to type."),
                        ParamSpec(name="interval", type="number", description="Seconds between key presses.", required=False, default=0.05),
                        ParamSpec(name="clear_first", type="boolean", description="Clear field before typing.", required=False, default=False),
                    ],
                    returns="object",
                    permission_required="power_user",
                ),
                ActionSpec(
                    name="key_press",
                    description="Press a key or key combination (hotkey).",
                    params=[
                        ParamSpec(name="keys", type="array", description="Key names, e.g. ['ctrl', 'c']."),
                        ParamSpec(name="presses", type="integer", description="Number of presses.", required=False, default=1),
                    ],
                    returns="object",
                    permission_required="power_user",
                    examples=[
                        {"description": "Copy selection", "params": {"keys": ["ctrl", "c"]}},
                        {"description": "Press Enter", "params": {"keys": ["enter"]}},
                    ],
                ),
                ActionSpec(
                    name="scroll",
                    description="Scroll the mouse wheel at the given position.",
                    params=[
                        ParamSpec(name="clicks", type="integer", description="Positive=up, negative=down.", required=False, default=3),
                        ParamSpec(name="x", type="integer", description="X coordinate.", required=False),
                        ParamSpec(name="y", type="integer", description="Y coordinate.", required=False),
                    ],
                    returns="object",
                    permission_required="power_user",
                ),
                ActionSpec(
                    name="drag_drop",
                    description="Drag from one position to another.",
                    params=[
                        ParamSpec(name="from_x", type="integer", description="Start X."),
                        ParamSpec(name="from_y", type="integer", description="Start Y."),
                        ParamSpec(name="to_x", type="integer", description="End X."),
                        ParamSpec(name="to_y", type="integer", description="End Y."),
                        ParamSpec(name="duration", type="number", description="Drag duration (seconds).", required=False, default=0.5),
                    ],
                    returns="object",
                    permission_required="power_user",
                ),
                ActionSpec(
                    name="find_on_screen",
                    description="Find a template image on the screen and return its location.",
                    params=[
                        ParamSpec(name="image_path", type="string", description="Path to template image."),
                        ParamSpec(name="confidence", type="number", description="Match confidence.", required=False, default=0.8),
                        ParamSpec(name="grayscale", type="boolean", description="Use grayscale matching.", required=False, default=True),
                        ParamSpec(name="timeout", type="integer", description="Search timeout (seconds).", required=False, default=10),
                    ],
                    returns="object",
                    returns_description="Location and region if found, or found=false.",
                    permission_required="readonly",
                ),
                ActionSpec(
                    name="get_screen_text",
                    description="Extract text from the screen via OCR (Tesseract).",
                    params=[
                        ParamSpec(name="region", type="array", description="(left, top, width, height) crop region.", required=False),
                        ParamSpec(name="lang", type="string", description="Tesseract language code.", required=False, default="eng"),
                    ],
                    returns="object",
                    returns_description="Extracted text from screen.",
                    permission_required="readonly",
                ),
                ActionSpec(
                    name="get_window_info",
                    description="Get information about windows (active or all).",
                    params=[
                        ParamSpec(name="title_pattern", type="string", description="Regex pattern to match.", required=False),
                        ParamSpec(name="include_all", type="boolean", description="Return all windows.", required=False, default=False),
                    ],
                    returns="object",
                    permission_required="readonly",
                ),
                ActionSpec(
                    name="focus_window",
                    description="Find and focus a window by title pattern.",
                    params=[
                        ParamSpec(name="title_pattern", type="string", description="Regex pattern to match."),
                        ParamSpec(name="timeout", type="integer", description="Search timeout (seconds).", required=False, default=10),
                    ],
                    returns="object",
                    permission_required="power_user",
                ),
                ActionSpec(
                    name="take_screenshot",
                    description="Take a screenshot of the screen or a region.",
                    params=[
                        ParamSpec(name="output_path", type="string", description="Save path. Returns base64 if omitted.", required=False),
                        ParamSpec(name="region", type="array", description="(left, top, width, height) crop region.", required=False),
                    ],
                    returns="object",
                    permission_required="readonly",
                ),
            ],
        )
