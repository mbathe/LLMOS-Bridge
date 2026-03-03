# PowerPoint Module -- Action Reference

Complete reference for all 25 actions provided by the PowerPoint module.

---

## create_presentation

Create a new blank PowerPoint presentation.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `output_path` | string | Yes | -- | Path where the .pptx will be saved |
| `slide_width` | number | No | -- | Slide width in cm |
| `slide_height` | number | No | -- | Slide height in cm |
| `theme_path` | string | No | -- | Source .pptx to copy theme from |

**Returns:** `{"path": str, "slide_width_cm": float, "slide_height_cm": float}`

```yaml
- id: create
  module: powerpoint
  action: create_presentation
  params:
    output_path: /reports/deck.pptx
```

---

## open_presentation

Open and cache an existing .pptx file.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to an existing .pptx file |

**Returns:** `{"path": str, "slide_count": int, "layout_names": list[str]}`

---

## save_presentation

Save the presentation to disk.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path of the cached presentation |
| `output_path` | string | No | -- | Save-as path. Overwrites original if omitted |

**Returns:** `{"saved_to": str}`

---

## get_presentation_info

Return metadata about a presentation.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the .pptx file |

**Returns:** `{"slide_count": int, "slide_width_cm": float, "slide_height_cm": float, "slide_layouts": list[str]}`

---

## add_slide

Add a new slide to the presentation.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the .pptx file |
| `layout_index` | integer | No | `1` | Slide layout index |
| `title` | string | No | -- | Optional slide title |
| `position` | integer | No | -- | Insert position (0-indexed). Appended if omitted |

**Returns:** `{"slide_index": int, "slide_count": int}`

```yaml
- id: new-slide
  module: powerpoint
  action: add_slide
  params:
    path: /reports/deck.pptx
    layout_index: 1
    title: "Agenda"
```

---

## delete_slide

Delete a slide from the presentation.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the .pptx file |
| `slide_index` | integer | Yes | -- | 0-indexed slide to delete |

**Returns:** Object confirming deletion.

---

## duplicate_slide

Duplicate a slide and insert the copy at a specified position.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the .pptx file |
| `slide_index` | integer | Yes | -- | 0-indexed slide to duplicate |
| `insert_after` | integer | No | -- | Insert copy after this index |

**Returns:** Object confirming duplication.

---

## reorder_slide

Move a slide from one position to another.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the .pptx file |
| `from_index` | integer | Yes | -- | Source slide index |
| `to_index` | integer | Yes | -- | Destination slide index |

**Returns:** Object confirming reorder.

---

## list_slides

List all slides with title, shape count, and notes preview.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the .pptx file |

**Returns:** `{"slides": [{"index": int, "title": str, "shape_count": int, "notes_preview": str}]}`

---

## read_slide

Read all content from a slide.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the .pptx file |
| `slide_index` | integer | Yes | -- | 0-indexed slide to read |
| `include_notes` | boolean | No | `true` | Include speaker notes |
| `include_shapes` | boolean | No | `true` | Include shape details |

**Returns:** Object with slide content including shapes, text, and notes.

---

## set_slide_layout

Change the layout of a slide.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the .pptx file |
| `slide_index` | integer | Yes | -- | 0-indexed slide |
| `layout_index` | integer | Yes | -- | Target layout index |

**Returns:** Object confirming layout change.

---

## set_slide_title

Set the title placeholder text of a slide.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the .pptx file |
| `slide_index` | integer | Yes | -- | 0-indexed slide |
| `title` | string | Yes | -- | Title text |
| `bold` | boolean | No | `false` | Bold title text |
| `font_size` | integer | No | -- | Font size in points |
| `font_color` | string | No | -- | Hex colour, e.g. `FF0000` |

**Returns:** Object confirming title update.

---

## add_text_box

