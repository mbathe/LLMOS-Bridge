# Changelog

All notable changes to the `browser` module will be documented in this file.

## [1.0.0] - 2026-02-01

### Added

- Initial release of the Browser module.
- Browser lifecycle: `open_browser` (Chromium/Firefox/WebKit), `close_browser` with auto-cleanup on shutdown.
- Navigation: `navigate_to` with configurable wait conditions (load, domcontentloaded, networkidle, commit).
- Element interaction: `click_element` (CSS/XPath selectors), `fill_input`, `submit_form`, `select_option`.
- Content extraction: `get_page_content` (HTML/text/markdown), `get_element_text`.
- Screenshot: `take_screenshot` (file or base64, full-page support).
- File download: `download_file` via browser context.
- JavaScript execution: `execute_script` with argument passing.
- Wait: `wait_for_element` with state conditions (attached, detached, visible, hidden).
- Session management: multiple concurrent sessions via `session_id`, per-session locking.
- Security decorators: `@requires_permission`, `@sensitive_action` (execute_script), `@audit_trail`.
