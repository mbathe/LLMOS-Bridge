"""Typed parameter models for the ``powerpoint`` module — full python-pptx feature coverage."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Presentation lifecycle
# ---------------------------------------------------------------------------


class CreatePresentationParams(BaseModel):
    output_path: str = Field(description="Path where the new .pptx will be saved.")
    slide_width: float | None = Field(
        default=None, description="Slide width in cm. Default: 33.87 cm (widescreen 16:9)."
    )
    slide_height: float | None = Field(
        default=None, description="Slide height in cm. Default: 19.05 cm."
    )
    theme_path: str | None = Field(
        default=None, description="Path to a .thmx or .pptx file to copy theme from."
    )


class OpenPresentationParams(BaseModel):
    path: str = Field(description="Path to an existing .pptx file.")


class SavePresentationParams(BaseModel):
    path: str
    output_path: str | None = Field(
        default=None, description="Save As path. Overwrites original if None."
    )


class GetPresentationInfoParams(BaseModel):
    path: str


# ---------------------------------------------------------------------------
# Slide management
# ---------------------------------------------------------------------------


class AddSlideParams(BaseModel):
    path: str
    layout_index: Annotated[int, Field(ge=0)] = Field(
        default=1, description="Slide layout index (0=blank, 1=title+content, 2=title, ...)."
    )
    title: str | None = None
    position: int | None = Field(
        default=None, description="Insert at this position (0-indexed). Appended if None."
    )


class DeleteSlideParams(BaseModel):
    path: str
    slide_index: Annotated[int, Field(ge=0)] = Field(description="0-indexed slide to delete.")


class DuplicateSlideParams(BaseModel):
    path: str
    slide_index: Annotated[int, Field(ge=0)]
    insert_after: int | None = Field(
        default=None, description="Index after which to insert the copy. End of deck if None."
    )


class ReorderSlideParams(BaseModel):
    path: str
    from_index: Annotated[int, Field(ge=0)]
    to_index: Annotated[int, Field(ge=0)]


class ListSlidesParams(BaseModel):
    path: str


class ReadSlideParams(BaseModel):
    path: str
    slide_index: Annotated[int, Field(ge=0)]
    include_notes: bool = True
    include_shapes: bool = True


class SetSlideLayoutParams(BaseModel):
    path: str
    slide_index: Annotated[int, Field(ge=0)]
    layout_index: Annotated[int, Field(ge=0)]


# ---------------------------------------------------------------------------
# Slide content — text
# ---------------------------------------------------------------------------


class SetSlideTitleParams(BaseModel):
    path: str
    slide_index: Annotated[int, Field(ge=0)]
    title: str
    bold: bool = False
    font_size: int | None = Field(default=None, ge=6, le=144)
    font_color: str | None = Field(default=None, description="Hex colour, e.g. 'FF0000'.")


class AddTextBoxParams(BaseModel):
    path: str
    slide_index: Annotated[int, Field(ge=0)]
    text: str
    left: float = Field(description="Left position in cm from slide left edge.")
    top: float = Field(description="Top position in cm from slide top edge.")
    width: float = Field(description="Width in cm.")
    height: float = Field(description="Height in cm.")
    bold: bool = False
    italic: bool = False
    underline: bool = False
    font_name: str | None = None
    font_size: int | None = Field(default=None, ge=6, le=144)
    font_color: str | None = Field(default=None, description="Hex colour.")
    bg_color: str | None = Field(default=None, description="Background hex colour.")
    alignment: Literal["left", "center", "right", "justify"] = "left"
    vertical_alignment: Literal["top", "middle", "bottom"] = "top"
    word_wrap: bool = True


class AddSlideNotesParams(BaseModel):
    path: str
    slide_index: Annotated[int, Field(ge=0)]
    notes: str
    append: bool = Field(default=False, description="Append to existing notes instead of replacing.")


# ---------------------------------------------------------------------------
# Slide content — shapes
# ---------------------------------------------------------------------------


class AddShapeParams(BaseModel):
    path: str
    slide_index: Annotated[int, Field(ge=0)]
    shape_type: Literal[
        "rectangle", "rounded_rectangle", "ellipse", "triangle",
        "right_arrow", "left_arrow", "up_arrow", "down_arrow",
        "pentagon", "hexagon", "star4", "star5", "star8",
        "callout", "cloud", "lightning", "heart", "checkmark",
        "line", "connector",
    ] = "rectangle"
    left: float = Field(description="Left position in cm.")
    top: float = Field(description="Top position in cm.")
    width: float = Field(description="Width in cm.")
    height: float = Field(description="Height in cm.")
    fill_color: str | None = Field(default=None, description="Fill hex colour.")
    line_color: str | None = Field(default=None, description="Border hex colour.")
    line_width: float | None = Field(default=None, description="Border width in pt.")
    text: str | None = None
    font_size: int | None = Field(default=None, ge=6, le=144)
    font_color: str | None = None
    transparency: Annotated[float, Field(ge=0.0, le=1.0)] | None = None


class FormatShapeParams(BaseModel):
    path: str
    slide_index: Annotated[int, Field(ge=0)]
    shape_index: Annotated[int, Field(ge=0)] = Field(description="0-indexed shape on slide.")
    fill_color: str | None = None
    line_color: str | None = None
    line_width: float | None = None
    transparency: Annotated[float, Field(ge=0.0, le=1.0)] | None = None
    shadow: bool | None = None
    rotation: float | None = Field(default=None, description="Rotation in degrees (clockwise).")


class AddImageParams(BaseModel):
    path: str
    slide_index: Annotated[int, Field(ge=0)]
    image_path: str = Field(description="Path to image file (PNG, JPEG, GIF, BMP, SVG).")
    left: float = Field(description="Left position in cm.")
    top: float = Field(description="Top position in cm.")
    width: float | None = Field(default=None, description="Width in cm. Auto-scale if None.")
    height: float | None = Field(default=None, description="Height in cm. Auto-scale if None.")
    maintain_aspect: bool = True


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------


class AddChartParams(BaseModel):
    path: str
    slide_index: Annotated[int, Field(ge=0)]
    chart_type: Literal["bar", "col", "line", "pie", "doughnut", "scatter", "area", "bubble", "radar"] = "col"
    # Data as dict: {"categories": [...], "series": [{"name": str, "values": [...]}]}
    data: dict[str, Any] = Field(description="Chart data: {categories: [...], series: [{name, values}]}")
    left: float = Field(description="Left position in cm.")
    top: float = Field(description="Top position in cm.")
    width: float = Field(default=14, description="Width in cm.")
    height: float = Field(default=10, description="Height in cm.")
    title: str | None = None
    has_legend: bool = True
    has_data_labels: bool = False
    style: Annotated[int, Field(ge=1, le=48)] = 2


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------


class AddTableParams(BaseModel):
    path: str
    slide_index: Annotated[int, Field(ge=0)]
    rows: Annotated[int, Field(ge=1, le=100)]
    cols: Annotated[int, Field(ge=1, le=30)]
    data: list[list[str]] | None = Field(
        default=None, description="Row-major list of cell values."
    )
    left: float = Field(description="Left position in cm.")
    top: float = Field(description="Top position in cm.")
    width: float = Field(default=20, description="Table width in cm.")
    height: float = Field(default=10, description="Table height in cm.")
    has_header: bool = True
    header_bg_color: str = Field(default="404040", description="Header row background hex colour.")
    header_font_color: str = Field(default="FFFFFF", description="Header row font hex colour.")
    alt_row_color: str | None = Field(
        default=None, description="Alternating row background hex colour."
    )
    font_size: int = Field(default=14, ge=6, le=72)


class FormatTableCellParams(BaseModel):
    path: str
    slide_index: Annotated[int, Field(ge=0)]
    shape_index: Annotated[int, Field(ge=0)] = Field(description="0-indexed table shape.")
    row: Annotated[int, Field(ge=0)]
    col: Annotated[int, Field(ge=0)]
    text: str | None = None
    bold: bool | None = None
    italic: bool | None = None
    font_size: int | None = None
    font_color: str | None = None
    bg_color: str | None = None
    alignment: Literal["left", "center", "right"] | None = None


# ---------------------------------------------------------------------------
# Background & theme
# ---------------------------------------------------------------------------


class SetSlideBackgroundParams(BaseModel):
    path: str
    slide_index: Annotated[int, Field(ge=0)] | None = Field(
        default=None, description="Slide index. All slides if None."
    )
    color: str | None = Field(default=None, description="Solid background hex colour.")
    image_path: str | None = Field(default=None, description="Path to background image file.")
    gradient: dict[str, Any] | None = Field(
        default=None,
        description="Gradient: {type: 'linear'|'radial', stops: [{position: 0.0, color: 'hex'}, ...], angle: 45}",
    )


class ApplyThemeParams(BaseModel):
    path: str
    theme_path: str = Field(description="Path to a .pptx file whose theme will be copied.")


class AddTransitionParams(BaseModel):
    path: str
    slide_index: Annotated[int, Field(ge=0)] | None = Field(
        default=None, description="Slide index. All slides if None."
    )
    transition: Literal["none", "fade", "push", "wipe", "split", "reveal", "random"] = "fade"
    duration: float = Field(default=1.0, ge=0.1, le=10.0, description="Duration in seconds.")
    advance_on_click: bool = True
    advance_after: float | None = Field(
        default=None, description="Auto-advance after N seconds. None = no auto-advance."
    )


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


class ExportToPdfParams(BaseModel):
    path: str
    output_path: str = Field(description="Destination PDF path.")
    use_libreoffice: bool = True


class ExportSlideAsImageParams(BaseModel):
    path: str
    slide_index: Annotated[int, Field(ge=0)]
    output_path: str = Field(description="Destination image path (PNG/JPEG).")
    width: int = Field(default=1920, ge=100, description="Output image width in pixels.")
    use_libreoffice: bool = Field(
        default=True, description="Use LibreOffice for rendering."
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

PARAMS_MAP: dict[str, type[BaseModel]] = {
    # Lifecycle
    "create_presentation": CreatePresentationParams,
    "open_presentation": OpenPresentationParams,
    "save_presentation": SavePresentationParams,
    "get_presentation_info": GetPresentationInfoParams,
    # Slide management
    "add_slide": AddSlideParams,
    "delete_slide": DeleteSlideParams,
    "duplicate_slide": DuplicateSlideParams,
    "reorder_slide": ReorderSlideParams,
    "list_slides": ListSlidesParams,
    "read_slide": ReadSlideParams,
    "set_slide_layout": SetSlideLayoutParams,
    # Text content
    "set_slide_title": SetSlideTitleParams,
    "add_text_box": AddTextBoxParams,
    "add_slide_notes": AddSlideNotesParams,
    # Shapes
    "add_shape": AddShapeParams,
    "format_shape": FormatShapeParams,
    "add_image": AddImageParams,
    # Charts
    "add_chart": AddChartParams,
    # Tables
    "add_table": AddTableParams,
    "format_table_cell": FormatTableCellParams,
    # Background & theme
    "set_slide_background": SetSlideBackgroundParams,
    "apply_theme": ApplyThemeParams,
    "add_transition": AddTransitionParams,
    # Export
    "export_to_pdf": ExportToPdfParams,
    "export_slide_as_image": ExportSlideAsImageParams,
}
