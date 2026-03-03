# Changelog

All notable changes to the `computer_control` module will be documented in this file.

## [1.0.0] - 2026-02-01

### Added

- Initial release of the Computer Control module.
- Semantic GUI actions: `click_element`, `type_into_element`, `find_and_interact`, `move_to_element`.
- Screen reading: `read_screen` with structured element list, OCR text, optional annotated screenshot, and scene graph.
- Waiting: `wait_for_element` with configurable timeout and poll interval.
- Scrolling: `scroll_to_element` with directional scrolling until element found.
- Multi-step workflows: `execute_gui_sequence` for chained semantic actions with stop-on-failure.
- Element inspection: `get_element_info` for non-interactive element lookup with alternatives.
- `ElementResolver` for fuzzy matching of natural language descriptions to detected UI elements.
- `SpeculativePrefetcher` integration for background screen parsing (~4s savings per iteration).
- `PerceptionCache` integration with MD5 content fingerprinting.
- Security decorators: `@requires_permission`, `@sensitive_action`, `@rate_limited`, `@audit_trail`.