Add a text box to a slide with full formatting options.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the .pptx file |
| `slide_index` | integer | Yes | -- | 0-indexed slide |
| `text` | string | Yes | -- | Text content |
| `left` | number | Yes | -- | Left position in cm |
| `top` | number | Yes | -- | Top position in cm |
| `width` | number | Yes | -- | Width in cm |
| `height` | number | Yes | -- | Height in cm |
| `bold` | boolean | No | `false` | Bold text |
| `italic` | boolean | No | `false` | Italic text |
| `font_size` | integer | No | -- | Font size in pt |
| `font_color` | string | No | -- | Hex colour |
| `alignment` | string | No | `"left"` | Text alignment: left, center, right, justify |

**Returns:** Object confirming text box creation.

```yaml
- id: textbox
  module: powerpoint
  action: add_text_box
  params:
    path: /reports/deck.pptx
    slide_index: 0
    text: "Welcome to the presentation"
    left: 5
    top: 10
    width: 20
    height: 3
    font_size: 24
    bold: true
    alignment: center
```

---

## add_slide_notes

Set or append speaker notes to a slide.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the .pptx file |
| `slide_index` | integer | Yes | -- | 0-indexed slide |
| `notes` | string | Yes | -- | Notes text |
| `append` | boolean | No | `false` | Append to existing notes |

**Returns:** Object confirming notes update.

---

## add_shape

Add an auto shape to a slide.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the .pptx file |
| `slide_index` | integer | Yes | -- | 0-indexed slide |
| `shape_type` | string | No | `"rectangle"` | Shape type (see below) |
| `left` | number | Yes | -- | Left position in cm |
| `top` | number | Yes | -- | Top position in cm |
| `width` | number | Yes | -- | Width in cm |
| `height` | number | Yes | -- | Height in cm |
| `fill_color` | string | No | -- | Fill hex colour |
| `line_color` | string | No | -- | Border hex colour |
| `text` | string | No | -- | Text inside shape |

**Supported shape types:** `rectangle`, `rounded_rectangle`, `ellipse`, `triangle`, `right_arrow`, `left_arrow`, `up_arrow`, `down_arrow`, `pentagon`, `hexagon`, `star4`, `star5`, `star8`, `callout`, `cloud`, `lightning`, `heart`, `checkmark`, `line`, `connector`.

**Returns:** Object confirming shape creation.

---

## format_shape

Modify fill, border, rotation, and shadow of an existing shape.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the .pptx file |
| `slide_index` | integer | Yes | -- | 0-indexed slide |
| `shape_index` | integer | Yes | -- | 0-indexed shape |
| `fill_color` | string | No | -- | Fill hex colour |
| `line_color` | string | No | -- | Border hex colour |
| `rotation` | number | No | -- | Rotation degrees |
| `shadow` | boolean | No | -- | Apply drop shadow |

**Returns:** Object confirming shape formatting.

---

## add_image

Insert an image onto a slide.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the .pptx file |
| `slide_index` | integer | Yes | -- | 0-indexed slide |
| `image_path` | string | Yes | -- | Path to the image file |
| `left` | number | Yes | -- | Left position in cm |
| `top` | number | Yes | -- | Top position in cm |
| `width` | number | No | -- | Width in cm. Auto-scaled if omitted |
| `height` | number | No | -- | Height in cm. Auto-scaled if omitted |

**Returns:** Object confirming image insertion.

---

## add_chart

Add a chart to a slide.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the .pptx file |
| `slide_index` | integer | Yes | -- | 0-indexed slide |
| `chart_type` | string | No | `"col"` | Type: bar, col, line, pie, doughnut, scatter, area, bubble, radar |
| `data` | object | Yes | -- | Chart data: `{"categories": [...], "series": [{"name": str, "values": [...]}]}` |
| `left` | number | Yes | -- | Left position in cm |
| `top` | number | Yes | -- | Top position in cm |
| `width` | number | No | `14` | Width in cm |
| `height` | number | No | `10` | Height in cm |
| `title` | string | No | -- | Chart title |
| `has_legend` | boolean | No | `true` | Show legend |
| `has_data_labels` | boolean | No | `false` | Show data labels |

