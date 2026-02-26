"""Typed parameter models for the ``excel`` module — full openpyxl feature coverage."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

_RANGE_EXAMPLE = "A1:Z100"
_CELL_EXAMPLE = "B3"


# ---------------------------------------------------------------------------
# Workbook lifecycle
# ---------------------------------------------------------------------------


class OpenWorkbookParams(BaseModel):
    path: str = Field(description="Path to the .xlsx / .xlsm file.")
    read_only: bool = False
    data_only: bool = Field(
        default=True,
        description="If True, return cached formula results instead of formulas.",
    )
    keep_vba: bool = Field(default=False, description="Preserve VBA macros (.xlsm).")


class CreateWorkbookParams(BaseModel):
    path: str = Field(description="Path for the new .xlsx file.")
    sheet_name: str = Field(default="Sheet1", description="Name of the initial worksheet.")


class CloseWorkbookParams(BaseModel):
    path: str
    save: bool = Field(default=False, description="Save before closing.")


class SaveWorkbookParams(BaseModel):
    path: str
    output_path: str | None = Field(
        default=None, description="Save As path. Overwrites original if None."
    )


class GetWorkbookInfoParams(BaseModel):
    path: str
    include_named_ranges: bool = True
    include_defined_names: bool = True


# ---------------------------------------------------------------------------
# Sheet management
# ---------------------------------------------------------------------------


class CreateSheetParams(BaseModel):
    path: str
    name: str = Field(description="Name for the new sheet.")
    position: int | None = Field(
        default=None, description="Insert position (0-indexed). Appended if None."
    )


class DeleteSheetParams(BaseModel):
    path: str
    name: str


class RenameSheetParams(BaseModel):
    path: str
    old_name: str
    new_name: str


class CopySheetParams(BaseModel):
    path: str
    source_sheet: str
    new_name: str
    position: int | None = None


class ListSheetsParams(BaseModel):
    path: str


class GetSheetInfoParams(BaseModel):
    path: str
    sheet: str
    include_dimensions: bool = True
    include_merged_cells: bool = True


class ProtectSheetParams(BaseModel):
    path: str
    sheet: str
    password: str | None = None
    allow_select_locked: bool = True
    allow_select_unlocked: bool = True
    allow_format_cells: bool = False
    allow_sort: bool = False
    allow_auto_filter: bool = False
    allow_insert_rows: bool = False
    allow_delete_rows: bool = False


class UnprotectSheetParams(BaseModel):
    path: str
    sheet: str
    password: str | None = None


class SetPageSetupParams(BaseModel):
    path: str
    sheet: str
    orientation: Literal["portrait", "landscape"] = "portrait"
    paper_size: Annotated[int, Field(ge=1, le=100)] = 9  # 9 = A4
    fit_to_page: bool = False
    fit_to_width: int = 1
    fit_to_height: int = 0
    scale: Annotated[int, Field(ge=10, le=400)] | None = None
    top_margin: float = 1.0
    bottom_margin: float = 1.0
    left_margin: float = 0.75
    right_margin: float = 0.75
    header_margin: float = 0.5
    footer_margin: float = 0.5
    print_area: str | None = Field(default=None, description="Print area range, e.g. 'A1:H50'.")
    print_title_rows: str | None = Field(default=None, description="Rows to repeat at top, e.g. '1:2'.")
    print_title_cols: str | None = Field(default=None, description="Cols to repeat at left, e.g. 'A:B'.")


# ---------------------------------------------------------------------------
# Cell & range operations
# ---------------------------------------------------------------------------


class ReadCellParams(BaseModel):
    path: str
    sheet: str
    cell: str = Field(description=f"Cell address, e.g. '{_CELL_EXAMPLE}'.")


class WriteCellParams(BaseModel):
    path: str
    sheet: str
    cell: str = Field(description=f"Cell address, e.g. '{_CELL_EXAMPLE}'.")
    value: str | int | float | bool | None


class ReadRangeParams(BaseModel):
    path: str
    sheet: str
    range: str = Field(description=f"Cell range, e.g. '{_RANGE_EXAMPLE}'. Use 'auto' for used range.")
    include_headers: bool = Field(default=True, description="Treat first row as column headers.")
    as_dict: bool = Field(default=False, description="If True, return list[dict] keyed by header.")


class WriteRangeParams(BaseModel):
    path: str
    sheet: str
    start_cell: str = Field(description="Top-left cell of the range to write.")
    data: list[list[str | int | float | bool | None]] = Field(
        description="Row-major list of rows to write."
    )


class CopyRangeParams(BaseModel):
    path: str
    source_sheet: str
    source_range: str
    dest_sheet: str
    dest_cell: str = Field(description="Top-left destination cell.")
    copy_values_only: bool = Field(default=False, description="If True, copy only values, not formatting.")


class InsertRowsParams(BaseModel):
    path: str
    sheet: str
    row: int = Field(ge=1, description="Row number before which to insert (1-indexed).")
    count: Annotated[int, Field(ge=1, le=1000)] = 1


class DeleteRowsParams(BaseModel):
    path: str
    sheet: str
    row: int = Field(ge=1, description="First row to delete (1-indexed).")
    count: Annotated[int, Field(ge=1, le=1000)] = 1


class InsertColumnsParams(BaseModel):
    path: str
    sheet: str
    column: int = Field(ge=1, description="Column index before which to insert (1-indexed).")
    count: Annotated[int, Field(ge=1, le=500)] = 1


class DeleteColumnsParams(BaseModel):
    path: str
    sheet: str
    column: int = Field(ge=1, description="First column to delete (1-indexed).")
    count: Annotated[int, Field(ge=1, le=500)] = 1


class MergeCellsParams(BaseModel):
    path: str
    sheet: str
    range: str = Field(description="Range to merge, e.g. 'A1:C3'.")


class UnmergeCellsParams(BaseModel):
    path: str
    sheet: str
    range: str = Field(description="Merged range to split.")


class FreezePanesParams(BaseModel):
    path: str
    sheet: str
    cell: str | None = Field(
        default=None,
        description="Cell at the top-left of the frozen region, e.g. 'B2'. None to unfreeze.",
    )


class SetColumnWidthParams(BaseModel):
    path: str
    sheet: str
    column: str = Field(description="Column letter or range, e.g. 'A' or 'A:D'.")
    width: float | None = Field(default=None, description="Width in characters. None = auto-fit.")
    auto_fit: bool = Field(default=False, description="Auto-fit based on cell content.")


class SetRowHeightParams(BaseModel):
    path: str
    sheet: str
    row: int = Field(ge=1, description="Row number (1-indexed).")
    height: float = Field(ge=0, le=409, description="Row height in points.")


class FindReplaceParams(BaseModel):
    path: str
    sheet: str | None = Field(default=None, description="Sheet name. All sheets if None.")
    find: str
    replace: str
    case_sensitive: bool = False
    whole_cell: bool = False


class RemoveDuplicatesParams(BaseModel):
    path: str
    sheet: str
    range: str = Field(description="Range to check, e.g. 'A1:E100'.")
    columns: list[int] | None = Field(
        default=None,
        description="Column indices (0-based within range) to check. All columns if None.",
    )
    keep: Literal["first", "last"] = "first"


# ---------------------------------------------------------------------------
# Formulas & logic
# ---------------------------------------------------------------------------


class ApplyFormulaParams(BaseModel):
    path: str
    sheet: str
    cell: str
    formula: str = Field(description="Excel formula string, e.g. '=SUM(A1:A10)'.")


class AddNamedRangeParams(BaseModel):
    path: str
    name: str
    sheet: str
    range: str
    scope: Literal["workbook", "sheet"] = "workbook"


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


class FormatRangeParams(BaseModel):
    path: str
    sheet: str
    range: str
    bold: bool | None = None
    italic: bool | None = None
    underline: bool | None = None
    strikethrough: bool | None = None
    font_name: str | None = Field(default=None, description="Font name, e.g. 'Arial'.")
    font_size: Annotated[int, Field(ge=6, le=72)] | None = None
    font_color: str | None = Field(default=None, description="Hex colour, e.g. 'FF0000'.")
    fill_color: str | None = Field(default=None, description="Hex background colour.")
    fill_type: Literal["solid", "none"] | None = "solid"
    number_format: str | None = Field(
        default=None, description="Excel number format string, e.g. '#,##0.00'."
    )
    alignment_horizontal: Literal["left", "center", "right", "fill", "justify", "centerContinuous", "distributed"] | None = None
    alignment_vertical: Literal["top", "center", "bottom", "justify", "distributed"] | None = None
    wrap_text: bool | None = None
    border_style: Literal["thin", "medium", "thick", "dashed", "dotted", "double", "none"] | None = None
    border_color: str | None = None
    border_sides: list[Literal["left", "right", "top", "bottom", "outline", "all"]] | None = None


class ApplyConditionalFormatParams(BaseModel):
    path: str
    sheet: str
    range: str
    format_type: Literal["color_scale", "data_bar", "icon_set", "cell_is", "formula"] = "cell_is"
    # For cell_is
    operator: Literal["greaterThan", "lessThan", "greaterThanOrEqual", "lessThanOrEqual", "equal", "notEqual", "between", "notBetween"] | None = None
    value: str | float | None = None
    value2: str | float | None = Field(default=None, description="Second value for 'between'/'notBetween'.")
    # Formatting to apply
    bold: bool | None = None
    font_color: str | None = None
    fill_color: str | None = None
    # For color_scale
    min_color: str = Field(default="FFFFFF", description="Hex color for minimum value.")
    mid_color: str | None = None
    max_color: str = Field(default="FF0000", description="Hex color for maximum value.")
    # For formula
    formula: str | None = None


class AddDataValidationParams(BaseModel):
    path: str
    sheet: str
    range: str
    validation_type: Literal["list", "whole", "decimal", "date", "time", "textLength", "custom"] = "list"
    operator: Literal["between", "notBetween", "equal", "notEqual", "greaterThan", "lessThan", "greaterThanOrEqual", "lessThanOrEqual"] | None = None
    formula1: str | None = Field(default=None, description="First value / formula / list source.")
    formula2: str | None = Field(default=None, description="Second value for 'between' ranges.")
    allow_blank: bool = True
    show_dropdown: bool = True
    show_input_message: bool = False
    input_title: str | None = None
    input_message: str | None = None
    show_error_alert: bool = True
    error_title: str | None = None
    error_message: str | None = None
    error_style: Literal["stop", "warning", "information"] = "stop"


class AddAutoFilterParams(BaseModel):
    path: str
    sheet: str
    range: str | None = Field(
        default=None, description="Range to apply filter. Uses used range if None."
    )


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------


class CreateChartParams(BaseModel):
    path: str
    sheet: str
    chart_type: Literal["bar", "col", "line", "pie", "doughnut", "scatter", "area", "bubble", "radar", "stock"] = "bar"
    data_range: str = Field(description="Data range for the chart, e.g. 'A1:B10'.")
    title: str | None = None
    x_axis_title: str | None = None
    y_axis_title: str | None = None
    position: str = Field(default="E5", description="Cell where the chart is anchored.")
    width: int = Field(default=15, description="Chart width in centimetres.")
    height: int = Field(default=10, description="Chart height in centimetres.")
    style: Annotated[int, Field(ge=1, le=48)] = 2
    has_legend: bool = True
    grouping: Literal["clustered", "stacked", "percentStacked", "standard"] = "clustered"
    smooth: bool = Field(default=False, description="Smooth lines (for line/scatter charts).")
    series_labels: bool = Field(default=True, description="Use first row/column as series labels.")


# ---------------------------------------------------------------------------
# Images & media
# ---------------------------------------------------------------------------


class InsertImageParams(BaseModel):
    path: str
    sheet: str
    image_path: str = Field(description="Path to image file (PNG, JPEG, BMP, GIF).")
    cell: str = Field(description="Cell to anchor the image, e.g. 'B5'.")
    width: float | None = Field(default=None, description="Width in pixels.")
    height: float | None = Field(default=None, description="Height in pixels.")


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------


class AddCommentParams(BaseModel):
    path: str
    sheet: str
    cell: str
    text: str
    author: str = Field(default="LLMOS Bridge")
    width: int = Field(default=200, description="Comment box width in pixels.")
    height: int = Field(default=100, description="Comment box height in pixels.")


class DeleteCommentParams(BaseModel):
    path: str
    sheet: str
    cell: str


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


class ExportToCsvParams(BaseModel):
    path: str
    sheet: str
    output_path: str
    delimiter: str = ","
    encoding: str = "utf-8"
    include_header: bool = True


class ExportToPdfParams(BaseModel):
    path: str
    output_path: str
    sheet: str | None = Field(
        default=None, description="Sheet to export. All sheets if None."
    )
    use_libreoffice: bool = Field(
        default=True, description="Use LibreOffice for conversion (requires libreoffice in PATH)."
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

PARAMS_MAP: dict[str, type[BaseModel]] = {
    # Workbook lifecycle
    "create_workbook": CreateWorkbookParams,
    "open_workbook": OpenWorkbookParams,
    "close_workbook": CloseWorkbookParams,
    "save_workbook": SaveWorkbookParams,
    "get_workbook_info": GetWorkbookInfoParams,
    # Sheet management
    "create_sheet": CreateSheetParams,
    "delete_sheet": DeleteSheetParams,
    "rename_sheet": RenameSheetParams,
    "copy_sheet": CopySheetParams,
    "list_sheets": ListSheetsParams,
    "get_sheet_info": GetSheetInfoParams,
    "protect_sheet": ProtectSheetParams,
    "unprotect_sheet": UnprotectSheetParams,
    "set_page_setup": SetPageSetupParams,
    # Cell & range operations
    "read_cell": ReadCellParams,
    "write_cell": WriteCellParams,
    "read_range": ReadRangeParams,
    "write_range": WriteRangeParams,
    "copy_range": CopyRangeParams,
    "insert_rows": InsertRowsParams,
    "delete_rows": DeleteRowsParams,
    "insert_columns": InsertColumnsParams,
    "delete_columns": DeleteColumnsParams,
    "merge_cells": MergeCellsParams,
    "unmerge_cells": UnmergeCellsParams,
    "freeze_panes": FreezePanesParams,
    "set_column_width": SetColumnWidthParams,
    "set_row_height": SetRowHeightParams,
    "find_replace": FindReplaceParams,
    "remove_duplicates": RemoveDuplicatesParams,
    # Formulas & logic
    "apply_formula": ApplyFormulaParams,
    "add_named_range": AddNamedRangeParams,
    # Formatting
    "format_range": FormatRangeParams,
    "apply_conditional_format": ApplyConditionalFormatParams,
    "add_data_validation": AddDataValidationParams,
    "add_autofilter": AddAutoFilterParams,
    # Charts
    "create_chart": CreateChartParams,
    # Images
    "insert_image": InsertImageParams,
    # Comments
    "add_comment": AddCommentParams,
    "delete_comment": DeleteCommentParams,
    # Export
    "export_to_csv": ExportToCsvParams,
    "export_to_pdf": ExportToPdfParams,
}
