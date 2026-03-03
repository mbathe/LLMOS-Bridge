# Visual Perception Module -- Action Reference

## parse_screen

Parse a screenshot and return a structured list of UI elements with labels,
bounding boxes, and confidence scores.

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `screenshot_path` | string | No | `null` | Absolute path to a PNG/JPEG screenshot file. If omitted, captures the current screen. |
| `box_threshold` | number | No | `null` | Override detection confidence threshold (0.0-1.0, default: 0.05) |

### Returns

```json
{
  "elements": [
    {
      "element_id": "e0001",
      "label": "Submit",
      "element_type": "icon",
      "bbox": [0.45, 0.80, 0.55, 0.85],
      "confidence": 1.0,
      "text": null,
      "interactable": true,
      "extra": {"source": "box_yolo_content_yolo"}
    }
  ],
  "width": 1920,
  "height": 1080,
  "raw_ocr": "string",
  "labeled_image_b64": "string | null",
  "parse_time_ms": 3421.5,
  "model_id": "omniparser-v2",
  "scene_graph_text": "string | null",
  "error": "string | null"
}
```

### Examples

```yaml
actions:
  - id: parse-screenshot
    module: vision
    action: parse_screen
    params:
      screenshot_path: /tmp/screenshot.png

  - id: parse-live-screen
    module: vision
    action: parse_screen
    params: {}

  - id: parse-sensitive
    module: vision
    action: parse_screen
    params:
      box_threshold: 0.3
```

### Security

- Permission: `screen_capture`
- Risk Level: Low

---

## capture_and_parse

Capture the current screen and immediately parse it into UI elements.
Combines screen capture and parsing in a single action.

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `monitor` | integer | No | `0` | Monitor index to capture (0 = primary monitor) |
| `region` | object | No | `null` | Optional crop region: `{left, top, width, height}` in pixels |
| `box_threshold` | number | No | `null` | Override detection confidence threshold (0.0-1.0) |

### Returns

```json
{
  "elements": [],
  "width": 1920,
  "height": 1080,
  "raw_ocr": "string",
  "labeled_image_b64": "string | null",
  "parse_time_ms": 3500.0,
  "model_id": "omniparser-v2",
  "scene_graph_text": "string | null",
  "error": "string | null"
}
```

### Examples

```yaml
actions:
  - id: capture-primary
    module: vision
    action: capture_and_parse
    params:
      monitor: 0

  - id: capture-region
    module: vision
    action: capture_and_parse
    params:
      monitor: 0
      region:
        left: 100
        top: 200
        width: 800
        height: 600

  - id: capture-second-monitor
    module: vision
    action: capture_and_parse
    params:
      monitor: 1
```

### Security

- Permission: `screen_capture`
- Risk Level: Low

---

## find_element

Parse the screen and find a specific UI element by label or type. Returns
the first matching element and its absolute pixel coordinates (center point).

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `query` | string | Yes | -- | Label substring or description of the element to find |
| `element_type` | string | No | `null` | Filter by element type: `icon`, `button`, `text`, `input`, `link` |
| `screenshot_path` | string | No | `null` | Optional path to an existing screenshot (captures screen if omitted) |

### Returns

When found:

```json
{
  "found": true,
  "element": {
    "element_id": "e0005",
    "label": "Submit Button",
    "element_type": "icon",
    "bbox": [0.45, 0.80, 0.55, 0.85],
    "confidence": 1.0,
    "text": null,
    "interactable": true
  },
  "pixel_x": 960,
  "pixel_y": 891
}
```

When not found:

```json
{
  "found": false,
  "element": null,
  "pixel_x": null,
  "pixel_y": null
}
```

### Examples

```yaml
actions:
  - id: find-submit
    module: vision
    action: find_element
    params:
      query: "Submit"
      element_type: button

  - id: find-search-box
    module: vision
    action: find_element
    params:
      query: "Search"
      element_type: input

  - id: find-in-screenshot
    module: vision
    action: find_element
    params:
      query: "Settings"
      screenshot_path: /tmp/app-screen.png
```

### Security

- Permission: `screen_capture`
- Risk Level: Low

---

## get_screen_text

Extract all visible text from the current screen using OCR. Returns
concatenated text without element bounding boxes.

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `screenshot_path` | string | No | `null` | Optional path to an existing screenshot (captures screen if omitted) |

### Returns

```json
{
  "text": "File Edit View Help\nUntitled Document\nHello World",
  "line_count": 3
}
```

### Examples

```yaml
actions:
  - id: read-screen
    module: vision
    action: get_screen_text
    params: {}

  - id: read-screenshot
    module: vision
    action: get_screen_text
    params:
      screenshot_path: /tmp/notification.png
```

### Security

- Permission: `screen_capture`
- Risk Level: Low