**Returns:** Object confirming chart creation.

```yaml
- id: chart
  module: powerpoint
  action: add_chart
  params:
    path: /reports/deck.pptx
    slide_index: 1
    chart_type: pie
    data:
      categories: ["Product A", "Product B", "Product C"]
      series:
        - name: Sales
          values: [45, 30, 25]
    left: 3
    top: 3
    title: "Sales Distribution"
    has_data_labels: true
```

---

## add_table

Add a table to a slide.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the .pptx file |
| `slide_index` | integer | Yes | -- | 0-indexed slide |
| `rows` | integer | Yes | -- | Number of rows |
| `cols` | integer | Yes | -- | Number of columns |
| `data` | array | No | -- | Row-major list of cell values |
| `left` | number | Yes | -- | Left position in cm |
| `top` | number | Yes | -- | Top position in cm |
| `width` | number | No | `20` | Table width in cm |
| `height` | number | No | `10` | Table height in cm |
| `has_header` | boolean | No | `true` | Style first row as header |

**Returns:** Object confirming table creation.

```yaml
- id: table
  module: powerpoint
  action: add_table
  params:
    path: /reports/deck.pptx
    slide_index: 2
    rows: 4
    cols: 3
    data:
      - ["Metric", "Target", "Actual"]
      - ["Revenue", "$1M", "$1.2M"]
      - ["Users", "10K", "12K"]
      - ["NPS", "50", "62"]
    left: 3
    top: 4
    has_header: true
```

---

## format_table_cell

Format a specific cell in a table shape.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the .pptx file |
| `slide_index` | integer | Yes | -- | 0-indexed slide |
| `shape_index` | integer | Yes | -- | 0-indexed table shape |
| `row` | integer | Yes | -- | Row index (0-based) |
| `col` | integer | Yes | -- | Column index (0-based) |
| `text` | string | No | -- | Cell text |
| `bg_color` | string | No | -- | Background hex colour |
| `font_color` | string | No | -- | Font hex colour |
| `bold` | boolean | No | -- | Bold text |

**Returns:** Object confirming cell formatting.

---

## set_slide_background

Set the background of one or all slides (solid color, image, or gradient).

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the .pptx file |
| `slide_index` | integer | No | -- | 0-indexed slide. All slides if omitted |
| `color` | string | No | -- | Solid background hex colour |
| `image_path` | string | No | -- | Path to background image |
| `gradient` | object | No | -- | Gradient spec: `{type, stops, angle}` |

**Returns:** Object confirming background change.

---

## apply_theme

Copy and apply the theme from another .pptx file.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the target .pptx |
| `theme_path` | string | Yes | -- | Path to the source .pptx to copy theme from |

**Returns:** Object confirming theme application.

---

## add_transition

Add a slide transition animation.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the .pptx file |
| `slide_index` | integer | No | -- | 0-indexed slide. All slides if omitted |
| `transition` | string | No | `"fade"` | Type: none, fade, push, wipe, split, reveal, random |
| `duration` | number | No | `1.0` | Transition duration in seconds |
| `advance_on_click` | boolean | No | `true` | Advance on click |
| `advance_after` | number | No | -- | Auto-advance after N seconds |

**Returns:** Object confirming transition setup.

---

## export_to_pdf

Export the presentation to a PDF file using LibreOffice.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the .pptx file |
| `output_path` | string | Yes | -- | Destination PDF path |

**Returns:** `{"pdf_path": str}`

---

## export_slide_as_image

Export a single slide as a PNG image using LibreOffice.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the .pptx file |
| `slide_index` | integer | Yes | -- | 0-indexed slide to export |
| `output_path` | string | Yes | -- | Destination image path |
| `width` | integer | No | `1920` | Output width in pixels |

**Returns:** `{"image_path": str, "slide_index": int}`
