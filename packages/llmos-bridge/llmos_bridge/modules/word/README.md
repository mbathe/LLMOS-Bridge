# Word Module

Create, read, edit, and export Microsoft Word (.docx) documents.

## Overview

The Word module provides comprehensive `.docx` document automation for IML
plans. It covers the full document lifecycle: creating and opening documents,
reading and writing paragraphs, table management, images, hyperlinks,
bookmarks, table of contents, headers and footers, find-and-replace, and
PDF export. All blocking I/O is delegated to `asyncio.to_thread` so the
async event loop is never blocked. Document instances are cached by source
path to avoid redundant disk reads within a session.

## Actions

| Action | Description | Risk | Permission |
|--------|-------------|------|------------|
| `open_document` | Open a .docx file and return its structure summary | Medium | `filesystem_read` |
| `create_document` | Create a new blank Word document with metadata and default font | Medium | `filesystem_write` |
| `save_document` | Save a cached document to disk | Medium | `filesystem_write` |
| `set_document_properties` | Update core document properties (title, author, subject) | Medium | `filesystem_write` |
| `get_document_meta` | Return all core_properties of the document | Low | `filesystem_read` |
| `set_margins` | Set page margins for a section | Medium | `filesystem_write` |
| `set_default_font` | Change the Normal style font name and/or size | Medium | `filesystem_write` |
| `read_document` | Extract full document content with optional tables and headers | Low | `filesystem_read` |
| `list_paragraphs` | List all paragraphs with index, text, style, and empty status | Low | `filesystem_read` |
| `list_tables` | List all tables with index, row/col counts, and preview | Low | `filesystem_read` |
| `extract_text` | Extract all text as a single string | Low | `filesystem_read` |
| `count_words` | Count total words across paragraphs and table cells | Low | `filesystem_read` |
| `write_paragraph` | Add a paragraph with text, style, and formatting | Medium | `filesystem_write` |
| `format_text` | Apply character-level formatting to a specific run | Medium | `filesystem_write` |
| `apply_style` | Apply a named Word style to a paragraph | Medium | `filesystem_write` |
| `delete_paragraph` | Remove a paragraph from the document | Medium | `filesystem_write` |
| `insert_page_break` | Insert a page break | Medium | `filesystem_write` |
| `insert_section_break` | Insert a section break of the given type | Medium | `filesystem_write` |
| `insert_list` | Insert a bulleted or numbered list | Medium | `filesystem_write` |
| `insert_table` | Insert a table with dimensions, data, style, and header | Medium | `filesystem_write` |
| `modify_table_cell` | Update text and formatting of a specific table cell | Medium | `filesystem_write` |
| `add_table_row` | Append a new row to an existing table | Medium | `filesystem_write` |
| `insert_image` | Insert an image with optional width, caption, and alignment | Medium | `filesystem_read`, `filesystem_write` |
| `insert_hyperlink` | Add a clickable hyperlink to a paragraph | Medium | `filesystem_write` |
| `add_bookmark` | Add a named bookmark to a paragraph | Medium | `filesystem_write` |
| `add_comment` | Add a comment to a paragraph | Medium | `filesystem_write` |
| `insert_toc` | Insert a Table of Contents field code | Medium | `filesystem_write` |
| `add_header_footer` | Set header/footer text with optional page numbers | Medium | `filesystem_write` |
| `find_replace` | Find and replace text throughout the document | Medium | `filesystem_write` |
| `export_to_pdf` | Export the document to PDF using LibreOffice headless | Medium | `filesystem_write` |

## Quick Start

```yaml
actions:
  - id: create-doc
    module: word
    action: create_document
    params:
      output_path: /reports/memo.docx
      title: "Project Update"
      author: "LLMOS Bridge"

  - id: add-heading
    module: word
    action: write_paragraph
    params:
      path: /reports/memo.docx
      text: "Project Status Update"
      style: "Heading 1"
    depends_on: [create-doc]

  - id: add-body
    module: word
    action: write_paragraph
    params:
      path: /reports/memo.docx
      text: "The project is on track for delivery in Q2."
      style: Normal
    depends_on: [add-heading]

  - id: save-doc
    module: word
    action: save_document
    params:
      path: /reports/memo.docx
    depends_on: [add-body]
```

## Requirements

| Dependency | Version | Notes |
|------------|---------|-------|
| `python-docx` | >= 1.0 | Core document engine |
| `libreoffice` | any | Required only for `export_to_pdf` action |

Install with:
```bash
pip install python-docx
```

## Configuration

Uses default LLMOS Bridge configuration. Sandbox paths are enforced by the
upstream PermissionGuard via `SecurityConfig.sandbox_paths`. Documents are
cached in memory per session; the cache is keyed by source path.

## Platform Support

| Platform | Status |
|----------|--------|
| Linux | Supported |
| macOS | Supported |
| Windows | Supported |

## Related Modules

- **excel** -- Spreadsheet automation; combine with Word to embed data tables
  from Excel workbooks into Word reports.
- **powerpoint** -- Presentation automation; share content between Word
  documents and PowerPoint slides.
- **filesystem** -- Low-level file operations for managing document files.
- **database** -- Query databases and feed results into Word documents.
