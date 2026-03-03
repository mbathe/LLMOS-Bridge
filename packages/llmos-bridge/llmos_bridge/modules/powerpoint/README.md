# PowerPoint Module

Full-featured PowerPoint presentation automation using python-pptx.

## Overview

The PowerPoint module provides comprehensive `.pptx` presentation automation
for IML plans. It covers the full lifecycle of presentation work: creating and
opening presentations, slide management, text boxes, shapes, images, charts,
tables, backgrounds, themes, transitions, and export to PDF and PNG. All
blocking I/O is offloaded to a thread via `asyncio.to_thread` so the async
event loop is never blocked. Presentations are cached in memory by source path
to avoid redundant disk reads within a session.

## Actions

| Action | Description | Risk | Permission |
|--------|-------------|------|------------|
| `create_presentation` | Create a new blank PowerPoint presentation | Medium | `filesystem_write` |
| `open_presentation` | Open and cache an existing .pptx file | Medium | `filesystem_read` |
| `save_presentation` | Save the presentation to disk | Medium | `filesystem_write` |
| `get_presentation_info` | Return metadata about a presentation | Low | `filesystem_read` |
| `add_slide` | Add a new slide to the presentation | Medium | `filesystem_write` |
| `delete_slide` | Delete a slide from the presentation | Medium | `filesystem_write` |
| `duplicate_slide` | Duplicate a slide and insert the copy | Medium | `filesystem_write` |
| `reorder_slide` | Move a slide from one position to another | Medium | `filesystem_write` |
| `list_slides` | List all slides with title, shape count, and notes | Low | `filesystem_read` |
| `read_slide` | Read all content from a slide | Low | `filesystem_read` |
| `set_slide_layout` | Change the layout of a slide | Medium | `filesystem_write` |
| `set_slide_title` | Set the title placeholder text of a slide | Medium | `filesystem_write` |
| `add_text_box` | Add a text box with full formatting options | Medium | `filesystem_write` |
| `add_slide_notes` | Set or append speaker notes to a slide | Medium | `filesystem_write` |
| `add_shape` | Add an auto shape to a slide | Medium | `filesystem_write` |
| `format_shape` | Modify fill, border, rotation, and shadow of a shape | Medium | `filesystem_write` |
| `add_image` | Insert an image onto a slide | Medium | `filesystem_read`, `filesystem_write` |
| `add_chart` | Add a chart to a slide | Medium | `filesystem_write` |
| `add_table` | Add a table to a slide | Medium | `filesystem_write` |
| `format_table_cell` | Format a specific cell in a table shape | Medium | `filesystem_write` |
| `set_slide_background` | Set slide background (solid color, image, gradient) | Medium | `filesystem_write` |
| `apply_theme` | Copy and apply theme from another .pptx file | Medium | `filesystem_read`, `filesystem_write` |
| `add_transition` | Add a slide transition animation | Medium | `filesystem_write` |
| `export_to_pdf` | Export the presentation to PDF using LibreOffice | Medium | `filesystem_write` |
| `export_slide_as_image` | Export a single slide as a PNG image | Medium | `filesystem_write` |

## Quick Start

```yaml
actions:
  - id: create-pptx
    module: powerpoint
    action: create_presentation
    params:
      output_path: /reports/demo.pptx

  - id: title-slide
    module: powerpoint
    action: add_slide
    params:
      path: /reports/demo.pptx
      layout_index: 0
      title: "Project Overview"
    depends_on: [create-pptx]

  - id: content-slide
    module: powerpoint
    action: add_slide
    params:
      path: /reports/demo.pptx
      layout_index: 1
      title: "Key Metrics"
    depends_on: [title-slide]

  - id: add-chart
    module: powerpoint
    action: add_chart
    params:
      path: /reports/demo.pptx
      slide_index: 1
      chart_type: col
      data:
        categories: ["Q1", "Q2", "Q3", "Q4"]
        series:
          - name: Revenue
            values: [100, 120, 140, 180]
      left: 2
      top: 3
      title: "Quarterly Revenue"
    depends_on: [content-slide]

  - id: save-pptx
    module: powerpoint
    action: save_presentation
    params:
      path: /reports/demo.pptx
    depends_on: [add-chart]
```

## Requirements

| Dependency | Version | Notes |
|------------|---------|-------|
| `python-pptx` | >= 0.6 | Core presentation engine |
| `libreoffice` | any | Required for `export_to_pdf` and `export_slide_as_image` actions |

Install with:
```bash
pip install python-pptx
```

## Configuration

Uses default LLMOS Bridge configuration. Sandbox paths are enforced by the
upstream PermissionGuard via `SecurityConfig.sandbox_paths`. Presentations
are cached in memory per session; keyed by source path.

## Platform Support

| Platform | Status |
|----------|--------|
| Linux | Supported |
| macOS | Supported |
| Windows | Supported |

## Related Modules

- **excel** -- Spreadsheet automation; combine with PowerPoint to build
  data-driven charts and tables from Excel data.
- **word** -- Document automation; share content between Word documents
  and PowerPoint slides.
- **filesystem** -- Low-level file operations for managing presentation files.
- **database** -- Query databases and feed results into presentation charts
  and tables.
