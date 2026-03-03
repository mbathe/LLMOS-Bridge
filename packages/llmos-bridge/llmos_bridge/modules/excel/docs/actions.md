# Excel Module -- Action Reference

Complete reference for all 42 actions provided by the Excel module.

---

## create_workbook

Create a new blank Excel workbook (.xlsx) with an initial sheet.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path for the new .xlsx file |
| `sheet_name` | string | No | `"Sheet1"` | Name of the initial worksheet |

**Returns:** `{"path": str, "created": true, "sheet_names": list[str]}`

```yaml
- id: new-wb
  module: excel
  action: create_workbook
  params:
    path: /data/new.xlsx
```

**Security:** Permission `local_worker` required.

---

## open_workbook

Open an Excel workbook (.xlsx/.xlsm) and cache it for subsequent operations.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the .xlsx / .xlsm file |
| `read_only` | boolean | No | `false` | Open in read-only mode |
| `data_only` | boolean | No | `true` | Return cached formula results instead of formulas |
| `keep_vba` | boolean | No | `false` | Preserve VBA macros (.xlsm) |

**Returns:** `{"path": str, "sheet_names": list[str], "active_sheet": str}`

```yaml
- id: open
  module: excel
  action: open_workbook
  params:
    path: /data/sales.xlsx
```

**Security:** Permission `local_worker` required.

---

## close_workbook

Close a cached workbook, optionally saving it first.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the workbook |
| `save` | boolean | No | `false` | Save before closing |

**Returns:** `{"closed": true, "path": str}`

**Security:** Permission `local_worker` required.

---

## save_workbook

Save a workbook to disk (optionally to a new path).

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the workbook |
| `output_path` | string | No | -- | Save As path. Overwrites original if not set |

**Returns:** `{"saved": true, "path": str}`

**Security:** Permission `local_worker` required.

---

## get_workbook_info

Retrieve metadata about a workbook: sheets, named ranges, defined names.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the workbook |
| `include_named_ranges` | boolean | No | `true` | Include named ranges |
| `include_defined_names` | boolean | No | `true` | Include defined names |

**Returns:** `{"path": str, "sheet_names": list, "named_ranges": list, ...}`

**Security:** Permission `local_worker` required.

---

## list_sheets

List all worksheet names in a workbook.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the workbook |

**Returns:** `{"sheets": list[str]}`

**Security:** Permission `local_worker` required.

---

## create_sheet

Add a new worksheet to a workbook.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the workbook |
| `name` | string | Yes | -- | Name for the new sheet |
| `position` | integer | No | -- | Insert position (0-indexed). Appended if not set |

**Returns:** `{"created": true, "name": str, "sheet_names": list}`

**Security:** Permission `local_worker` required.

---

## delete_sheet

Delete a worksheet from a workbook.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the workbook |
| `name` | string | Yes | -- | Name of the sheet to delete |

**Returns:** `{"deleted": true, "sheet_names": list}`

**Security:** Permission `local_worker` required.

---

## rename_sheet

Rename a worksheet.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the workbook |
| `old_name` | string | Yes | -- | Current sheet name |
| `new_name` | string | Yes | -- | New sheet name |

**Returns:** `{"renamed": true, "old_name": str, "new_name": str}`

**Security:** Permission `local_worker` required.

---

## copy_sheet

Copy a worksheet within the same workbook.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the workbook |
| `source_sheet` | string | Yes | -- | Name of the sheet to copy |
| `new_name` | string | Yes | -- | Name for the copy |
| `position` | integer | No | -- | Insert position (0-indexed) |

**Returns:** `{"copied": true, "new_name": str, "sheet_names": list}`

**Security:** Permission `local_worker` required.

---

## get_sheet_info

Get information about a worksheet: dimensions, merged cells, etc.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the workbook |
| `sheet` | string | Yes | -- | Worksheet name |
| `include_dimensions` | boolean | No | `true` | Include dimension info |
| `include_merged_cells` | boolean | No | `true` | Include merged cell ranges |

**Returns:** `{"sheet": str, "dimensions": str, "merged_cells": list, ...}`

