# Window Tracker Module -- Action Reference

## get_active_window

Get information about the currently focused window.

### Parameters

No parameters required.

### Returns

```json
{
  "found": true,
  "window_id": "string",
  "title": "string",
  "pid": 1234,
  "x": 0,
  "y": 0,
  "width": 1920,
  "height": 1080
}
```

If no active window can be detected:

```json
{
  "found": false,
  "error": "Cannot detect active window (xdotool not available?)"
}
```

### Examples

```yaml
actions:
  - id: check-focused
    module: window_tracker
    action: get_active_window
    params: {}
```

### Security

- Permission: `screen_capture`
- Risk Level: Low
- Audit trail: standard

---

## list_windows

List all visible windows with their geometry and focus state.

### Parameters

No parameters required.

### Returns

```json
{
  "windows": [
    {
      "window_id": "string",
      "title": "string",
      "pid": 1234,
      "is_focused": false,
      "workspace": 0,
      "x": 0,
      "y": 0,
      "width": 1920,
      "height": 1080
    }
  ],
  "count": 5
}
```

### Examples

```yaml
actions:
  - id: get-all-windows
    module: window_tracker
    action: list_windows
    params: {}
```

### Security

- Permission: `screen_capture`
- Risk Level: Low
- Audit trail: standard

---

## start_tracking

Begin tracking a target window by title pattern or ID. If neither is provided,
tracks the currently active window.

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `title_pattern` | string | No | `null` | Regex pattern to match the target window title |
| `window_id` | string | No | `null` | Window ID to track directly |

### Returns

```json
{
  "tracking": true,
  "target_title": "string",
  "target_window_id": "string"
}
```

### Examples

```yaml
actions:
  - id: track-browser
    module: window_tracker
    action: start_tracking
    params:
      title_pattern: "Firefox"

  - id: track-by-id
    module: window_tracker
    action: start_tracking
    params:
      window_id: "0x04800003"

  - id: track-current
    module: window_tracker
    action: start_tracking
    params: {}
```

### Security

- Risk Level: Low
- Audit trail: standard

---

## stop_tracking

Stop tracking the target window and return final statistics.

### Parameters

No parameters required.

### Returns

```json
{
  "tracking": false,
  "total_context_switches": 3
}
```

### Examples

```yaml
actions:
  - id: stop-track
    module: window_tracker
    action: stop_tracking
    params: {}
```

### Security

- Risk Level: Low
- Audit trail: standard

---

## get_tracking_status

Check if the tracked window is still focused. Updates the internal
context switch counter.

### Parameters

No parameters required.

### Returns

When tracking is active:

```json
{
  "tracking": true,
  "target_focused": true,
  "context_switches": 2,
  "active_window": "Firefox - Google",
  "target_title": "Firefox"
}
```

When not tracking:

```json
{
  "tracking": false
}
```

### Examples

```yaml
actions:
  - id: check-status
    module: window_tracker
    action: get_tracking_status
    params: {}
```

### Security

- Risk Level: Low
- Audit trail: standard

---

## recover_focus

Re-focus the tracked target window. Uses wmctrl (preferred) or xdotool
to activate the window.

### Parameters

No parameters required.

### Returns

```json
{
  "recovered": true,
  "already_focused": false,
  "target_title": "Firefox"
}
```

### Examples

```yaml
actions:
  - id: bring-back-focus
    module: window_tracker
    action: recover_focus
    params: {}
```

### Security

- Permission: `keyboard`
- Risk Level: Medium
- Audit trail: standard

---

## focus_window

Focus a specific window by ID or title pattern.

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `window_id` | string | No | `null` | Window ID to focus |
| `title_pattern` | string | No | `null` | Title pattern to match and focus |

At least one of `window_id` or `title_pattern` must be provided.

### Returns

```json
{
  "focused": true
}
```

### Examples

```yaml
actions:
  - id: focus-terminal
    module: window_tracker
    action: focus_window
    params:
      title_pattern: "Terminal"

  - id: focus-by-id
    module: window_tracker
    action: focus_window
    params:
      window_id: "0x04800003"
```

### Security

- Permission: `keyboard`
- Risk Level: Medium
- Audit trail: standard

---

## detect_context_switch

Check if the context (focused window) has changed since the last check.
Increments the internal context switch counter on detection.

### Parameters

No parameters required.

### Returns

```json
{
  "tracking": true,
  "switched": true,
  "target_focused": false,
  "context_switches": 3,
  "current_window": "Slack - General"
}
```

### Examples

```yaml
actions:
  - id: check-switch
    module: window_tracker
    action: detect_context_switch
    params: {}
```

### Security

- Risk Level: Low
- Audit trail: standard
