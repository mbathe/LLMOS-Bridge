# GUI Module

Desktop GUI automation via PyAutoGUI -- mouse clicks, keyboard input, image matching, screenshots, OCR, and window management.

**Module ID:** `gui`
**Version:** 1.0.0
**Type:** daemon
**Sandbox Level:** strict

## Actions

| Action | Description | Risk | Permission |
|--------|-------------|------|------------|
| `click_position` | Click at specific screen coordinates | high | `gui.keyboard` |
| `click_image` | Find a template image on screen and click its center | high | `gui.keyboard` |
| `double_click` | Double-click at coordinates or on an image | high | `gui.keyboard` |
| `right_click` | Right-click at coordinates or on an image | high | `gui.keyboard` |
| `type_text` | Type text as keyboard input | high | `gui.keyboard` |
| `key_press` | Press a key or key combination (hotkey) | high | `gui.keyboard` |
| `scroll` | Scroll the mouse wheel at the given position | high | `gui.keyboard` |
| `drag_drop` | Drag from one position to another | high | `gui.keyboard` |
| `find_on_screen` | Find a template image on the screen and return its location | low | `gui.screen_capture` |
| `get_screen_text` | Extract text from the screen via OCR (Tesseract) | low | `gui.screen_capture` |
| `get_window_info` | Get information about windows (active or all) | low | `gui.screen_capture` |
| `focus_window` | Find and focus a window by title pattern | medium | `gui.keyboard` |
| `take_screenshot` | Take a screenshot of the screen or a region | low | `gui.screen_capture` |

## Quick Start

```python
from llmos_bridge.modules.gui.module import GUIModule

module = GUIModule()

# Click at coordinates
result = await module.execute("click_position", {"x": 500, "y": 300})

# Type text with layout-agnostic input
result = await module.execute("type_text", {"text": "Hello, world!"})

# Press a hotkey
result = await module.execute("key_press", {"keys": ["ctrl", "c"]})

# Take a screenshot
result = await module.execute("take_screenshot", {"output_path": "/tmp/screen.png"})

# Find and click an image on screen
result = await module.execute("click_image", {"image_path": "/tmp/button.png"})

# Get text via OCR
result = await module.execute("get_screen_text", {})
```

## Requirements

| Dependency | Required | Purpose |
|-----------|----------|---------|
| `pyautogui` | Yes | Mouse/keyboard automation core |
| `pytesseract` | Optional | OCR text extraction (`get_screen_text`) |
| `opencv-python` | Optional | Image matching confidence (`click_image`, `find_on_screen`) |
| `pygetwindow` | Optional | Window management (`get_window_info`, `focus_window`) |

Install with:

```bash
pip install pyautogui
# Optional extras:
pip install pytesseract opencv-python pygetwindow
```

Tesseract binary must also be installed system-wide for OCR:

```bash
# Ubuntu/Debian
sudo apt install tesseract-ocr
# macOS
brew install tesseract
```

## Configuration

The GUI module requires a display environment (X11, Wayland, or macOS Quartz). Headless servers need Xvfb or similar.

### Text Input Engine

The `type_text` action uses an intelligent `TextInputEngine` that automatically selects the best input method for the current environment:

- **clipboard** -- Most reliable for non-US keyboard layouts
- **xdotool** -- X11 native, good for ASCII
- **wtype** -- Wayland native
- **ydotool** -- Wayland alternative
- **pyautogui** -- Fallback, character-by-character

Set `method` parameter to override auto-detection: `"auto"`, `"clipboard"`, `"xdotool"`, `"wtype"`, `"ydotool"`, `"pyautogui"`.

## Platform Support

| Platform | Status | Notes |
|----------|--------|-------|
| Linux | Supported | X11 and Wayland (via xdotool/wtype) |
| macOS | Supported | Quartz display server |
| Windows | Supported | Win32 API via pyautogui |

## Related Modules

- **computer_control** -- Higher-level semantic GUI automation that delegates physical actions to this module
- **perception_vision** -- Screen parsing via OmniParser, provides element detection for computer_control
- **window_tracker** -- xdotool/wmctrl-based window monitoring and context recovery