**Security:** Permission `local_worker` required.

---

## protect_sheet

Protect a worksheet with an optional password.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the workbook |
| `sheet` | string | Yes | -- | Worksheet name |
| `password` | string | No | -- | Protection password |
| `allow_select_locked` | boolean | No | `true` | Allow selecting locked cells |
| `allow_select_unlocked` | boolean | No | `true` | Allow selecting unlocked cells |
| `allow_format_cells` | boolean | No | `false` | Allow formatting cells |
| `allow_sort` | boolean | No | `false` | Allow sorting |
| `allow_auto_filter` | boolean | No | `false` | Allow auto-filter |
| `allow_insert_rows` | boolean | No | `false` | Allow inserting rows |
| `allow_delete_rows` | boolean | No | `false` | Allow deleting rows |

**Returns:** `{"protected": true, "sheet": str}`

**Security:** Permission `local_worker` required.

---

## unprotect_sheet

Remove protection from a worksheet.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the workbook |
| `sheet` | string | Yes | -- | Worksheet name |
| `password` | string | No | -- | Protection password |

**Returns:** `{"unprotected": true, "sheet": str}`

**Security:** Permission `local_worker` required.

---

## set_page_setup

Configure page setup (orientation, margins, paper size, print area) for a worksheet.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the workbook |
| `sheet` | string | Yes | -- | Worksheet name |
| `orientation` | string | No | `"portrait"` | Page orientation: portrait or landscape |
| `paper_size` | integer | No | `9` | Paper size code (9=A4, 1=Letter) |
| `fit_to_page` | boolean | No | `false` | Fit content to page |
| `top_margin` | number | No | `1.0` | Top margin in inches |
| `bottom_margin` | number | No | `1.0` | Bottom margin in inches |
| `left_margin` | number | No | `0.75` | Left margin in inches |
| `right_margin` | number | No | `0.75` | Right margin in inches |
| `print_area` | string | No | -- | Print area range, e.g. `A1:H50` |

**Returns:** `{"configured": true, "sheet": str}`

**Security:** Permission `local_worker` required.

---

## read_cell

Read the value of a single cell.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the workbook |
| `sheet` | string | Yes | -- | Worksheet name |
| `cell` | string | Yes | -- | Cell address, e.g. `B3` |

**Returns:** `{"cell": str, "value": Any, "data_type": str}`

**Security:** Permission `local_worker` required.

---

## write_cell

Write a value to a single cell.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the workbook |
| `sheet` | string | Yes | -- | Worksheet name |
| `cell` | string | Yes | -- | Cell address, e.g. `B3` |
| `value` | string | Yes | -- | Value to write |

**Returns:** `{"written": true, "cell": str, "value": Any}`

**Security:** Permission `local_worker` required.

---

## read_range

Read a rectangular range of cells from a worksheet. Use `range='auto'` for the entire used area.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the workbook |
| `sheet` | string | Yes | -- | Worksheet name |
| `range` | string | Yes | -- | Cell range (e.g. `A1:Z100`) or `auto` |
| `include_headers` | boolean | No | `true` | Treat first row as column headers |
| `as_dict` | boolean | No | `false` | Return list[dict] keyed by header |

**Returns:** `{"data": list[list], "row_count": int, "col_count": int}`

```yaml
- id: read-data
  module: excel
  action: read_range
  params:
    path: /data/sales.xlsx
    sheet: Sheet1
    range: "A1:D100"
    as_dict: true
```

**Security:** Permission `local_worker` required.

---

## write_range

Write a 2-D list of values starting at a given cell.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the workbook |
| `sheet` | string | Yes | -- | Worksheet name |
| `start_cell` | string | Yes | -- | Top-left cell of the range |
| `data` | array | Yes | -- | Row-major list of rows to write |

**Returns:** `{"written": true, "rows": int, "cells_written": int}`

**Security:** Permission `local_worker` required.

---

## copy_range

