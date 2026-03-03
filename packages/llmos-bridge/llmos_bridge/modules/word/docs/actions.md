# Word Module -- Action Reference

Complete reference for all 30 actions provided by the Word module.

---

## open_document

Open a .docx file and return its structure summary.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the .docx file |

**Returns:** `{"path": str, "paragraph_count": int, "table_count": int, "section_count": int}`

```yaml
- id: open
  module: word
  action: open_document
  params:
    path: /docs/report.docx
```

---

## create_document

Create a new blank Word document, set metadata and default font, and save it.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `output_path` | string | Yes | -- | Destination .docx file path |
| `title` | string | No | -- | Document title |
| `author` | string | No | -- | Document author |
| `default_font` | string | No | -- | Default font name |
| `default_font_size` | integer | No | -- | Default font size in pt |

**Returns:** `{"path": str, "created": bool}`

```yaml
- id: create
  module: word
  action: create_document
  params:
    output_path: /docs/new.docx
    title: "My Document"
    default_font: Calibri
    default_font_size: 11
```

---

## save_document

Save a cached document to disk.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Source .docx path (must be open/cached) |
| `output_path` | string | No | -- | Save-as path |

**Returns:** `{"path": str, "saved": bool}`

---

## set_document_properties

Update core document properties (title, author, subject, etc.).

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the .docx file |
| `title` | string | No | -- | Document title |
| `author` | string | No | -- | Author name |
| `subject` | string | No | -- | Document subject |
| `description` | string | No | -- | Document description |
| `keywords` | string | No | -- | Keywords string |
| `category` | string | No | -- | Document category |

---

## get_document_meta

Return all core_properties of the document.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the .docx file |

**Returns:** Object with all core document properties.

---

## set_margins

Set page margins for a section.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the .docx file |
| `top` | number | No | `2.54` | Top margin in cm |
| `bottom` | number | No | `2.54` | Bottom margin in cm |
| `left` | number | No | `3.17` | Left margin in cm |
| `right` | number | No | `3.17` | Right margin in cm |
| `section` | integer | No | `0` | 0-indexed section |

---

## set_default_font

Change the Normal style font name and/or size for the whole document.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the .docx file |
| `font_name` | string | Yes | -- | Font name, e.g. `Calibri` |
| `font_size` | integer | No | -- | Font size in pt |

---

## read_document

Extract full document content -- paragraphs, optionally tables and headers/footers.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the .docx file |
| `include_tables` | boolean | No | `true` | Include table data |
| `include_headers_footers` | boolean | No | `false` | Include headers/footers |

**Returns:** Object with paragraphs, tables, and header/footer content.

---

## list_paragraphs

List all paragraphs with their index, text, style and empty status.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the .docx file |
| `include_empty` | boolean | No | `false` | Include empty paragraphs |
| `style_filter` | string | No | -- | Filter by style name |

---

## list_tables

List all tables with index, row/col counts, and first cell preview.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the .docx file |

---

## extract_text

Extract all text from the document as a single string.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the .docx file |
| `separator` | string | No | `"\n"` | Paragraph separator |
| `include_tables` | boolean | No | `true` | Include table cell text |

**Returns:** `{"path": str, "text": str, "length": int}`

---

## count_words

Count total words across all paragraph and table cell text.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the .docx file |

**Returns:** `{"path": str, "word_count": int}`

---

## write_paragraph

Add a paragraph with text, style, and formatting. Optionally insert at a specific position.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the .docx file |
| `text` | string | Yes | -- | Paragraph text |
| `style` | string | No | `"Normal"` | Paragraph style name |
| `bold` | boolean | No | `false` | Bold text |
| `italic` | boolean | No | `false` | Italic text |
| `underline` | boolean | No | `false` | Underlined text |
| `font_name` | string | No | -- | Font name |
| `font_size` | integer | No | -- | Font size in pt |
| `font_color` | string | No | -- | Hex colour, e.g. `FF0000` |
| `alignment` | string | No | `"left"` | Alignment: left, center, right, justify |
| `insert_after_paragraph` | integer | No | -- | Insert after this paragraph index |

```yaml
- id: heading
  module: word
  action: write_paragraph
  params:
    path: /docs/report.docx
    text: "Executive Summary"
    style: "Heading 1"
    bold: true
```

---

## format_text

Apply character-level formatting to a specific run within a paragraph.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the .docx file |
| `paragraph_index` | integer | Yes | -- | 0-indexed paragraph |
| `run_index` | integer | No | `0` | 0-indexed run |
| `bold` | boolean | No | -- | Bold |
| `italic` | boolean | No | -- | Italic |
| `font_name` | string | No | -- | Font name |
| `font_size` | integer | No | -- | Font size in pt |
| `font_color` | string | No | -- | Hex colour |

---

## apply_style

Apply a named Word style to a paragraph.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the .docx file |
| `paragraph_index` | integer | Yes | -- | 0-indexed paragraph |
| `style` | string | Yes | -- | Style name to apply |

---

## delete_paragraph

Remove a paragraph from the document.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the .docx file |
| `paragraph_index` | integer | Yes | -- | 0-indexed paragraph to delete |

---

## insert_page_break

Insert a page break, optionally after a specific paragraph.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the .docx file |
| `after_paragraph` | integer | No | -- | Insert after this paragraph index |

---

## insert_section_break

Insert a section break of the given type.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the .docx file |
| `break_type` | string | No | `"nextPage"` | Type: nextPage, continuous, evenPage, oddPage |
| `after_paragraph` | integer | No | -- | Insert after this paragraph index |

