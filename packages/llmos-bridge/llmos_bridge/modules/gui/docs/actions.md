# GUI Module -- Action Reference

## Mouse Actions

### click_position

Click at specific screen coordinates.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `x` | integer | Yes | -- | X coordinate (>= 0) |
| `y` | integer | Yes | -- | Y coordinate (>= 0) |
| `button` | string | No | `"left"` | Mouse button: `left`, `right`, `middle` |
| `clicks` | integer | No | `1` | Number of clicks (1-3) |
| `interval` | number | No | `0.1` | Seconds between clicks (0.0-5.0) |

**Returns:** `{x, y, button, clicks, clicked: true}`

**Permission:** `gui.keyboard` | **Risk:** high | **Rate limit:** 120/min

---

### click_image

Find a template image on screen and click its center.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `image_path` | string | Yes | -- | Path to template image file |
| `confidence` | number | No | `0.8` | Match confidence threshold (0.5-1.0) |
| `button` | string | No | `"left"` | Mouse button |
| `timeout` | integer | No | `10` | Search timeout in seconds (1-60) |

**Returns:** `{image_path, x, y, button, clicked: true}`

**Permission:** `gui.keyboard` | **Risk:** high

**Note:** Requires `opencv-python` for confidence-based matching.

---

### double_click

Double-click at coordinates or on an image.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `x` | integer | No | -- | X coordinate |
| `y` | integer | No | -- | Y coordinate |
| `image_path` | string | No | -- | Template image path (alternative to x/y) |
| `confidence` | number | No | `0.8` | Match confidence for image matching |

**Returns:** `{x, y, double_clicked: true}`

**Permission:** `gui.keyboard` | **Risk:** high

---

### right_click

Right-click at coordinates or on an image.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `x` | integer | No | -- | X coordinate |
| `y` | integer | No | -- | Y coordinate |
| `image_path` | string | No | -- | Template image path (alternative to x/y) |

**Returns:** `{x, y, right_clicked: true}`

**Permission:** `gui.keyboard` | **Risk:** high

---

### scroll

Scroll the mouse wheel at the given position.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `clicks` | integer | No | `3` | Positive = scroll up, negative = scroll down |
| `x` | integer | No | -- | X coordinate (current position if omitted) |
| `y` | integer | No | -- | Y coordinate (current position if omitted) |

**Returns:** `{clicks, direction, scrolled: true}`

**Permission:** `gui.keyboard` | **Risk:** high

---

### drag_drop

Drag from one position to another.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `from_x` | integer | Yes | -- | Start X coordinate |
| `from_y` | integer | Yes | -- | Start Y coordinate |
| `to_x` | integer | Yes | -- | End X coordinate |
| `to_y` | integer | Yes | -- | End Y coordinate |
| `duration` | number | No | `0.5` | Drag duration in seconds (0.1-5.0) |

**Returns:** `{from: {x, y}, to: {x, y}, duration, dragged: true}`

**Permission:** `gui.keyboard` | **Risk:** high

---

## Keyboard Actions

### type_text

Type text as keyboard input using the best available input method.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `text` | string | Yes | -- | Text to type |
| `interval` | number | No | `0.05` | Seconds between key presses (0.0-1.0) |
| `clear_first` | boolean | No | `false` | Clear field before typing (Ctrl+A, Delete) |
| `method` | string | No | `"auto"` | Input method: `auto`, `clipboard`, `xdotool`, `wtype`, `ydotool`, `pyautogui` |

**Returns:** `{text, length, typed: true, method}`

**Permission:** `gui.keyboard` | **Risk:** high | **Rate limit:** 120/min

**Notes:**
- `auto` mode selects clipboard > xdotool > wtype > ydotool > pyautogui based on availability.
- `clipboard` is most reliable for non-US keyboard layouts.

---

### key_press

Press a key or key combination (hotkey).

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `keys` | array[string] | Yes | -- | Key names (e.g. `["ctrl", "c"]` or `["enter"]`) |
| `presses` | integer | No | `1` | Number of presses (1-100) |
| `interval` | number | No | `0.1` | Seconds between presses (0.0-2.0) |

**Returns:** `{keys, presses, pressed: true}`

**Permission:** `gui.keyboard` | **Risk:** high | **Rate limit:** 120/min

**Examples:**
- Copy selection: `{"keys": ["ctrl", "c"]}`
- Press Enter: `{"keys": ["enter"]}`
- Alt+F4: `{"keys": ["alt", "F4"]}`

---

## Screen / Vision Actions

### find_on_screen

Find a template image on the screen and return its location.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `image_path` | string | Yes | -- | Path to template image |
| `confidence` | number | No | `0.8` | Match confidence (0.5-1.0) |
| `grayscale` | boolean | No | `true` | Use grayscale matching (faster) |
| `timeout` | integer | No | `10` | Search timeout in seconds (1-60) |

**Returns:** `{found, image_path, x, y, region: {left, top, width, height}}`

**Permission:** `gui.screen_capture` | **Risk:** low

---

### get_screen_text

Extract text from the screen via OCR (Tesseract).

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `region` | array | No | -- | `(left, top, width, height)` crop region. Full screen if omitted |
| `lang` | string | No | `"eng"` | Tesseract language code |

**Returns:** `{text, region, lang}`

**Permission:** `gui.screen_capture` | **Risk:** low

**Requires:** `pytesseract` package and Tesseract binary.

---

### take_screenshot

Take a screenshot of the screen or a region.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `output_path` | string | No | -- | Save path. Returns base64 if omitted |
| `region` | array | No | -- | `(left, top, width, height)` crop region |

**Returns:** `{saved_to, width, height}` or `{base64, width, height}`

**Permission:** `gui.screen_capture` | **Risk:** low

---

## Window Management Actions

### get_window_info

Get information about windows (active or all).

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `title_pattern` | string | No | -- | Regex pattern to match window title |
| `include_all` | boolean | No | `false` | Return all windows |

**Returns:** `{windows: [{title, left, top, width, height, visible, minimized}], count}`

**Permission:** `gui.screen_capture` | **Risk:** low

**Requires:** `pygetwindow` package.

---

### focus_window

Find and focus a window by title pattern.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `title_pattern` | string | Yes | -- | Regex pattern to match window title |
| `timeout` | integer | No | `10` | Search timeout in seconds (1-30) |

**Returns:** `{focused, title, title_pattern}` or `{focused: false, error}`

**Permission:** `gui.keyboard` | **Risk:** medium

**Requires:** `pygetwindow` package.