Copy a range of cells to another location (same or different sheet).

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the workbook |
| `source_sheet` | string | Yes | -- | Source worksheet name |
| `source_range` | string | Yes | -- | Source cell range |
| `dest_sheet` | string | Yes | -- | Destination worksheet name |
| `dest_cell` | string | Yes | -- | Top-left destination cell |
| `copy_values_only` | boolean | No | `false` | Copy only values, not formatting |

**Returns:** `{"copied": true, "cells_copied": int}`

**Security:** Permission `local_worker` required.

---

## insert_rows

Insert empty rows before a given row.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the workbook |
| `sheet` | string | Yes | -- | Worksheet name |
| `row` | integer | Yes | -- | Row number before which to insert (1-indexed) |
| `count` | integer | No | `1` | Number of rows to insert |

**Returns:** `{"inserted": true, "row": int, "count": int}`

**Security:** Permission `local_worker` required.

---

## delete_rows

Delete rows from a worksheet.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the workbook |
| `sheet` | string | Yes | -- | Worksheet name |
| `row` | integer | Yes | -- | First row to delete (1-indexed) |
| `count` | integer | No | `1` | Number of rows to delete |

**Returns:** `{"deleted": true, "row": int, "count": int}`

**Security:** Permission `local_worker` required.

---

## insert_columns

Insert empty columns before a given column.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the workbook |
| `sheet` | string | Yes | -- | Worksheet name |
| `column` | integer | Yes | -- | Column index before which to insert (1-indexed) |
| `count` | integer | No | `1` | Number of columns to insert |

**Returns:** `{"inserted": true, "column": int, "count": int}`

**Security:** Permission `local_worker` required.

---

## delete_columns

Delete columns from a worksheet.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the workbook |
| `sheet` | string | Yes | -- | Worksheet name |
| `column` | integer | Yes | -- | First column to delete (1-indexed) |
| `count` | integer | No | `1` | Number of columns to delete |

**Returns:** `{"deleted": true, "column": int, "count": int}`

**Security:** Permission `local_worker` required.

---

## set_column_width

Set column width or auto-fit.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the workbook |
| `sheet` | string | Yes | -- | Worksheet name |
| `column` | string | Yes | -- | Column letter or range, e.g. `A` or `A:D` |
| `width` | number | No | -- | Width in characters |
| `auto_fit` | boolean | No | `false` | Auto-fit based on cell content |

**Returns:** `{"set": true, "column": str}`

**Security:** Permission `local_worker` required.

---

## set_row_height

Set the height of a row.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the workbook |
| `sheet` | string | Yes | -- | Worksheet name |
| `row` | integer | Yes | -- | Row number (1-indexed) |
| `height` | number | Yes | -- | Row height in points |

**Returns:** `{"set": true, "row": int, "height": float}`

**Security:** Permission `local_worker` required.

---

## merge_cells

Merge a range of cells into one.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the workbook |
| `sheet` | string | Yes | -- | Worksheet name |
| `range` | string | Yes | -- | Range to merge, e.g. `A1:C3` |

**Returns:** `{"merged": true, "range": str}`

**Security:** Permission `local_worker` required.

---

## unmerge_cells

Split a merged cell range back into individual cells.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the workbook |
| `sheet` | string | Yes | -- | Worksheet name |
| `range` | string | Yes | -- | Merged range to split |

**Returns:** `{"unmerged": true, "range": str}`

**Security:** Permission `local_worker` required.

---

## freeze_panes

Freeze rows and/or columns at a given cell. Pass null to unfreeze.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the workbook |
| `sheet` | string | Yes | -- | Worksheet name |
| `cell` | string | No | -- | Cell at the top-left of the frozen region. Null to unfreeze |

**Returns:** `{"frozen": true, "cell": str | null}`

**Security:** Permission `local_worker` required.

---

## find_replace

Find and replace text across one or all worksheets.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the workbook |
| `sheet` | string | No | -- | Worksheet name. All sheets if not set |
| `find` | string | Yes | -- | Text to search for |
| `replace` | string | Yes | -- | Replacement text |
| `case_sensitive` | boolean | No | `false` | Case-sensitive search |
| `whole_cell` | boolean | No | `false` | Match entire cell content only |

**Returns:** `{"replacements": int, "cells_checked": int}`

