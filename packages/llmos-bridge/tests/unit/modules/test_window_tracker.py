"""Unit tests — WindowTrackerModule (context-aware window monitoring)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from llmos_bridge.modules.window_tracker.module import (
    TrackingState,
    WindowInfo,
    WindowTrackerModule,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_module(**overrides) -> WindowTrackerModule:
    """Create a WindowTrackerModule with mocked system tools."""
    with patch("llmos_bridge.modules.window_tracker.module.shutil") as mock_shutil:
        mock_shutil.which = lambda cmd: f"/usr/bin/{cmd}" if cmd in ("xdotool", "wmctrl") else None
        module = WindowTrackerModule()
    # Apply overrides.
    for k, v in overrides.items():
        setattr(module, k, v)
    return module


# ---------------------------------------------------------------------------
# WindowInfo
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestWindowInfo:
    def test_defaults(self):
        info = WindowInfo(window_id="123", title="Firefox")
        assert info.pid is None
        assert info.is_focused is False
        assert info.workspace == 0

    def test_full_info(self):
        info = WindowInfo(
            window_id="456",
            title="Terminal",
            pid=1234,
            x=100,
            y=200,
            width=800,
            height=600,
            is_focused=True,
            workspace=1,
        )
        assert info.pid == 1234
        assert info.width == 800


# ---------------------------------------------------------------------------
# TrackingState
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTrackingState:
    def test_defaults(self):
        state = TrackingState()
        assert state.is_tracking is False
        assert state.context_switches == 0
        assert state.target_title_pattern is None


# ---------------------------------------------------------------------------
# Module actions
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetActiveWindow:
    @pytest.mark.asyncio
    async def test_no_xdotool(self):
        module = _make_module(_has_xdotool=False)
        result = await module._action_get_active_window({})
        assert result["found"] is False

    @pytest.mark.asyncio
    async def test_with_xdotool(self):
        module = _make_module()

        def mock_run_cmd(cmd, timeout=5):
            if "getactivewindow" in cmd:
                return "12345"
            if "getwindowname" in cmd:
                return "Firefox"
            if "getwindowpid" in cmd:
                return "5678"
            if "getwindowgeometry" in cmd:
                return "X=100\nY=200\nWIDTH=800\nHEIGHT=600"
            return ""

        module._run_cmd = mock_run_cmd
        result = await module._action_get_active_window({})
        assert result["found"] is True
        assert result["title"] == "Firefox"
        assert result["window_id"] == "12345"


@pytest.mark.unit
class TestListWindows:
    @pytest.mark.asyncio
    async def test_empty_list(self):
        module = _make_module(_has_wmctrl=False, _has_xdotool=False)
        result = await module._action_list_windows({})
        assert result["count"] == 0

    @pytest.mark.asyncio
    async def test_with_wmctrl(self):
        module = _make_module()

        def mock_run_cmd(cmd, timeout=5):
            if "wmctrl" in cmd and "-l" in cmd:
                return (
                    "0x04000003  0 1234 100  200  800  600  host Firefox\n"
                    "0x04000004  0 5678 0    0    1920 1080 host Terminal"
                )
            if "getactivewindow" in cmd:
                return "67108867"  # 0x04000003
            return ""

        module._run_cmd = mock_run_cmd
        result = await module._action_list_windows({})
        assert result["count"] == 2
        assert result["windows"][0]["title"] == "Firefox"


@pytest.mark.unit
class TestStartTracking:
    @pytest.mark.asyncio
    async def test_track_by_title(self):
        module = _make_module()
        result = await module._action_start_tracking({"title_pattern": "Firefox"})
        assert result["tracking"] is True
        assert result["target_title"] == "Firefox"
        assert module._tracking.is_tracking is True

    @pytest.mark.asyncio
    async def test_track_by_window_id(self):
        module = _make_module()
        result = await module._action_start_tracking({"window_id": "12345"})
        assert result["tracking"] is True
        assert result["target_window_id"] == "12345"

    @pytest.mark.asyncio
    async def test_track_current(self):
        module = _make_module()
        module._run_cmd = lambda cmd, timeout=5: (
            "12345" if "getactivewindow" in cmd
            else "Firefox" if "getwindowname" in cmd
            else "1234" if "getwindowpid" in cmd
            else "X=0\nY=0\nWIDTH=1920\nHEIGHT=1080" if "getwindowgeometry" in cmd
            else ""
        )
        result = await module._action_start_tracking({})
        assert result["tracking"] is True


@pytest.mark.unit
class TestStopTracking:
    @pytest.mark.asyncio
    async def test_stop(self):
        module = _make_module()
        module._tracking = TrackingState(is_tracking=True, context_switches=3)
        result = await module._action_stop_tracking({})
        assert result["tracking"] is False
        assert result["total_context_switches"] == 3
        assert module._tracking.is_tracking is False


@pytest.mark.unit
class TestGetTrackingStatus:
    @pytest.mark.asyncio
    async def test_not_tracking(self):
        module = _make_module()
        result = await module._action_get_tracking_status({})
        assert result["tracking"] is False

    @pytest.mark.asyncio
    async def test_target_focused(self):
        module = _make_module()
        module._tracking = TrackingState(
            is_tracking=True,
            target_window_id="12345",
            last_known_focused=True,
        )
        module._run_cmd = lambda cmd, timeout=5: (
            "12345" if "getactivewindow" in cmd
            else "Firefox" if "getwindowname" in cmd
            else "" if "getwindowpid" in cmd
            else "X=0\nY=0\nWIDTH=1920\nHEIGHT=1080"
        )
        result = await module._action_get_tracking_status({})
        assert result["tracking"] is True
        assert result["target_focused"] is True
        assert result["context_switches"] == 0

    @pytest.mark.asyncio
    async def test_context_switch_detected(self):
        module = _make_module()
        module._tracking = TrackingState(
            is_tracking=True,
            target_window_id="12345",
            last_known_focused=True,
        )
        # Active window is different from target.
        module._run_cmd = lambda cmd, timeout=5: (
            "99999" if "getactivewindow" in cmd
            else "Other App" if "getwindowname" in cmd
            else "" if "getwindowpid" in cmd
            else "X=0\nY=0\nWIDTH=1920\nHEIGHT=1080"
        )
        result = await module._action_get_tracking_status({})
        assert result["target_focused"] is False
        assert result["context_switches"] == 1


@pytest.mark.unit
class TestRecoverFocus:
    @pytest.mark.asyncio
    async def test_not_tracking(self):
        module = _make_module()
        result = await module._action_recover_focus({})
        assert result["recovered"] is False

    @pytest.mark.asyncio
    async def test_already_focused(self):
        module = _make_module()
        module._tracking = TrackingState(
            is_tracking=True,
            target_window_id="12345",
        )
        module._run_cmd = lambda cmd, timeout=5: (
            "12345" if "getactivewindow" in cmd
            else "Firefox" if "getwindowname" in cmd
            else "" if "getwindowpid" in cmd
            else "X=0\nY=0\nWIDTH=1920\nHEIGHT=1080"
        )
        result = await module._action_recover_focus({})
        assert result["recovered"] is True
        assert result["already_focused"] is True

    @pytest.mark.asyncio
    @patch("llmos_bridge.modules.window_tracker.module.time")
    async def test_recover_by_id(self, mock_time):
        module = _make_module()
        module._tracking = TrackingState(
            is_tracking=True,
            target_window_id="12345",
        )
        # Active is different window.
        module._run_cmd = lambda cmd, timeout=5: (
            "99999" if "getactivewindow" in cmd
            else "Other" if "getwindowname" in cmd
            else "" if "getwindowpid" in cmd
            else "X=0\nY=0\nWIDTH=1920\nHEIGHT=1080"
        )
        module._focus_window_by_id = MagicMock(return_value=True)
        result = await module._action_recover_focus({})
        assert result["recovered"] is True
        assert result["already_focused"] is False
        module._focus_window_by_id.assert_called_with("12345")


@pytest.mark.unit
class TestFocusWindow:
    @pytest.mark.asyncio
    async def test_no_params(self):
        module = _make_module()
        result = await module._action_focus_window({})
        assert result["focused"] is False

    @pytest.mark.asyncio
    async def test_by_id(self):
        module = _make_module()
        module._focus_window_by_id = MagicMock(return_value=True)
        result = await module._action_focus_window({"window_id": "12345"})
        assert result["focused"] is True


@pytest.mark.unit
class TestDetectContextSwitch:
    @pytest.mark.asyncio
    async def test_not_tracking(self):
        module = _make_module()
        result = await module._action_detect_context_switch({})
        assert result["tracking"] is False

    @pytest.mark.asyncio
    async def test_no_switch(self):
        module = _make_module()
        module._tracking = TrackingState(
            is_tracking=True,
            target_window_id="12345",
            last_known_focused=True,
        )
        module._run_cmd = lambda cmd, timeout=5: (
            "12345" if "getactivewindow" in cmd
            else "Firefox" if "getwindowname" in cmd
            else "" if "getwindowpid" in cmd
            else "X=0\nY=0\nWIDTH=1920\nHEIGHT=1080"
        )
        result = await module._action_detect_context_switch({})
        assert result["switched"] is False

    @pytest.mark.asyncio
    async def test_switch_detected(self):
        module = _make_module()
        module._tracking = TrackingState(
            is_tracking=True,
            target_window_id="12345",
            last_known_focused=True,
        )
        module._run_cmd = lambda cmd, timeout=5: (
            "99999" if "getactivewindow" in cmd
            else "Other" if "getwindowname" in cmd
            else "" if "getwindowpid" in cmd
            else "X=0\nY=0\nWIDTH=1920\nHEIGHT=1080"
        )
        result = await module._action_detect_context_switch({})
        assert result["switched"] is True
        assert result["context_switches"] == 1


# ---------------------------------------------------------------------------
# Target matching
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTargetMatching:
    def test_match_by_window_id(self):
        module = _make_module()
        module._tracking = TrackingState(target_window_id="12345")
        active = WindowInfo(window_id="12345", title="Test")
        assert module._is_target_focused(active) is True

    def test_no_match_by_window_id(self):
        module = _make_module()
        module._tracking = TrackingState(target_window_id="12345")
        active = WindowInfo(window_id="99999", title="Test")
        assert module._is_target_focused(active) is False

    def test_match_by_title_pattern(self):
        module = _make_module()
        module._tracking = TrackingState(target_title_pattern="Fire.*")
        active = WindowInfo(window_id="1", title="Firefox - Mozilla")
        assert module._is_target_focused(active) is True

    def test_no_match_by_title(self):
        module = _make_module()
        module._tracking = TrackingState(target_title_pattern="Chrome")
        active = WindowInfo(window_id="1", title="Firefox")
        assert module._is_target_focused(active) is False

    def test_none_active(self):
        module = _make_module()
        module._tracking = TrackingState(target_window_id="12345")
        assert module._is_target_focused(None) is False

    def test_invalid_regex_falls_back(self):
        module = _make_module()
        module._tracking = TrackingState(target_title_pattern="[invalid")
        active = WindowInfo(window_id="1", title="[invalid test")
        # Should not raise, falls back to substring match.
        assert module._is_target_focused(active) is True
