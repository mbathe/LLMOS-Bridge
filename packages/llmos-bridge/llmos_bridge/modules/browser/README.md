# Browser Module

Web browser automation via Playwright -- navigate, click, fill forms, extract content, take screenshots, and execute JavaScript.

**Module ID:** `browser`
**Version:** 1.0.0
**Type:** daemon
**Sandbox Level:** strict

## Overview

The `browser` module provides programmatic web browser automation using Playwright. It supports Chromium, Firefox, and WebKit engines with session management, enabling concurrent browser instances identified by `session_id`.

All operations are natively async. Sessions are protected by per-session `asyncio.Lock` to prevent race conditions.

## Actions

| Action | Description | Risk | Permission |
|--------|-------------|------|------------|
| `open_browser` | Launch a browser instance (Chromium, Firefox, or WebKit) | high | `browser.browser_control` |
| `navigate_to` | Navigate to a URL and wait for page load | high | `browser.browser_control` |
| `click_element` | Click an element matching a CSS selector or XPath | high | `browser.browser_control` |
| `fill_input` | Fill a text input or textarea with a value | high | `browser.browser_control` |
| `submit_form` | Click a submit button and wait for navigation | high | `browser.browser_control` |
| `select_option` | Select option(s) in a `<select>` element | medium | `browser.browser_control` |
| `get_element_text` | Get the text content of an element | low | `browser.browser_control` |
| `get_page_content` | Get the page content as HTML, text, or markdown | low | `browser.browser_control` |
| `take_screenshot` | Take a screenshot of the current page | low | `browser.browser_control` |
| `download_file` | Download a file from a URL via the browser | high | `browser.browser_control` |
| `execute_script` | Execute JavaScript in the page context | critical | `browser.browser_control` |
| `wait_for_element` | Wait for an element to reach a specific state | low | `browser.browser_control` |
| `close_browser` | Close the browser and free resources | low | `browser.browser_control` |

## Quick Start

```python
from llmos_bridge.modules.browser.module import BrowserModule

module = BrowserModule()

# Open a browser
result = await module.execute("open_browser", {
    "browser": "chromium",
    "headless": True,
})

# Navigate to a page
result = await module.execute("navigate_to", {
    "url": "https://example.com",
    "wait_until": "networkidle",
})

# Fill a form
await module.execute("fill_input", {
    "selector": "#email",
    "value": "user@example.com",
})

# Click a button
await module.execute("click_element", {
    "selector": "button[type=submit]",
})

# Get page content
result = await module.execute("get_page_content", {
    "format": "text",
})

# Take a screenshot
result = await module.execute("take_screenshot", {
    "output_path": "/tmp/page.png",
    "full_page": True,
})

# Execute JavaScript
result = await module.execute("execute_script", {
    "script": "() => document.title",
})

# Close the browser
await module.execute("close_browser", {})
```

## Session Management

Multiple browser sessions can run concurrently, identified by `session_id`:

```python
# Open two browser sessions
await module.execute("open_browser", {"session_id": "session-a", "browser": "chromium"})
await module.execute("open_browser", {"session_id": "session-b", "browser": "firefox"})

# Navigate each independently
await module.execute("navigate_to", {"session_id": "session-a", "url": "https://site-a.com"})
await module.execute("navigate_to", {"session_id": "session-b", "url": "https://site-b.com"})

# Close individually
await module.execute("close_browser", {"session_id": "session-a"})
await module.execute("close_browser", {"session_id": "session-b"})
```

If `session_id` is omitted, the `"default"` session is used. All sessions are automatically closed on module shutdown (`on_stop()`).

## Requirements

| Dependency | Required | Purpose |
|-----------|----------|---------|
| `playwright` | Yes | Browser automation engine |

Install with:

```bash
pip install playwright
python -m playwright install chromium
# Or install all browsers:
python -m playwright install
```

## Configuration

### Browser Options

| Parameter | Default | Description |
|-----------|---------|-------------|
| `browser` | `"chromium"` | Engine: `chromium`, `firefox`, `webkit` |
| `headless` | `true` | Run without visible window |
| `viewport_width` | `1280` | Browser viewport width (320-3840) |
| `viewport_height` | `720` | Browser viewport height (240-2160) |
| `locale` | `"en-US"` | Browser locale |
| `timezone` | -- | Timezone ID (e.g., `"America/New_York"`) |
| `proxy` | -- | Proxy URL (e.g., `"http://proxy:8080"`) |

## Platform Support

| Platform | Status | Notes |
|----------|--------|-------|
| Linux | Supported | Requires display for headed mode (Xvfb for headless servers) |
| macOS | Supported | Full support |
| Windows | Supported | Full support |

## Related Modules

- **gui** -- Desktop GUI automation (mouse, keyboard) for non-browser desktop interactions
- **computer_control** -- Semantic GUI automation that can work alongside browser for hybrid workflows
- **filesystem** -- File operations for downloaded content processing