**Security:** Permission `local_worker` required.

---

## remove_duplicates

Remove duplicate rows from a range.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the workbook |
| `sheet` | string | Yes | -- | Worksheet name |
| `range` | string | Yes | -- | Range to check, e.g. `A1:E100` |
| `columns` | array | No | -- | Column indices (0-based) to check. All if not set |
| `keep` | string | No | `"first"` | Which duplicate to keep: `first` or `last` |

**Returns:** `{"removed": int, "remaining": int}`

**Security:** Permission `local_worker` required.

---

## apply_formula

Write an Excel formula to a cell.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the workbook |
| `sheet` | string | Yes | -- | Worksheet name |
| `cell` | string | Yes | -- | Cell address |
| `formula` | string | Yes | -- | Excel formula, e.g. `=SUM(A1:A10)` |

**Returns:** `{"applied": true, "cell": str, "formula": str}`

```yaml
- id: sum
  module: excel
  action: apply_formula
  params:
    path: /data/report.xlsx
    sheet: Sales
    cell: E11
    formula: "=SUM(E2:E10)"
```

**Security:** Permission `local_worker` required.

---

## add_named_range

Define a named range in the workbook.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the workbook |
| `name` | string | Yes | -- | Name for the range |
| `sheet` | string | Yes | -- | Worksheet name |
| `range` | string | Yes | -- | Cell range, e.g. `A1:D100` |
| `scope` | string | No | `"workbook"` | Scope: `workbook` or `sheet` |

**Returns:** `{"added": true, "name": str}`

**Security:** Permission `local_worker` required.

---

## add_data_validation

Add data validation rules to a range (dropdown list, number range, etc.).

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the workbook |
| `sheet` | string | Yes | -- | Worksheet name |
| `range` | string | Yes | -- | Cell range to validate |
| `validation_type` | string | No | `"list"` | Type: list, whole, decimal, date, time, textLength, custom |
| `operator` | string | No | -- | Comparison operator |
| `formula1` | string | No | -- | First value / formula / list source |
| `formula2` | string | No | -- | Second value for `between` ranges |
| `allow_blank` | boolean | No | `true` | Allow blank cells |
| `show_dropdown` | boolean | No | `true` | Show dropdown list |
| `show_error_alert` | boolean | No | `true` | Show error alert on invalid input |
| `error_title` | string | No | -- | Error alert title |
| `error_message` | string | No | -- | Error alert message |

**Returns:** `{"added": true, "range": str, "type": str}`

**Security:** Permission `local_worker` required.

---

## add_autofilter

Enable auto-filter (dropdown arrows) on a range.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the workbook |
| `sheet` | string | Yes | -- | Worksheet name |
| `range` | string | No | -- | Range to apply filter. Uses used range if not set |

**Returns:** `{"added": true, "range": str}`

**Security:** Permission `local_worker` required.

---

## format_range

Apply font, fill, border, and alignment formatting to a cell range. Only specified properties are changed.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the workbook |
| `sheet` | string | Yes | -- | Worksheet name |
| `range` | string | Yes | -- | Cell range to format |
| `bold` | boolean | No | -- | Bold font |
| `italic` | boolean | No | -- | Italic font |
| `font_name` | string | No | -- | Font name, e.g. `Arial` |
| `font_size` | integer | No | -- | Font size in points |
| `font_color` | string | No | -- | Hex font colour (e.g. `FF0000`) |
| `fill_color` | string | No | -- | Hex background colour |
| `number_format` | string | No | -- | Number format string, e.g. `#,##0.00` |
| `alignment_horizontal` | string | No | -- | Horizontal: left, center, right |
| `alignment_vertical` | string | No | -- | Vertical: top, center, bottom |
| `wrap_text` | boolean | No | -- | Wrap text in cells |
| `border_style` | string | No | -- | Border style (thin, medium, thick, dashed, dotted, double, none) |
| `border_color` | string | No | -- | Hex border colour |

**Returns:** `{"formatted": true, "cells_formatted": int}`

**Security:** Permission `local_worker` required.

---

## apply_conditional_format

