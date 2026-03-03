# Computer Control Module -- Action Reference

All actions in this module accept natural language `target_description` parameters. The module captures the screen, parses UI elements via the vision module, resolves the best match, and delegates physical actions to the GUI module.

## click_element

Find a UI element by description and click it.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `target_description` | string | Yes | -- | Natural language description (e.g., "Submit button") |
| `click_type` | string | No | `"single"` | Type of click: `single`, `double`, `right` |
| `element_type` | string | No | -- | Filter by type: `button`, `input`, `link`, `icon`, `text`, `checkbox` |
| `timeout` | number | No | `5.0` | Max seconds for capture and parse (1.0-30.0) |

**Returns:** `{clicked, element_id, label, element_type, bbox, confidence, pixel_x, pixel_y, match_strategy, click_type}`

If not found: `{clicked: false, error, screen_elements, screen_text}`

**Permission:** `screen_capture`, `keyboard` | **Risk:** high | **Rate limit:** 60/min

**Example:**
```json
{"target_description": "Submit button", "click_type": "single"}
```

---

## type_into_element

Find an input field by description, click it to focus, and type text.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `target_description` | string | Yes | -- | Description of the input field |
| `text` | string | Yes | -- | Text to type |
| `clear_first` | boolean | No | `true` | Clear field before typing (Ctrl+A, Delete) |
| `element_type` | string | No | -- | Filter by element type (usually `input`) |

**Returns:** `{typed, element_id, label, element_type, bbox, confidence, pixel_x, pixel_y, match_strategy, text, length}`

If not found: `{typed: false, error, screen_elements, screen_text}`

**Permission:** `screen_capture`, `keyboard` | **Risk:** high | **Rate limit:** 60/min

**Example:**
```json
{"target_description": "Search input", "text": "hello world", "clear_first": true}
```

---

## wait_for_element

Poll the screen until an element matching the description appears.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `target_description` | string | Yes | -- | Description of the element to wait for |
| `timeout` | number | No | `30.0` | Max seconds to wait (1.0-120.0) |
| `poll_interval` | number | No | `2.0` | Seconds between screen captures (0.5-10.0) |
| `element_type` | string | No | -- | Filter by element type |

**Returns:** `{found: true, element_id, label, element_type, bbox, confidence, pixel_x, pixel_y, match_strategy, wait_time_ms}`

If timeout: `{found: false, error, wait_time_ms}`

**Permission:** `screen_capture` | **Risk:** medium | **Rate limit:** 30/min

---

## read_screen

Capture the screen and parse all UI elements. Returns structured element list, OCR text, and optionally an annotated screenshot with bounding boxes.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `monitor` | integer | No | `0` | Monitor index |
| `region` | object | No | -- | Crop region: `{left, top, width, height}` |
| `include_screenshot` | boolean | No | `false` | Include annotated screenshot as base64 PNG (~200-500KB) |

**Returns:**
```json
{
  "elements": [{"element_id", "label", "element_type", "bbox", "confidence", "interactable"}],
  "element_count": 42,
  "interactable_count": 18,
  "text": "OCR extracted text...",
  "parse_time_ms": 1234.5,
  "screenshot_b64": "...",
  "scene_graph": "[WINDOW: Firefox] (focused) -> [TOOLBAR] -> button: 'Back' [INTERACTABLE]"
}
```

**Permission:** `screen_capture` | **Risk:** medium

**Notes:**
- Elements are capped at 100 per response to prevent massive payloads.
- OCR text is capped at 2000 characters.
- Scene graph provides hierarchical view of detected regions (taskbar, title bar, sidebar, forms).

---

## find_and_interact

Find an element by description and perform an interaction.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `target_description` | string | Yes | -- | Element description |
| `interaction` | string | No | `"click"` | Interaction type: `click`, `double_click`, `right_click`, `hover` |
| `params` | object | No | `{}` | Additional interaction parameters |

**Returns:** `{interacted, element_id, label, element_type, bbox, confidence, pixel_x, pixel_y, match_strategy, interaction}`

**Permission:** `screen_capture`, `keyboard` | **Risk:** high | **Rate limit:** 60/min

---

## get_element_info

Find an element by description and return its details without interacting.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `target_description` | string | Yes | -- | Element description |
| `element_type` | string | No | -- | Filter by element type |

**Returns:** `{found, element_id, label, element_type, bbox, confidence, pixel_x, pixel_y, match_strategy, text, interactable, alternatives}`

The `alternatives` array contains other candidate matches: `[{label, element_type, confidence}]`.

**Permission:** `screen_capture` | **Risk:** low

---

## execute_gui_sequence

Execute a multi-step GUI workflow: a sequence of semantic actions.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `steps` | array | Yes | -- | List of steps: `[{action, target, params}]` |
| `stop_on_failure` | boolean | No | `true` | Stop the sequence if any step fails |

Each step object:
- `action` (string): Action name (e.g., `click_element`, `type_into_element`)
- `target` (string): Natural language element description
- `params` (object): Additional parameters for the action

**Returns:** `{completed, total, results: [{step, action, result}], stopped_at_step?}`

**Permission:** `screen_capture`, `keyboard` | **Risk:** critical | **Rate limit:** 20/min

**Example:**
```json
{
  "steps": [
    {"action": "click_element", "target": "Username field"},
    {"action": "type_into_element", "target": "Username field", "params": {"text": "admin"}},
    {"action": "click_element", "target": "Password field"},
    {"action": "type_into_element", "target": "Password field", "params": {"text": "secret"}},
    {"action": "click_element", "target": "Login button"}
  ],
  "stop_on_failure": true
}
```

---

## move_to_element

Find an element by description and move the mouse cursor to its center.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `target_description` | string | Yes | -- | Element description |
| `element_type` | string | No | -- | Filter by element type |

**Returns:** `{moved, element_id, label, element_type, bbox, confidence, pixel_x, pixel_y, match_strategy}`

**Permission:** `screen_capture`, `keyboard` | **Risk:** high | **Rate limit:** 60/min

---

## scroll_to_element

Scroll the screen until an element matching the description becomes visible.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `target_description` | string | Yes | -- | Element description |
| `max_scrolls` | integer | No | `10` | Max scroll attempts (1-50) |
| `direction` | string | No | `"down"` | Scroll direction: `down`, `up` |

**Returns:** `{found, element_id, label, element_type, bbox, confidence, pixel_x, pixel_y, match_strategy, scrolls_needed}`

If not found: `{found: false, error, scrolls_needed}`

**Permission:** `screen_capture`, `keyboard` | **Risk:** medium | **Rate limit:** 30/min
