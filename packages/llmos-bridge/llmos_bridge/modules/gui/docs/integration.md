# GUI Module -- Integration Guide

## Cross-Module Workflows

The `gui` module provides low-level physical GUI automation primitives. It is designed to work with other LLMOS modules to build sophisticated automation workflows.

### GUI + Computer Control + Perception Vision (Computer Use Agent)

The most common integration pattern. The `computer_control` module orchestrates `gui` and `perception_vision` to enable semantic GUI automation:

```
computer_control (orchestration layer)
    |
    +-- perception_vision ("vision")  -- captures screen, detects UI elements
    |
    +-- gui ("gui")                   -- performs physical mouse/keyboard actions
```

**Workflow:**

1. `computer_control.read_screen` -> `perception_vision.capture_and_parse` -> structured element list
2. `computer_control.click_element("Submit button")` -> resolves to pixel coordinates -> `gui.click_position(x, y)`
3. `computer_control.type_into_element("Search input", "query")` -> `gui.click_position` + `gui.type_text`

**IML Plan Example:**

```json
{
  "plan_id": "fill-search-form",
  "protocol_version": "2.0",
  "actions": [
    {
      "id": "read",
      "module": "computer_control",
      "action": "read_screen",
      "params": {}
    },
    {
      "id": "click-search",
      "module": "computer_control",
      "action": "click_element",
      "params": {"target_description": "Search input field"},
      "depends_on": ["read"]
    },
    {
      "id": "type-query",
      "module": "computer_control",
      "action": "type_into_element",
      "params": {
        "target_description": "Search input field",
        "text": "LLMOS Bridge documentation"
      },
      "depends_on": ["click-search"]
    },
    {
      "id": "submit",
      "module": "computer_control",
      "action": "click_element",
      "params": {"target_description": "Search button"},
      "depends_on": ["type-query"]
    }
  ]
}
```

### GUI + Window Tracker

Combine GUI actions with window tracking for context-aware automation:

```json
{
  "plan_id": "switch-and-type",
  "protocol_version": "2.0",
  "actions": [
    {
      "id": "focus",
      "module": "gui",
      "action": "focus_window",
      "params": {"title_pattern": "Visual Studio Code"}
    },
    {
      "id": "track",
      "module": "window_tracker",
      "action": "get_active_window",
      "params": {},
      "depends_on": ["focus"]
    },
    {
      "id": "type",
      "module": "gui",
      "action": "type_text",
      "params": {"text": "console.log('hello')"},
      "depends_on": ["track"]
    }
  ]
}
```

### GUI + Browser

Use the `gui` module for desktop interactions alongside browser automation:

```json
{
  "plan_id": "browser-with-desktop",
  "protocol_version": "2.0",
  "actions": [
    {
      "id": "open",
      "module": "browser",
      "action": "open_browser",
      "params": {"headless": false}
    },
    {
      "id": "navigate",
      "module": "browser",
      "action": "navigate_to",
      "params": {"url": "https://example.com/upload"},
      "depends_on": ["open"]
    },
    {
      "id": "screenshot",
      "module": "gui",
      "action": "take_screenshot",
      "params": {"output_path": "/tmp/before-upload.png"},
      "depends_on": ["navigate"]
    }
  ]
}
```

## Direct Usage (Without Computer Control)

When you know exact coordinates or have template images, use the `gui` module directly for maximum performance (avoids vision parsing overhead):

```python
gui = registry.get("gui")

# Direct coordinate click (fastest path)
await gui.execute("click_position", {"x": 100, "y": 200})

# Image-based click (no vision module needed, uses OpenCV)
await gui.execute("click_image", {"image_path": "/tmp/ok_button.png"})

# Keyboard shortcut
await gui.execute("key_press", {"keys": ["ctrl", "s"]})
```

## TextInputEngine Integration

The `type_text` action uses `TextInputEngine` internally for layout-agnostic text input. The engine is a singleton created on first use and auto-detects the display server:

- **X11**: prefers clipboard (xclip/xsel) > xdotool > pyautogui
- **Wayland**: prefers clipboard (wl-copy) > wtype > ydotool > pyautogui
- **macOS/Windows**: uses pyautogui directly

Override with the `method` parameter when the auto-detected method does not work for your environment.