Apply conditional formatting (color scales, data bars, icon sets, cell rules) to a range.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the workbook |
| `sheet` | string | Yes | -- | Worksheet name |
| `range` | string | Yes | -- | Cell range |
| `format_type` | string | No | `"cell_is"` | Type: color_scale, data_bar, icon_set, cell_is, formula |
| `operator` | string | No | -- | Comparison operator for cell_is |
| `value` | string | No | -- | Comparison value |
| `bold` | boolean | No | -- | Bold font in matched cells |
| `font_color` | string | No | -- | Font colour for matched cells |
| `fill_color` | string | No | -- | Fill colour for matched cells |
| `min_color` | string | No | `"FFFFFF"` | Color-scale minimum colour |
| `max_color` | string | No | `"FF0000"` | Color-scale maximum colour |

**Returns:** `{"applied": true, "range": str}`

**Security:** Permission `local_worker` required.

---

## create_chart

Create a chart from a data range and embed it in the sheet.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the workbook |
| `sheet` | string | Yes | -- | Worksheet name |
| `chart_type` | string | Yes | -- | Type: bar, col, line, pie, doughnut, scatter, area, radar |
| `data_range` | string | Yes | -- | Data range for the chart |
| `title` | string | No | -- | Chart title |
| `x_axis_title` | string | No | -- | X-axis title |
| `y_axis_title` | string | No | -- | Y-axis title |
| `position` | string | No | `"E5"` | Anchor cell for the chart |
| `width` | integer | No | `15` | Chart width in centimetres |
| `height` | integer | No | `10` | Chart height in centimetres |
| `has_legend` | boolean | No | `true` | Show legend |

**Returns:** `{"created": true, "chart_type": str, "position": str}`

```yaml
- id: chart
  module: excel
  action: create_chart
  params:
    path: /data/report.xlsx
    sheet: Sales
    chart_type: bar
    data_range: "A1:B10"
    title: "Monthly Sales"
```

**Security:** Permission `local_worker` required.

---

## insert_image

Insert an image (PNG, JPEG, BMP, GIF) into a worksheet.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the workbook |
| `sheet` | string | Yes | -- | Worksheet name |
| `image_path` | string | Yes | -- | Path to the image file |
| `cell` | string | Yes | -- | Cell to anchor the image |
| `width` | number | No | -- | Width in pixels |
| `height` | number | No | -- | Height in pixels |

**Returns:** `{"inserted": true, "cell": str}`

**Security:** Permission `local_worker` required.

---

## add_comment

Add a comment to a cell.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the workbook |
| `sheet` | string | Yes | -- | Worksheet name |
| `cell` | string | Yes | -- | Cell address |
| `text` | string | Yes | -- | Comment text |
| `author` | string | No | `"LLMOS Bridge"` | Comment author |

**Returns:** `{"added": true, "cell": str}`

**Security:** Permission `local_worker` required.

---

## delete_comment

Delete a comment from a cell.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the workbook |
| `sheet` | string | Yes | -- | Worksheet name |
| `cell` | string | Yes | -- | Cell address |

**Returns:** `{"deleted": true, "cell": str}`

**Security:** Permission `local_worker` required.

---

## export_to_csv

Export a single worksheet to a CSV file.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the workbook |
| `sheet` | string | Yes | -- | Worksheet name |
| `output_path` | string | Yes | -- | Destination CSV file path |
| `delimiter` | string | No | `","` | CSV delimiter |
| `encoding` | string | No | `"utf-8"` | Output file encoding |
| `include_header` | boolean | No | `true` | Include header row |

**Returns:** `{"exported": true, "output_path": str, "rows_written": int}`

**Security:** Permission `local_worker` required.

---

## export_to_pdf

Convert the workbook to PDF using LibreOffice (must be installed).

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the workbook |
| `output_path` | string | Yes | -- | Destination PDF file path |
| `sheet` | string | No | -- | Sheet to export. All sheets if not set |
| `use_libreoffice` | boolean | No | `true` | Use LibreOffice for conversion |

**Returns:** `{"exported": true, "output_path": str}`

**Security:** Permission `local_worker` required.
