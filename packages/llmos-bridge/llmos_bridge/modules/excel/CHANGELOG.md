# Changelog -- Excel Module

## [1.0.0] -- 2026-01-15

### Added
- Initial release with 42 actions covering full Excel automation.
- **Workbook lifecycle**: `create_workbook`, `open_workbook`, `close_workbook`, `save_workbook`, `get_workbook_info`.
- **Sheet management**: `list_sheets`, `create_sheet`, `delete_sheet`, `rename_sheet`, `copy_sheet`, `get_sheet_info`, `protect_sheet`, `unprotect_sheet`, `set_page_setup`.
- **Cell & range operations**: `read_cell`, `write_cell`, `read_range`, `write_range`, `copy_range`.
- **Row & column operations**: `insert_rows`, `delete_rows`, `insert_columns`, `delete_columns`, `set_column_width`, `set_row_height`.
- **Cell manipulation**: `merge_cells`, `unmerge_cells`, `freeze_panes`.
- **Data operations**: `find_replace`, `remove_duplicates`, `apply_formula`, `add_named_range`, `add_data_validation`, `add_autofilter`.
- **Formatting**: `format_range`, `apply_conditional_format`.
- **Charts**: `create_chart` with support for bar, col, line, pie, doughnut, scatter, area, radar types.
- **Images**: `insert_image` supporting PNG, JPEG, BMP, and GIF.
- **Comments**: `add_comment`, `delete_comment`.
- **Export**: `export_to_csv`, `export_to_pdf` (via LibreOffice).
- Thread-safe workbook caching with per-file locking.
- All blocking I/O offloaded via `asyncio.to_thread`.
- Security decorators: `@requires_permission`, `@audit_trail`.
