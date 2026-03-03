# Changelog -- Word Module

## [1.0.0] -- 2026-01-15

### Added
- Initial release with 30 actions covering full Word document automation.
- **Document lifecycle**: `open_document`, `create_document`, `save_document`, `set_document_properties`, `get_document_meta`, `set_margins`, `set_default_font`.
- **Read operations**: `read_document`, `list_paragraphs`, `list_tables`, `extract_text`, `count_words`.
- **Paragraph operations**: `write_paragraph`, `format_text`, `apply_style`, `delete_paragraph`, `insert_page_break`, `insert_section_break`, `insert_list`.
- **Table operations**: `insert_table`, `modify_table_cell`, `add_table_row`.
- **Rich content**: `insert_image`, `insert_hyperlink`, `add_bookmark`, `add_comment`, `insert_toc`.
- **Header/footer**: `add_header_footer` with page number support.
- **Search**: `find_replace` with case-sensitive and whole-word options.
- **Export**: `export_to_pdf` via LibreOffice headless.
- Thread-safe document caching with per-file locking.
- All blocking I/O offloaded via `asyncio.to_thread`.
- Security decorators: `@requires_permission`, `@audit_trail`, `@sensitive_action`.
