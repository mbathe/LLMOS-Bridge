# Computer Control Module

Semantic GUI automation gateway. Describe UI elements in natural language and interact with them automatically via vision + GUI modules.

**Module ID:** `computer_control`
**Version:** 1.0.0
**Type:** daemon (system)
**Sandbox Level:** strict

## Overview

The `computer_control` module is an orchestration layer that bridges perception (vision) and physical GUI actions. Instead of requiring pixel coordinates, you describe elements in natural language (e.g., "Submit button", "Search input field") and the module handles:

1. Screen capture via the `vision` module (OmniParser or custom backend)
2. Element resolution using fuzzy matching (ElementResolver)
3. Pixel coordinate computation from bounding boxes
4. Physical action delegation to the `gui` module (PyAutoGUI)

## Architecture

```
computer_control (this module)
    |
    +-- vision (OmniParserModule)    -- screen capture + UI element parsing
    |
    +-- gui (GUIModule)              -- physical mouse/keyboard actions
```

Both dependent modules are accessed via the `ModuleRegistry` -- no direct imports of pyautogui, torch, or OmniParser.

## Actions

| Action | Description | Risk | Permission |
|--------|-------------|------|------------|
| `click_element` | Find a UI element by description and click it | high | `screen_capture`, `keyboard` |
| `type_into_element` | Find an input field by description, click it, and type text | high | `screen_capture`, `keyboard` |
| `wait_for_element` | Poll the screen until an element appears | medium | `screen_capture` |
| `read_screen` | Capture and parse all UI elements on screen | medium | `screen_capture` |
| `find_and_interact` | Find an element and perform click/double_click/right_click/hover | high | `screen_capture`, `keyboard` |
| `get_element_info` | Find an element and return details without interacting | low | `screen_capture` |
| `execute_gui_sequence` | Execute a multi-step GUI workflow | critical | `screen_capture`, `keyboard` |
| `move_to_element` | Find an element and move the cursor to it | high | `screen_capture`, `keyboard` |
| `scroll_to_element` | Scroll until an element becomes visible | medium | `screen_capture`, `keyboard` |

## Quick Start

```python
from llmos_bridge.modules.computer_control.module import ComputerControlModule
from llmos_bridge.modules.registry import ModuleRegistry

module = ComputerControlModule()
module.set_registry(registry)  # Registry must contain "vision" and "gui" modules

# Click a button by description
result = await module.execute("click_element", {
    "target_description": "Submit button",
    "click_type": "single",
})

# Type into an input field
result = await module.execute("type_into_element", {
    "target_description": "Email address input",
    "text": "user@example.com",
    "clear_first": True,
})

# Read all UI elements on screen
result = await module.execute("read_screen", {
    "include_screenshot": True,
})

# Wait for a loading indicator to disappear
result = await module.execute("wait_for_element", {
    "target_description": "Success message",
    "timeout": 30.0,
})

# Execute a multi-step workflow
result = await module.execute("execute_gui_sequence", {
    "steps": [
        {"action": "click_element", "target": "Username field"},
        {"action": "type_into_element", "target": "Username field", "params": {"text": "admin"}},
        {"action": "click_element", "target": "Login button"},
    ],
    "stop_on_failure": True,
})
```

## Requirements

| Dependency | Required | Purpose |
|-----------|----------|---------|
| `pyautogui` | Yes (via gui module) | Physical mouse/keyboard automation |
| `llmos-bridge[vision]` | Yes (via vision module) | Screen parsing (OmniParser) |

The module itself has no direct heavy dependencies -- it accesses `vision` and `gui` modules through the registry.

## Configuration

### Vision Configuration

In `llmos_bridge.config.Settings`:

```yaml
vision:
  cache_max_entries: 5          # LRU cache size for parsed screens
  cache_ttl_seconds: 2.0        # Cache entry time-to-live
  speculative_prefetch: true     # Background parse after each action
```

### Speculative Prefetcher

After each click/type/interact action, the module triggers a background screen parse via `SpeculativePrefetcher`. The next `read_screen` or element resolution call can use the pre-cached result, saving approximately 4 seconds per iteration.

## Platform Support

| Platform | Status | Notes |
|----------|--------|-------|
| Linux | Supported | X11 and Wayland |
| macOS | Supported | Quartz display |
| Windows | Supported | Win32 API |

## Related Modules

- **gui** -- Low-level physical GUI automation (mouse, keyboard, screenshots)
- **perception_vision** -- Screen capture and UI element detection (OmniParser backend)
- **window_tracker** -- Window monitoring and context recovery
- **browser** -- Web browser automation (complementary for web-based workflows)
