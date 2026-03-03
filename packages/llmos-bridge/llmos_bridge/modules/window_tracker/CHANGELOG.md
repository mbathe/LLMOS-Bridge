# Changelog -- Window Tracker Module

## [1.0.0] -- 2026-02-20

### Added
- Initial release with 8 actions.
- `get_active_window` -- Retrieve focused window info (ID, title, PID, geometry) via xdotool.
- `list_windows` -- List all visible windows with geometry, PID, workspace, and focus state via wmctrl.
- `start_tracking` -- Begin tracking a target window by title regex pattern or window ID.
- `stop_tracking` -- Stop tracking and return total context switch count.
- `get_tracking_status` -- Check if the tracked window is still focused with context switch counter.
- `recover_focus` -- Re-focus the tracked target window via wmctrl or xdotool.
- `focus_window` -- Focus any window by ID or title pattern.
- `detect_context_switch` -- Detect if the focused window changed since last check.
- WindowInfo dataclass for structured window data.
- TrackingState for in-memory session tracking with context switch counting.
- Graceful degradation when xdotool or wmctrl are not installed.
- Window ID normalization (hex/decimal) for cross-tool compatibility.
- Security decorators: `@requires_permission(Permission.SCREEN_CAPTURE)`, `@requires_permission(Permission.KEYBOARD)`, `@audit_trail`.
