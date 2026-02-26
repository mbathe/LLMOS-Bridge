"""Typed parameter models for the ``word`` module — full python-docx feature coverage."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Document lifecycle
# ---------------------------------------------------------------------------


class OpenDocumentParams(BaseModel):
    path: str = Field(description="Path to the .docx file.")


class CreateDocumentParams(BaseModel):
    output_path: str = Field(description="Path where the new document will be saved.")
    title: str | None = None
    author: str | None = None
    subject: str | None = None
    description: str | None = None
    default_font: str | None = Field(default=None, description="Default font name, e.g. 'Calibri'.")
    default_font_size: int | None = Field(default=None, ge=6, le=72, description="Default font size in pt.")


class SaveDocumentParams(BaseModel):
    path: str
    output_path: str | None = Field(
        default=None, description="Save As path. Overwrites original if None."
    )


class SetDocumentPropertiesParams(BaseModel):
    path: str
    title: str | None = None
    author: str | None = None
    subject: str | None = None
    description: str | None = None
    keywords: str | None = None
    category: str | None = None
    company: str | None = None
    language: str | None = None


class GetDocumentMetaParams(BaseModel):
    path: str


class SetMarginsParams(BaseModel):
    path: str
    top: float = Field(default=2.54, description="Top margin in cm.")
    bottom: float = Field(default=2.54, description="Bottom margin in cm.")
    left: float = Field(default=3.17, description="Left margin in cm.")
    right: float = Field(default=3.17, description="Right margin in cm.")
    section: int = Field(default=0, ge=0, description="0-indexed section.")


class SetDefaultFontParams(BaseModel):
    path: str
    font_name: str = Field(description="Font name, e.g. 'Times New Roman'.")
    font_size: int | None = Field(default=None, ge=6, le=72)


# ---------------------------------------------------------------------------
# Read operations
# ---------------------------------------------------------------------------


class ReadDocumentParams(BaseModel):
    path: str
    include_tables: bool = True
    include_headers_footers: bool = False


class ListParagraphsParams(BaseModel):
    path: str
    include_empty: bool = Field(default=False, description="Include empty paragraphs.")
    style_filter: str | None = Field(
        default=None, description="Only return paragraphs with this style."
    )


class ListTablesParams(BaseModel):
    path: str


class ExtractTextParams(BaseModel):
    path: str
    separator: str = Field(default="\n", description="Paragraph separator.")
    include_tables: bool = True


class CountWordsParams(BaseModel):
    path: str


# ---------------------------------------------------------------------------
# Paragraph operations
# ---------------------------------------------------------------------------


class WriteParagraphParams(BaseModel):
    path: str
    text: str
    style: str = Field(
        default="Normal",
        description="Word paragraph style name, e.g. 'Heading 1', 'Normal', 'Quote'.",
    )
    bold: bool = False
    italic: bool = False
    underline: bool = False
    font_name: str | None = None
    font_size: int | None = Field(default=None, ge=6, le=72)
    font_color: str | None = Field(default=None, description="Hex colour, e.g. 'FF0000'.")
    alignment: Literal["left", "center", "right", "justify"] = "left"
    space_before: float | None = Field(default=None, description="Space before paragraph in pt.")
    space_after: float | None = Field(default=None, description="Space after paragraph in pt.")
    line_spacing: float | None = Field(default=None, description="Line spacing multiplier.")
    insert_after_paragraph: int | None = Field(
        default=None,
        description="0-indexed paragraph index to insert after. Appends if None.",
    )


class FormatTextParams(BaseModel):
    """Apply character-level formatting to a run of text within a paragraph."""

    path: str
    paragraph_index: int = Field(description="0-indexed paragraph.")
    run_index: int = Field(default=0, ge=0, description="0-indexed run within paragraph.")
    bold: bool | None = None
    italic: bool | None = None
    underline: bool | None = None
    strikethrough: bool | None = None
    all_caps: bool | None = None
    font_name: str | None = None
    font_size: int | None = Field(default=None, ge=6, le=72)
    font_color: str | None = Field(default=None, description="Hex colour.")
    highlight_color: Literal["yellow", "green", "cyan", "magenta", "blue", "red", "darkBlue", "darkCyan", "darkGreen", "darkMagenta", "darkRed", "darkYellow", "darkGray", "lightGray", "white", "black", "none"] | None = None


class ApplyStyleParams(BaseModel):
    path: str
    paragraph_index: int = Field(description="0-indexed paragraph to style.")
    style: str = Field(description="Word style name to apply.")


class DeleteParagraphParams(BaseModel):
    path: str
    paragraph_index: int = Field(description="0-indexed paragraph to delete.")


class InsertPageBreakParams(BaseModel):
    path: str
    after_paragraph: int | None = Field(
        default=None, description="Insert break after this paragraph index. Appends if None."
    )


class InsertSectionBreakParams(BaseModel):
    path: str
    after_paragraph: int | None = None
    break_type: Literal["nextPage", "continuous", "evenPage", "oddPage"] = "nextPage"


class InsertListParams(BaseModel):
    path: str
    items: list[str] = Field(description="List of text items.")
    list_style: Literal["bullet", "number", "alpha", "roman"] = "bullet"
    indent_level: Annotated[int, Field(ge=0, le=8)] = 0
    insert_after_paragraph: int | None = None


# ---------------------------------------------------------------------------
# Table operations
# ---------------------------------------------------------------------------


class InsertTableParams(BaseModel):
    path: str
    rows: Annotated[int, Field(ge=1, le=500)]
    cols: Annotated[int, Field(ge=1, le=50)]
    data: list[list[str]] | None = Field(
        default=None, description="Row-major list of cell values to fill."
    )
    style: str = "Table Grid"
    has_header: bool = Field(default=True, description="Apply header formatting to first row.")
    insert_after_paragraph: int | None = None


class ModifyTableCellParams(BaseModel):
    path: str
    table_index: int = Field(ge=0, description="0-indexed table.")
    row: int = Field(ge=0, description="0-indexed row.")
    col: int = Field(ge=0, description="0-indexed column.")
    text: str | None = None
    bold: bool | None = None
    italic: bool | None = None
    font_size: int | None = Field(default=None, ge=6, le=72)
    font_color: str | None = None
    bg_color: str | None = Field(default=None, description="Background hex colour.")
    alignment: Literal["left", "center", "right", "justify"] | None = None
    vertical_alignment: Literal["top", "center", "bottom"] | None = None


class AddTableRowParams(BaseModel):
    path: str
    table_index: int = Field(ge=0)
    data: list[str] = Field(description="Cell values for the new row.")


# ---------------------------------------------------------------------------
# Rich content
# ---------------------------------------------------------------------------


class InsertImageParams(BaseModel):
    path: str
    image_path: str = Field(description="Path to the image file (PNG, JPEG, GIF, BMP).")
    width_cm: float | None = Field(default=None, description="Image width in centimetres.")
    height_cm: float | None = Field(default=None, description="Image height in centimetres.")
    insert_after_paragraph: int | None = None
    caption: str | None = None
    alignment: Literal["left", "center", "right"] = "left"


class InsertHyperlinkParams(BaseModel):
    path: str
    paragraph_index: int
    text: str
    url: str
    font_name: str | None = None
    font_size: int | None = Field(default=None, ge=6, le=72)


class AddBookmarkParams(BaseModel):
    path: str
    paragraph_index: int
    name: str = Field(description="Bookmark name (letters, digits, underscore).")


class AddCommentParams(BaseModel):
    path: str
    paragraph_index: int
    text: str
    author: str = Field(default="LLMOS Bridge")


class InsertTableOfContentsParams(BaseModel):
    path: str
    title: str = "Contents"
    max_depth: Annotated[int, Field(ge=1, le=9)] = 3
    insert_after_paragraph: int | None = None


# ---------------------------------------------------------------------------
# Header / footer
# ---------------------------------------------------------------------------


class AddHeaderFooterParams(BaseModel):
    path: str
    header_text: str | None = None
    footer_text: str | None = None
    section: Annotated[int, Field(ge=0)] = 0
    page_numbers: bool = Field(default=False, description="Add page numbers to footer.")
    alignment: Literal["left", "center", "right"] = "center"


# ---------------------------------------------------------------------------
# Search & replace
# ---------------------------------------------------------------------------


class FindReplaceParams(BaseModel):
    path: str
    find: str = Field(description="Text to search for.")
    replace: str = Field(description="Replacement text.")
    case_sensitive: bool = False
    whole_word: bool = False


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


class ExportToPdfParams(BaseModel):
    path: str
    output_path: str = Field(description="Destination PDF path.")
    use_libreoffice: bool = Field(
        default=True, description="Use LibreOffice for conversion."
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

PARAMS_MAP: dict[str, type[BaseModel]] = {
    # Lifecycle
    "open_document": OpenDocumentParams,
    "create_document": CreateDocumentParams,
    "save_document": SaveDocumentParams,
    "set_document_properties": SetDocumentPropertiesParams,
    "get_document_meta": GetDocumentMetaParams,
    "set_margins": SetMarginsParams,
    "set_default_font": SetDefaultFontParams,
    # Read
    "read_document": ReadDocumentParams,
    "list_paragraphs": ListParagraphsParams,
    "list_tables": ListTablesParams,
    "extract_text": ExtractTextParams,
    "count_words": CountWordsParams,
    # Paragraphs
    "write_paragraph": WriteParagraphParams,
    "format_text": FormatTextParams,
    "apply_style": ApplyStyleParams,
    "delete_paragraph": DeleteParagraphParams,
    "insert_page_break": InsertPageBreakParams,
    "insert_section_break": InsertSectionBreakParams,
    "insert_list": InsertListParams,
    # Tables
    "insert_table": InsertTableParams,
    "modify_table_cell": ModifyTableCellParams,
    "add_table_row": AddTableRowParams,
    # Rich content
    "insert_image": InsertImageParams,
    "insert_hyperlink": InsertHyperlinkParams,
    "add_bookmark": AddBookmarkParams,
    "add_comment": AddCommentParams,
    "insert_toc": InsertTableOfContentsParams,
    # Header/footer
    "add_header_footer": AddHeaderFooterParams,
    # Search
    "find_replace": FindReplaceParams,
    # Export
    "export_to_pdf": ExportToPdfParams,
}
