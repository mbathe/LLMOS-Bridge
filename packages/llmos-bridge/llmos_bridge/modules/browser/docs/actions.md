# Browser Module -- Action Reference

All actions accept an optional `session_id` parameter. If omitted, the `"default"` session is used. A session must be opened with `open_browser` before any other action can be performed on it.

## Lifecycle Actions

### open_browser

Launch a browser instance.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `browser` | string | No | `"chromium"` | Browser engine: `chromium`, `firefox`, `webkit` |
| `headless` | boolean | No | `true` | Run in headless mode |
| `viewport_width` | integer | No | `1280` | Viewport width (320-3840) |
| `viewport_height` | integer | No | `720` | Viewport height (240-2160) |
| `locale` | string | No | `"en-US"` | Browser locale |
| `timezone` | string | No | -- | Timezone ID (e.g., `"America/New_York"`) |
| `proxy` | string | No | -- | Proxy URL (e.g., `"http://proxy:8080"`) |
| `session_id` | string | No | -- | Session identifier |

**Returns:** `{session_id, browser, headless, viewport: {width, height}, status: "opened"}`

**Permission:** `browser.browser_control` | **Risk:** high

**Example:**
```json
{"browser": "chromium", "headless": true, "viewport_width": 1920, "viewport_height": 1080}
```

---

### close_browser

Close the browser and free resources.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `session_id` | string | No | -- | Session to close |

**Returns:** `{session_id, status: "closed"}` or `{session_id, status: "not_open"}`

**Risk:** low

---

## Navigation Actions

### navigate_to

Navigate to a URL and wait for page load.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `url` | string | Yes | -- | Target URL |
| `wait_until` | string | No | `"load"` | Load event: `load`, `domcontentloaded`, `networkidle`, `commit` |
| `timeout` | integer | No | `30000` | Navigation timeout in ms (1000-120000) |
| `session_id` | string | No | -- | Session identifier |

**Returns:** `{url, title, status, session_id}`

**Permission:** `browser.browser_control` | **Risk:** high

**Example:**
```json
{"url": "https://example.com", "wait_until": "networkidle"}
```

---

## Element Interaction Actions

### click_element

Click an element matching a CSS selector or XPath.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `selector` | string | Yes | -- | CSS selector or XPath expression |
| `button` | string | No | `"left"` | Mouse button: `left`, `right`, `middle` |
| `click_count` | integer | No | `1` | Number of clicks (1-3) |
| `timeout` | integer | No | `5000` | Element wait timeout in ms (500-30000) |
| `session_id` | string | No | -- | Session identifier |

**Returns:** `{selector, clicked: true, url, session_id}`

**Permission:** `browser.browser_control` | **Risk:** high

---

### fill_input

Fill a text input or textarea with a value.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `selector` | string | Yes | -- | CSS selector of the input element |
| `value` | string | Yes | -- | Text to fill in |
| `timeout` | integer | No | `5000` | Element wait timeout in ms (500-30000) |
| `session_id` | string | No | -- | Session identifier |

**Returns:** `{selector, filled: true, session_id}`

**Permission:** `browser.browser_control` | **Risk:** high

**Note:** This clears the existing value before filling. Uses Playwright's `fill()` which triggers input events.

---

### submit_form

Click a submit button and wait for navigation.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `selector` | string | Yes | -- | Selector of the form or submit button |
| `timeout` | integer | No | `5000` | Timeout in ms (500-30000) |
| `session_id` | string | No | -- | Session identifier |

**Returns:** `{submitted: true, url, title, session_id}`

**Permission:** `browser.browser_control` | **Risk:** high

---

### select_option

Select option(s) in a `<select>` element.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `selector` | string | Yes | -- | CSS selector of the `<select>` element |
| `value` | string or array | Yes | -- | Value(s) to select |
| `timeout` | integer | No | `5000` | Timeout in ms (500-30000) |
| `session_id` | string | No | -- | Session identifier |

**Returns:** `{selector, selected: [...], session_id}`

**Permission:** `browser.browser_control` | **Risk:** medium

---

### get_element_text

Get the text content of an element.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `selector` | string | Yes | -- | CSS selector |
| `timeout` | integer | No | `5000` | Element wait timeout in ms (500-30000) |
| `session_id` | string | No | -- | Session identifier |

**Returns:** `{selector, text, session_id}`

**Risk:** low

---

## Content Extraction Actions

### get_page_content

Get the page content in various formats.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `format` | string | No | `"text"` | Output format: `html`, `text`, `markdown` |
| `selector` | string | No | -- | Limit content to this CSS selector |
| `session_id` | string | No | -- | Session identifier |

**Returns:** `{content, format, url, title, session_id}`

**Risk:** low

---

### take_screenshot

Take a screenshot of the current page.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `output_path` | string | No | -- | Save path. Returns base64 if omitted |
| `full_page` | boolean | No | `false` | Capture full scrollable page |
| `session_id` | string | No | -- | Session identifier |

**Returns:** `{saved_to, size_bytes, session_id}` or `{base64, size_bytes, session_id}`

**Risk:** low

---

### download_file

Download a file from a URL via the browser.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `url` | string | Yes | -- | URL to download |
| `destination` | string | Yes | -- | Local save path |
| `timeout` | integer | No | `60000` | Download timeout in ms (1000-300000) |
| `session_id` | string | No | -- | Session identifier |

**Returns:** `{url, destination, suggested_filename, session_id}`

**Permission:** `browser.browser_control` | **Risk:** high

---

## JavaScript Actions

### execute_script

Execute JavaScript in the page context.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `script` | string | Yes | -- | JavaScript code to execute |
| `args` | array | No | `[]` | Arguments passed to the script |
| `session_id` | string | No | -- | Session identifier |

**Returns:** `{result, session_id}`

**Permission:** `browser.browser_control` | **Risk:** critical

**Examples:**
```json
{"script": "() => document.title"}
{"script": "(name) => document.querySelector(name).textContent", "args": ["h1"]}
```

**Security note:** This action has `@sensitive_action(RiskLevel.HIGH)` and `@audit_trail("detailed")` decorators. All script executions are logged.

---

## Wait Actions

### wait_for_element

Wait for an element to reach a specific state.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `selector` | string | Yes | -- | CSS selector |
| `state` | string | No | `"visible"` | Target state: `attached`, `detached`, `visible`, `hidden` |
| `timeout` | integer | No | `10000` | Wait timeout in ms (500-60000) |
| `session_id` | string | No | -- | Session identifier |

**Returns:** `{selector, state, found, session_id}`

**Risk:** low

**State descriptions:**
- `attached` -- Element exists in DOM
- `detached` -- Element removed from DOM
- `visible` -- Element is visible on page
- `hidden` -- Element is hidden (display:none, visibility:hidden, etc.)
