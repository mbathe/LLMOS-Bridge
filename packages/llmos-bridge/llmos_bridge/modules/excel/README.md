# Excel Module

Full-featured Excel spreadsheet automation using openpyxl.

## Overview

The Excel module provides comprehensive `.xlsx` and `.xlsm` workbook
automation for IML plans. It covers the full lifecycle of spreadsheet work:
creating and opening workbooks, reading and writing cells and ranges, sheet
management, formatting, charting, data validation, conditional formatting,
formulas, comments, and export to CSV and PDF. All blocking I/O is offloaded
to a thread via `asyncio.to_thread` so the async event loop is never blocked.
Workbooks are cached in memory to avoid redundant disk reads within a session.

## Actions

| Action | Description | Risk | Permission |
|--------|-------------|------|------------|
| `create_workbook` | Create a new blank Excel workbook with an initial sheet | Medium | `filesystem_write` |
| `open_workbook` | Open an Excel workbook and cache it for subsequent operations | Medium | `filesystem_read` |
| `close_workbook` | Close a cached workbook, optionally saving it first | Medium | `filesystem_write` |
| `save_workbook` | Save a workbook to disk (optionally to a new path) | Medium | `filesystem_write` |
| `get_workbook_info` | Retrieve metadata: sheets, named ranges, defined names | Medium | `filesystem_read` |
| `list_sheets` | List all worksheet names in a workbook | Medium | `filesystem_read` |
| `create_sheet` | Add a new worksheet to a workbook | Medium | `filesystem_write` |
| `delete_sheet` | Delete a worksheet from a workbook | Medium | `filesystem_write` |
| `rename_sheet` | Rename a worksheet | Medium | `filesystem_write` |
| `copy_sheet` | Copy a worksheet within the same workbook | Medium | `filesystem_write` |
| `get_sheet_info` | Get info about a worksheet: dimensions, merged cells | Medium | `filesystem_read` |
| `protect_sheet` | Protect a worksheet with an optional password | Medium | `filesystem_write` |
| `unprotect_sheet` | Remove protection from a worksheet | Medium | `filesystem_write` |
| `set_page_setup` | Configure page setup (orientation, margins, paper size) | Medium | `filesystem_write` |
| `read_cell` | Read the value of a single cell | Medium | `filesystem_read` |
| `write_cell` | Write a value to a single cell | Medium | `filesystem_write` |
| `read_range` | Read a rectangular range of cells | Medium | `filesystem_read` |
| `write_range` | Write a 2-D list of values starting at a given cell | Medium | `filesystem_write` |
| `copy_range` | Copy a range of cells to another location | Medium | `filesystem_write` |
| `insert_rows` | Insert empty rows before a given row | Medium | `filesystem_write` |
| `delete_rows` | Delete rows from a worksheet | Medium | `filesystem_write` |
| `insert_columns` | Insert empty columns before a given column | Medium | `filesystem_write` |
| `delete_columns` | Delete columns from a worksheet | Medium | `filesystem_write` |
| `set_column_width` | Set column width or auto-fit | Medium | `filesystem_write` |
| `set_row_height` | Set the height of a row | Medium | `filesystem_write` |
| `merge_cells` | Merge a range of cells into one | Medium | `filesystem_write` |
| `unmerge_cells` | Split a merged cell range back into individual cells | Medium | `filesystem_write` |
| `freeze_panes` | Freeze rows and/or columns at a given cell | Medium | `filesystem_write` |
| `find_replace` | Find and replace text across one or all worksheets | Medium | `filesystem_write` |
| `remove_duplicates` | Remove duplicate rows from a range | Medium | `filesystem_write` |
| `apply_formula` | Write an Excel formula to a cell | Medium | `filesystem_write` |
| `add_named_range` | Define a named range in the workbook | Medium | `filesystem_write` |
| `add_data_validation` | Add data validation rules to a range | Medium | `filesystem_write` |
| `add_autofilter` | Enable auto-filter (dropdown arrows) on a range | Medium | `filesystem_write` |
| `format_range` | Apply font, fill, border, and alignment formatting | Medium | `filesystem_write` |
| `apply_conditional_format` | Apply conditional formatting to a range | Medium | `filesystem_write` |
| `create_chart` | Create a chart from a data range and embed it in the sheet | Medium | `filesystem_write` |
| `insert_image` | Insert an image into a worksheet | Medium | `filesystem_read`, `filesystem_write` |
| `add_comment` | Add a comment to a cell | Medium | `filesystem_write` |
| `delete_comment` | Delete a comment from a cell | Medium | `filesystem_write` |
| `export_to_csv` | Export a single worksheet to a CSV file | Medium | `filesystem_write` |
| `export_to_pdf` | Convert the workbook to PDF using LibreOffice | Medium | `filesystem_write` |

## Quick Start

```yaml
actions:
  - id: create-report
    module: excel
    action: create_workbook
    params:
      path: /data/report.xlsx
      sheet_name: Sales

  - id: write-headers
    module: excel
    action: write_range
    params:
      path: /data/report.xlsx
      sheet: Sales
      start_cell: A1
      data:
        - ["Product", "Q1", "Q2", "Q3", "Q4"]
    depends_on: [create-report]

  - id: format-headers
    module: excel
    action: format_range
    params:
      path: /data/report.xlsx
      sheet: Sales
      range: "A1:E1"
      bold: true
      fill_color: "4472C4"
      font_color: "FFFFFF"
    depends_on: [write-headers]

  - id: save-report
    module: excel
    action: save_workbook
    params:
      path: /data/report.xlsx
    depends_on: [format-headers]
```

## Requirements

| Dependency | Version | Notes |
|------------|---------|-------|
| `openpyxl` | >= 3.1.0 | Core spreadsheet engine |
| `libreoffice` | any | Required only for `export_to_pdf` action |

Install with:
```bash
pip install openpyxl
```

## Configuration

Uses default LLMOS Bridge configuration. Sandbox paths are enforced by the
upstream PermissionGuard via `SecurityConfig.sandbox_paths`. Workbooks are
cached in memory per session; call `close_workbook` to release memory.

## Platform Support

| Platform | Status |
|----------|--------|
| Linux | Supported |
| macOS | Supported |
| Windows | Supported |

## Related Modules

- **word** -- Document automation for `.docx` files; combine with Excel to
  embed spreadsheet data in Word reports.
- **powerpoint** -- Presentation automation; use Excel data to build charts
  and tables in slides.
- **filesystem** -- Low-level file operations; useful for managing workbook
  files before/after processing.
- **database** -- Query databases and feed results into Excel workbooks.
