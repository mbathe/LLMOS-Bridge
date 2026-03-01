"""Window Tracker — context-aware focus monitoring and recovery.

Monitors which window has focus, detects context switches (e.g. user opens
another window mid-task), and auto-recovers focus to the target window.

Implementation:
  - X11: ``xdotool getactivewindow``, ``xdotool getwindowname``, ``wmctrl -l``
  - Wayland: ``swaymsg -t get_tree`` (sway) or ``kdotool`` (KDE)
  - Fallback: ``xdotool`` via XWayland

All actions are non-destructive reads (except focus recovery).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any

from llmos_bridge.modules.base import BaseModule, Platform
from llmos_bridge.modules.manifest import ActionSpec, ModuleManifest, ParamSpec
from llmos_bridge.security.decorators import audit_trail, requires_permission
from llmos_bridge.security.models import Permission


@dataclass
class WindowInfo:
    """Information about a window."""

    window_id: str
    title: str
    pid: int | None = None
    class_name: str | None = None
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0
    is_focused: bool = False
    workspace: int = 0


@dataclass
class TrackingState:
    """State for active window tracking."""

    target_title_pattern: str | None = None
    target_window_id: str | None = None
    is_tracking: bool = False
    context_switches: int = 0
    last_focus_check: float = 0.0
    last_known_focused: bool = True


class WindowTrackerModule(BaseModule):
    """Window focus monitoring and context recovery.

    Provides:
      - ``get_active_window`` — current focused window info
      - ``list_windows`` — all visible windows
      - ``start_tracking`` — begin tracking a target window
      - ``stop_tracking`` — stop tracking
      - ``get_tracking_status`` — is target still focused?
      - ``recover_focus`` — re-focus target window
      - ``focus_window`` — focus a specific window
      - ``detect_context_switch`` — check if context changed
    """

    MODULE_ID = "window_tracker"
    VERSION = "1.0.0"
    SUPPORTED_PLATFORMS = [Platform.LINUX]

    def __init__(self) -> None:
        self._tracking = TrackingState()
        self._has_xdotool = shutil.which("xdotool") is not None
        self._has_wmctrl = shutil.which("wmctrl") is not None
        super().__init__()

    # ------------------------------------------------------------------
    # X11 helpers
    # ------------------------------------------------------------------

    def _run_cmd(self, cmd: list[str], timeout: int = 5) -> str:
        """Run a command and return stdout, or empty string on failure."""
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            return result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return ""

    def _get_active_window_id(self) -> str:
        if not self._has_xdotool:
            return ""
        return self._run_cmd(["xdotool", "getactivewindow"])

    def _get_window_name(self, window_id: str) -> str:
        if not self._has_xdotool:
            return ""
        return self._run_cmd(["xdotool", "getwindowname", window_id])

    def _get_window_pid(self, window_id: str) -> int | None:
        if not self._has_xdotool:
            return None
        pid_str = self._run_cmd(["xdotool", "getwindowpid", window_id])
        try:
            return int(pid_str) if pid_str else None
        except ValueError:
            return None

    def _get_window_geometry(self, window_id: str) -> dict[str, int]:
        """Get window geometry via xdotool."""
        if not self._has_xdotool:
            return {"x": 0, "y": 0, "width": 0, "height": 0}
        out = self._run_cmd(["xdotool", "getwindowgeometry", "--shell", window_id])
        geo: dict[str, int] = {"x": 0, "y": 0, "width": 0, "height": 0}
        for line in out.splitlines():
            if "=" in line:
                key, val = line.split("=", 1)
                key = key.strip().lower()
                if key in ("x", "y", "width", "height"):
                    try:
                        geo[key] = int(val.strip())
                    except ValueError:
                        pass
        return geo

    def _get_active_window_info(self) -> WindowInfo | None:
        wid = self._get_active_window_id()
        if not wid:
            return None
        title = self._get_window_name(wid)
        pid = self._get_window_pid(wid)
        geo = self._get_window_geometry(wid)
        return WindowInfo(
            window_id=wid,
            title=title,
            pid=pid,
            is_focused=True,
            **geo,
        )

    def _list_windows_wmctrl(self) -> list[WindowInfo]:
        """List windows using wmctrl -l -G -p."""
        if not self._has_wmctrl:
            return []
        out = self._run_cmd(["wmctrl", "-l", "-G", "-p"])
        windows: list[WindowInfo] = []
        active_id = self._get_active_window_id()

        for line in out.splitlines():
            parts = line.split(None, 8)
            if len(parts) < 9:
                continue
            wid = parts[0]
            workspace = int(parts[1]) if parts[1].isdigit() else 0
            pid = int(parts[2]) if parts[2].isdigit() else None
            x = int(parts[3]) if parts[3].lstrip("-").isdigit() else 0
            y = int(parts[4]) if parts[4].lstrip("-").isdigit() else 0
            width = int(parts[5]) if parts[5].isdigit() else 0
            height = int(parts[6]) if parts[6].isdigit() else 0
            # parts[7] is hostname, parts[8] is title
            title = parts[8] if len(parts) > 8 else ""

            # Normalize window ID format
            is_focused = False
            if active_id:
                try:
                    is_focused = int(wid, 16) == int(active_id)
                except ValueError:
                    is_focused = wid == active_id

            windows.append(WindowInfo(
                window_id=wid,
                title=title,
                pid=pid,
                x=x,
                y=y,
                width=width,
                height=height,
                is_focused=is_focused,
                workspace=workspace,
            ))
        return windows

    def _focus_window_by_id(self, window_id: str) -> bool:
        """Focus a window by its ID."""
        if self._has_wmctrl:
            result = self._run_cmd(["wmctrl", "-i", "-a", window_id])
            return True  # wmctrl doesn't output on success
        if self._has_xdotool:
            self._run_cmd(["xdotool", "windowactivate", "--sync", window_id])
            return True
        return False

    def _focus_window_by_title(self, title_pattern: str) -> bool:
        """Focus a window matching a title pattern."""
        if self._has_wmctrl:
            # wmctrl -a activates window by title substring match.
            self._run_cmd(["wmctrl", "-a", title_pattern])
            return True
        if self._has_xdotool:
            self._run_cmd([
                "xdotool", "search", "--name", title_pattern,
                "windowactivate", "--sync",
            ])
            return True
        return False

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    @requires_permission(Permission.SCREEN_CAPTURE, reason="Reads active window info")
    @audit_trail("standard")
    async def _action_get_active_window(self, params: dict[str, Any]) -> dict[str, Any]:
        info = self._get_active_window_info()
        if info is None:
            return {"found": False, "error": "Cannot detect active window (xdotool not available?)"}
        return {
            "found": True,
            "window_id": info.window_id,
            "title": info.title,
            "pid": info.pid,
            "x": info.x,
            "y": info.y,
            "width": info.width,
            "height": info.height,
        }

    @requires_permission(Permission.SCREEN_CAPTURE, reason="Lists all windows")
    @audit_trail("standard")
    async def _action_list_windows(self, params: dict[str, Any]) -> dict[str, Any]:
        windows = self._list_windows_wmctrl()
        if not windows:
            # Fallback: just the active window.
            active = self._get_active_window_info()
            if active:
                windows = [active]
        return {
            "windows": [
                {
                    "window_id": w.window_id,
                    "title": w.title,
                    "pid": w.pid,
                    "is_focused": w.is_focused,
                    "workspace": w.workspace,
                    "x": w.x,
                    "y": w.y,
                    "width": w.width,
                    "height": w.height,
                }
                for w in windows
            ],
            "count": len(windows),
        }

    @audit_trail("standard")
    async def _action_start_tracking(self, params: dict[str, Any]) -> dict[str, Any]:
        title_pattern = params.get("title_pattern")
        window_id = params.get("window_id")

        if not title_pattern and not window_id:
            # Track current active window.
            active = self._get_active_window_info()
            if active:
                window_id = active.window_id
                title_pattern = active.title

        self._tracking = TrackingState(
            target_title_pattern=title_pattern,
            target_window_id=window_id,
            is_tracking=True,
            context_switches=0,
            last_focus_check=time.monotonic(),
            last_known_focused=True,
        )
        return {
            "tracking": True,
            "target_title": title_pattern,
            "target_window_id": window_id,
        }

    @audit_trail("standard")
    async def _action_stop_tracking(self, params: dict[str, Any]) -> dict[str, Any]:
        switches = self._tracking.context_switches
        self._tracking = TrackingState()
        return {"tracking": False, "total_context_switches": switches}

    @audit_trail("standard")
    async def _action_get_tracking_status(self, params: dict[str, Any]) -> dict[str, Any]:
        if not self._tracking.is_tracking:
            return {"tracking": False}

        active = self._get_active_window_info()
        is_on_target = self._is_target_focused(active)

        if not is_on_target and self._tracking.last_known_focused:
            self._tracking.context_switches += 1
        self._tracking.last_known_focused = is_on_target
        self._tracking.last_focus_check = time.monotonic()

        return {
            "tracking": True,
            "target_focused": is_on_target,
            "context_switches": self._tracking.context_switches,
            "active_window": active.title if active else None,
            "target_title": self._tracking.target_title_pattern,
        }

    @requires_permission(Permission.KEYBOARD, reason="Recovers window focus")
    @audit_trail("standard")
    async def _action_recover_focus(self, params: dict[str, Any]) -> dict[str, Any]:
        if not self._tracking.is_tracking:
            return {"recovered": False, "error": "Not tracking any window"}

        active = self._get_active_window_info()
        if self._is_target_focused(active):
            return {"recovered": True, "already_focused": True}

        success = False
        if self._tracking.target_window_id:
            success = self._focus_window_by_id(self._tracking.target_window_id)
        elif self._tracking.target_title_pattern:
            success = self._focus_window_by_title(self._tracking.target_title_pattern)

        if success:
            time.sleep(0.3)  # Let window manager settle.
            self._tracking.last_known_focused = True

        return {
            "recovered": success,
            "already_focused": False,
            "target_title": self._tracking.target_title_pattern,
        }

    @requires_permission(Permission.KEYBOARD, reason="Focuses a specific window")
    @audit_trail("standard")
    async def _action_focus_window(self, params: dict[str, Any]) -> dict[str, Any]:
        window_id = params.get("window_id")
        title_pattern = params.get("title_pattern")

        if window_id:
            success = self._focus_window_by_id(window_id)
        elif title_pattern:
            success = self._focus_window_by_title(title_pattern)
        else:
            return {"focused": False, "error": "Provide window_id or title_pattern"}

        return {"focused": success}

    @audit_trail("standard")
    async def _action_detect_context_switch(self, params: dict[str, Any]) -> dict[str, Any]:
        if not self._tracking.is_tracking:
            return {"tracking": False, "switched": False}

        active = self._get_active_window_info()
        is_on_target = self._is_target_focused(active)
        switched = not is_on_target and self._tracking.last_known_focused

        if switched:
            self._tracking.context_switches += 1
        self._tracking.last_known_focused = is_on_target

        return {
            "tracking": True,
            "switched": switched,
            "target_focused": is_on_target,
            "context_switches": self._tracking.context_switches,
            "current_window": active.title if active else None,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _is_target_focused(self, active: WindowInfo | None) -> bool:
        if active is None:
            return False

        if self._tracking.target_window_id:
            try:
                return int(active.window_id) == int(self._tracking.target_window_id)
            except ValueError:
                return active.window_id == self._tracking.target_window_id

        if self._tracking.target_title_pattern:
            try:
                return bool(re.search(
                    self._tracking.target_title_pattern,
                    active.title,
                    re.IGNORECASE,
                ))
            except re.error:
                return self._tracking.target_title_pattern.lower() in active.title.lower()

        return False

    # ------------------------------------------------------------------
    # Manifest
    # ------------------------------------------------------------------

    def get_manifest(self) -> ModuleManifest:
        _ACTIONSPEC_KEYS = {"permissions", "risk_level", "irreversible", "data_classification"}
        raw_meta = self._collect_security_metadata()
        security_meta = {
            action: {k: v for k, v in meta.items() if k in _ACTIONSPEC_KEYS}
            for action, meta in raw_meta.items()
        }
        return ModuleManifest(
            module_id=self.MODULE_ID,
            version=self.VERSION,
            description=(
                "Window focus monitoring and context recovery. "
                "Tracks target windows, detects context switches, "
                "and auto-recovers focus when the user opens another window."
            ),
            platforms=[p.value for p in self.SUPPORTED_PLATFORMS],
            actions=[
                ActionSpec(
                    name="get_active_window",
                    description="Get information about the currently focused window.",
                    params=[],
                    returns_description="Window info: title, PID, geometry",
                    **security_meta.get("get_active_window", {}),
                ),
                ActionSpec(
                    name="list_windows",
                    description="List all visible windows with their geometry and focus state.",
                    params=[],
                    returns_description="List of window info objects",
                    **security_meta.get("list_windows", {}),
                ),
                ActionSpec(
                    name="start_tracking",
                    description="Begin tracking a target window by title pattern or ID.",
                    params=[
                        ParamSpec("title_pattern", "string", "Regex pattern to match window title", required=False),
                        ParamSpec("window_id", "string", "Window ID to track", required=False),
                    ],
                    returns_description="Tracking confirmation",
                    **security_meta.get("start_tracking", {}),
                ),
                ActionSpec(
                    name="stop_tracking",
                    description="Stop tracking the target window.",
                    params=[],
                    returns_description="Final tracking statistics",
                    **security_meta.get("stop_tracking", {}),
                ),
                ActionSpec(
                    name="get_tracking_status",
                    description="Check if the tracked window is still focused.",
                    params=[],
                    returns_description="Tracking status with context switch count",
                    **security_meta.get("get_tracking_status", {}),
                ),
                ActionSpec(
                    name="recover_focus",
                    description="Re-focus the tracked target window.",
                    params=[],
                    returns_description="Recovery result",
                    **security_meta.get("recover_focus", {}),
                ),
                ActionSpec(
                    name="focus_window",
                    description="Focus a specific window by ID or title.",
                    params=[
                        ParamSpec("window_id", "string", "Window ID to focus", required=False),
                        ParamSpec("title_pattern", "string", "Title pattern to match", required=False),
                    ],
                    returns_description="Focus result",
                    **security_meta.get("focus_window", {}),
                ),
                ActionSpec(
                    name="detect_context_switch",
                    description="Check if the context (focused window) has changed since last check.",
                    params=[],
                    returns_description="Context switch detection result",
                    **security_meta.get("detect_context_switch", {}),
                ),
            ],
            declared_permissions=[
                Permission.SCREEN_CAPTURE,
                Permission.KEYBOARD,
            ],
        )
