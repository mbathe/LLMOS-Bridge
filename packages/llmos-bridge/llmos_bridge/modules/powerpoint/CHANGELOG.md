# Changelog -- PowerPoint Module

## [1.0.0] -- 2026-01-15

### Added
- Initial release with 25 actions covering full PowerPoint automation.
- **Presentation lifecycle**: `create_presentation`, `open_presentation`, `save_presentation`, `get_presentation_info`.
- **Slide management**: `add_slide`, `delete_slide`, `duplicate_slide`, `reorder_slide`, `list_slides`, `read_slide`, `set_slide_layout`.
- **Text content**: `set_slide_title`, `add_text_box`, `add_slide_notes`.
- **Shapes**: `add_shape` (20 shape types), `format_shape`, `add_image`.
- **Charts**: `add_chart` with support for bar, col, line, pie, doughnut, scatter, area, bubble, radar types.
- **Tables**: `add_table`, `format_table_cell`.
- **Background & theme**: `set_slide_background` (solid, image, gradient), `apply_theme`, `add_transition` (7 transition types).
- **Export**: `export_to_pdf`, `export_slide_as_image` (via LibreOffice).
- Thread-safe presentation caching with per-file locking.
- All blocking I/O offloaded via `asyncio.to_thread`.
- Security decorators: `@requires_permission`, `@audit_trail`, `@sensitive_action`.
