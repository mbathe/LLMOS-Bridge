# Changelog

All notable changes to the `gui` module will be documented in this file.

## [1.0.0] - 2026-02-01

### Added

- Initial release of the GUI module.
- Mouse actions: `click_position`, `click_image`, `double_click`, `right_click`, `scroll`, `drag_drop`.
- Keyboard actions: `type_text` with multi-strategy TextInputEngine (clipboard, xdotool, wtype, ydotool, pyautogui), `key_press` with hotkey support.
- Screen/Vision actions: `find_on_screen` (template matching), `get_screen_text` (OCR via Tesseract), `take_screenshot` (file or base64).
- Window management: `get_window_info`, `focus_window`.
- Security decorators: `@requires_permission` for keyboard and screen capture actions, `@rate_limited` for click/type/key_press.
- Layout-agnostic text input via TextInputEngine auto-detection.
