# Browser Module -- Integration Guide

## Cross-Module Workflows

The `browser` module provides Playwright-based web automation. It works alongside other LLMOS modules for comprehensive automation workflows spanning web and desktop environments.

### Browser + Computer Control (Hybrid Web/Desktop)

For applications that require both browser DOM access and desktop GUI interaction (e.g., file upload dialogs, browser notifications, OS-level prompts):

```json
{
  "plan_id": "upload-workflow",
  "protocol_version": "2.0",
  "description": "Upload a file through a web form using browser + desktop GUI",
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
      "params": {"url": "https://app.example.com/upload"},
      "depends_on": ["open"]
    },
    {
      "id": "click-upload",
      "module": "browser",
      "action": "click_element",
      "params": {"selector": "button.upload-trigger"},
      "depends_on": ["navigate"]
    },
    {
      "id": "wait-dialog",
      "module": "computer_control",
      "action": "wait_for_element",
      "params": {
        "target_description": "File chooser dialog or Open dialog",
        "timeout": 10.0
      },
      "depends_on": ["click-upload"]
    },
    {
      "id": "type-path",
      "module": "computer_control",
      "action": "type_into_element",
      "params": {
        "target_description": "File name input",
        "text": "/home/user/document.pdf"
      },
      "depends_on": ["wait-dialog"]
    },
    {
      "id": "click-open",
      "module": "computer_control",
      "action": "click_element",
      "params": {"target_description": "Open button"},
      "depends_on": ["type-path"]
    },
    {
      "id": "verify",
      "module": "browser",
      "action": "wait_for_element",
      "params": {"selector": ".upload-success", "state": "visible", "timeout": 15000},
      "depends_on": ["click-open"]
    }
  ]
}
```

### Browser + Filesystem (Web Scraping + Storage)

Extract web content and save to the local filesystem:

```json
{
  "plan_id": "scrape-and-save",
  "protocol_version": "2.0",
  "actions": [
    {
      "id": "open",
      "module": "browser",
      "action": "open_browser",
      "params": {"headless": true}
    },
    {
      "id": "navigate",
      "module": "browser",
      "action": "navigate_to",
      "params": {"url": "https://example.com/data"},
      "depends_on": ["open"]
    },
    {
      "id": "extract",
      "module": "browser",
      "action": "get_page_content",
      "params": {"format": "text", "selector": "main.content"},
      "depends_on": ["navigate"]
    },
    {
      "id": "save",
      "module": "filesystem",
      "action": "write_file",
      "params": {
        "path": "/tmp/scraped_content.txt",
        "content": "{{result.extract.content}}"
      },
      "depends_on": ["extract"]
    },
    {
      "id": "screenshot",
      "module": "browser",
      "action": "take_screenshot",
      "params": {"output_path": "/tmp/page_screenshot.png", "full_page": true},
      "depends_on": ["navigate"]
    },
    {
      "id": "close",
      "module": "browser",
      "action": "close_browser",
      "params": {},
      "depends_on": ["save", "screenshot"]
    }
  ]
}
```

### Browser + GUI (Visual Verification)

Use the `gui` module to take OS-level screenshots of the browser window for visual verification:

```json
{
  "plan_id": "visual-test",
  "protocol_version": "2.0",
  "actions": [
    {
      "id": "open",
      "module": "browser",
      "action": "open_browser",
      "params": {"headless": false, "viewport_width": 1920, "viewport_height": 1080}
    },
    {
      "id": "navigate",
      "module": "browser",
      "action": "navigate_to",
      "params": {"url": "https://example.com"},
      "depends_on": ["open"]
    },
    {
      "id": "focus",
      "module": "gui",
      "action": "focus_window",
      "params": {"title_pattern": "Example Domain"},
      "depends_on": ["navigate"]
    },
    {
      "id": "os-screenshot",
      "module": "gui",
      "action": "take_screenshot",
      "params": {"output_path": "/tmp/os_level_screenshot.png"},
      "depends_on": ["focus"]
    },
    {
      "id": "browser-screenshot",
      "module": "browser",
      "action": "take_screenshot",
      "params": {"output_path": "/tmp/browser_screenshot.png"},
      "depends_on": ["navigate"]
    }
  ]
}
```

### Multi-Session Parallel Browsing

Open multiple browser sessions for concurrent web operations:

```json
{
  "plan_id": "parallel-browse",
  "protocol_version": "2.0",
  "execution_mode": "parallel",
  "actions": [
    {
      "id": "open-a",
      "module": "browser",
      "action": "open_browser",
      "params": {"session_id": "site-a", "headless": true}
    },
    {
      "id": "open-b",
      "module": "browser",
      "action": "open_browser",
      "params": {"session_id": "site-b", "headless": true}
    },
    {
      "id": "nav-a",
      "module": "browser",
      "action": "navigate_to",
      "params": {"session_id": "site-a", "url": "https://api-a.example.com"},
      "depends_on": ["open-a"]
    },
    {
      "id": "nav-b",
      "module": "browser",
      "action": "navigate_to",
      "params": {"session_id": "site-b", "url": "https://api-b.example.com"},
      "depends_on": ["open-b"]
    },
    {
      "id": "content-a",
      "module": "browser",
      "action": "get_page_content",
      "params": {"session_id": "site-a", "format": "text"},
      "depends_on": ["nav-a"]
    },
    {
      "id": "content-b",
      "module": "browser",
      "action": "get_page_content",
      "params": {"session_id": "site-b", "format": "text"},
      "depends_on": ["nav-b"]
    }
  ]
}
```

## Form Automation Pattern

A common pattern for filling and submitting web forms:

```python
browser = registry.get("browser")

# Open and navigate
await browser.execute("open_browser", {"headless": True})
await browser.execute("navigate_to", {"url": "https://app.example.com/login"})

# Fill form fields
await browser.execute("fill_input", {"selector": "#username", "value": "admin"})
await browser.execute("fill_input", {"selector": "#password", "value": "secret"})

# Select a dropdown option
await browser.execute("select_option", {"selector": "#role", "value": "administrator"})

# Submit and wait for result
await browser.execute("submit_form", {"selector": "form#login button[type=submit]"})
await browser.execute("wait_for_element", {"selector": ".dashboard", "state": "visible"})

# Verify
result = await browser.execute("get_page_content", {"format": "text"})
assert "Welcome" in result["content"]

# Cleanup
await browser.execute("close_browser", {})
```

## Browser vs. Computer Control for Web

**Use `browser` module when:**
- You have CSS selectors or XPath expressions for elements
- You need DOM-level access (innerHTML, JavaScript evaluation)
- You are working with headless automation
- You need reliable, fast element targeting

**Use `computer_control` module when:**
- You only have natural language descriptions of elements
- The web page uses complex rendering (Canvas, WebGL, iframes) that selectors cannot reach
- You need to interact with browser chrome (address bar, tabs, menus)
- You are working with a non-headless browser and need visual element recognition

**Combine both when:**
- The workflow spans browser DOM interaction AND desktop OS dialogs (file choosers, print dialogs)
- You need to verify visual rendering quality alongside DOM content
