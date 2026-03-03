# Window Tracker Module

Window focus monitoring and context recovery. Tracks target windows, detects
context switches, and auto-recovers focus when the user opens another window.

## Overview

The Window Tracker module provides context-aware focus monitoring for GUI
automation workflows. During multi-step screen interactions, users may
accidentally switch to another window. This module detects such context
switches and can automatically recover focus to the target window, keeping
the automation pipeline on track.

Implementation details:
- **X11**: Uses `xdotool` for active window detection and `wmctrl` for
  window listing and focus management.
- **Wayland**: Falls back to `xdotool` via XWayland. Native Wayland support
  planned via `swaymsg` (sway) and `kdotool` (KDE).

The module maintains in-memory tracking state (target window, context switch
count) that persists across action calls within a session.

## Actions

| Action | Description | Risk | Permission |
|--------|-------------|------|------------|
| `get_active_window` | Get information about the currently focused window | Low | `screen_capture` |
| `list_windows` | List all visible windows with geometry and focus state | Low | `screen_capture` |
| `start_tracking` | Begin tracking a target window by title pattern or ID | Low | -- |
| `stop_tracking` | Stop tracking the target window | Low | -- |
| `get_tracking_status` | Check if the tracked window is still focused | Low | -- |
| `recover_focus` | Re-focus the tracked target window | Medium | `keyboard` |
| `focus_window` | Focus a specific window by ID or title | Medium | `keyboard` |
| `detect_context_switch` | Check if the focused window has changed since last check | Low | -- |

## Quick Start

```yaml
actions:
  - id: track-browser
    module: window_tracker
    action: start_tracking
    params:
      title_pattern: "Firefox"

  - id: check-focus
    module: window_tracker
    action: get_tracking_status
    depends_on: [track-browser]
    params: {}

  - id: recover
    module: window_tracker
    action: recover_focus
    depends_on: [check-focus]
    params: {}
```

## Requirements

System dependencies (Linux):
- `xdotool` -- Window ID detection, name lookup, geometry, focus activation
- `wmctrl` -- Window listing with geometry and PID, focus by ID/title

Install on Debian/Ubuntu:
```bash
sudo apt install xdotool wmctrl
```

The module gracefully degrades if tools are missing: `list_windows` falls back
to returning only the active window if `wmctrl` is unavailable.

## Configuration

No module-specific configuration. Uses default LLMOS Bridge settings.

## Platform Support

| Platform | Status |
|----------|--------|
| Linux (X11) | Supported |
| Linux (Wayland via XWayland) | Supported (fallback) |
| macOS | Not supported |
| Windows | Not supported |

## Related Modules

- **vision** -- Parse screen content to understand what the tracked window shows.
- **computer_control** -- Click, type, and interact with the tracked window.
- **gui** -- GUI automation actions that benefit from focus tracking.
- **triggers** -- Create triggers that fire when context switches are detected.