---

## insert_list

Insert a bulleted or numbered list.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the .docx file |
| `items` | array | Yes | -- | List of text items |
| `list_style` | string | No | `"bullet"` | Style: bullet, number, alpha, roman |
| `indent_level` | integer | No | `0` | Indent level (0-8) |
| `insert_after_paragraph` | integer | No | -- | Insert after this paragraph index |

```yaml
- id: list
  module: word
  action: insert_list
  params:
    path: /docs/report.docx
    items:
      - "Complete Phase 1 deliverables"
      - "Begin Phase 2 planning"
      - "Schedule review meeting"
    list_style: number
```

---

## insert_table

Insert a table with given dimensions, optional data, style, and header row.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the .docx file |
| `rows` | integer | Yes | -- | Number of rows |
| `cols` | integer | Yes | -- | Number of columns |
| `data` | array | No | -- | Row-major list of cell values |
| `style` | string | No | `"Table Grid"` | Table style name |
| `has_header` | boolean | No | `true` | Mark first row as header |
| `insert_after_paragraph` | integer | No | -- | Insert after this paragraph index |

```yaml
- id: table
  module: word
  action: insert_table
  params:
    path: /docs/report.docx
    rows: 4
    cols: 3
    data:
      - ["Name", "Role", "Status"]
      - ["Alice", "Lead", "Active"]
      - ["Bob", "Dev", "Active"]
      - ["Charlie", "QA", "On Leave"]
    has_header: true
```

---

## modify_table_cell

Update text and formatting of a specific table cell.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the .docx file |
| `table_index` | integer | Yes | -- | 0-indexed table |
| `row` | integer | Yes | -- | 0-indexed row |
| `col` | integer | Yes | -- | 0-indexed column |
| `text` | string | No | -- | New cell text |
| `bold` | boolean | No | -- | Bold text |
| `font_size` | integer | No | -- | Font size in pt |
| `bg_color` | string | No | -- | Background hex colour |

---

## add_table_row

Append a new row to an existing table.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the .docx file |
| `table_index` | integer | Yes | -- | 0-indexed table |
| `data` | array | Yes | -- | Cell values for the new row |

---

## insert_image

Insert an image into the document with optional width, caption, and alignment.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the .docx file |
| `image_path` | string | Yes | -- | Path to the image file |
| `width_cm` | number | No | -- | Image width in cm |
| `height_cm` | number | No | -- | Image height in cm |
| `caption` | string | No | -- | Optional caption text |
| `alignment` | string | No | `"left"` | Alignment: left, center, right |
| `insert_after_paragraph` | integer | No | -- | Insert after this paragraph index |

---

## insert_hyperlink

Add a clickable hyperlink to an existing paragraph.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the .docx file |
| `paragraph_index` | integer | Yes | -- | 0-indexed target paragraph |
| `text` | string | Yes | -- | Display text for the link |
| `url` | string | Yes | -- | URL for the hyperlink |
| `font_name` | string | No | -- | Font name |
| `font_size` | integer | No | -- | Font size in pt |

---

## add_bookmark

Add a named bookmark to a paragraph.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the .docx file |
| `paragraph_index` | integer | Yes | -- | 0-indexed paragraph |
| `name` | string | Yes | -- | Bookmark name (letters, digits, underscore) |

---

## add_comment

Add a comment to a paragraph. Implemented as a visible note paragraph since python-docx does not support native comment XML.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the .docx file |
| `paragraph_index` | integer | Yes | -- | 0-indexed paragraph |
| `text` | string | Yes | -- | Comment text |
| `author` | string | No | `"LLMOS Bridge"` | Comment author |

---

## insert_toc

Insert a Table of Contents field code. Requires document to be opened in Word and TOC refreshed (F9).

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the .docx file |
| `title` | string | No | `"Contents"` | TOC heading text |
| `max_depth` | integer | No | `3` | Maximum heading depth (1-9) |
| `insert_after_paragraph` | integer | No | -- | Insert after this paragraph index |

---

## add_header_footer

Set header and/or footer text for a section, with optional page numbers.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the .docx file |
| `header_text` | string | No | -- | Header text |
| `footer_text` | string | No | -- | Footer text |
| `section` | integer | No | `0` | 0-indexed section |
| `page_numbers` | boolean | No | `false` | Add page numbers to footer |
| `alignment` | string | No | `"center"` | Text alignment: left, center, right |

---

## find_replace

Find and replace all occurrences of a text string throughout the document.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the .docx file |
| `find` | string | Yes | -- | Text to search for |
| `replace` | string | Yes | -- | Replacement text |
| `case_sensitive` | boolean | No | `false` | Case-sensitive search |
| `whole_word` | boolean | No | `false` | Match whole words only |

**Returns:** `{"path": str, "find": str, "replace": str, "replacements_made": int}`

```yaml
- id: replace
  module: word
  action: find_replace
  params:
    path: /docs/template.docx
    find: "{{company_name}}"
    replace: "Acme Corp"
```

---

## export_to_pdf

Export the document to PDF using LibreOffice headless.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the .docx file |
| `output_path` | string | Yes | -- | Destination PDF path |
| `use_libreoffice` | boolean | No | `true` | Use LibreOffice for conversion |

**Returns:** `{"path": str, "pdf_path": str, "exported": bool}`

**Security:** Permission `local_worker` required.
