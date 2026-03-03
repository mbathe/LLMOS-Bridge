# PowerPoint Module -- Cross-Module Integration Guide

This document describes common multi-module workflows involving the PowerPoint module.

---

## 1. Data-Driven Presentation from Excel

Read data from an **excel** workbook and build a presentation with charts and tables.

```yaml
plan_id: excel-to-pptx
protocol_version: "2.0"
description: Build a data-driven presentation from Excel data.
execution_mode: sequential
actions:
  - id: read-data
    module: excel
    action: read_range
    params:
      path: /data/metrics.xlsx
      sheet: Dashboard
      range: "A1:D10"
      as_dict: true

  - id: create-pptx
    module: powerpoint
    action: create_presentation
    params:
      output_path: /reports/dashboard.pptx
    depends_on: [read-data]

  - id: title-slide
    module: powerpoint
    action: add_slide
    params:
      path: /reports/dashboard.pptx
      layout_index: 0
      title: "Monthly Dashboard"
    depends_on: [create-pptx]

  - id: chart-slide
    module: powerpoint
    action: add_slide
    params:
      path: /reports/dashboard.pptx
      layout_index: 5
    depends_on: [title-slide]

  - id: add-chart
    module: powerpoint
    action: add_chart
    params:
      path: /reports/dashboard.pptx
      slide_index: 1
      chart_type: col
      data:
        categories: ["Jan", "Feb", "Mar", "Apr"]
        series:
          - name: Revenue
            values: [10000, 12000, 15000, 14000]
          - name: Costs
            values: [8000, 9000, 10000, 9500]
      left: 2
      top: 3
      title: "Revenue vs Costs"
    depends_on: [chart-slide]

  - id: table-slide
    module: powerpoint
    action: add_slide
    params:
      path: /reports/dashboard.pptx
      layout_index: 5
      title: "Detailed Metrics"
    depends_on: [add-chart]

  - id: add-table
    module: powerpoint
    action: add_table
    params:
      path: /reports/dashboard.pptx
      slide_index: 2
      rows: 5
      cols: 4
      data: "{{result.read-data.data}}"
      left: 2
      top: 4
      has_header: true
    depends_on: [table-slide]

  - id: save-pptx
    module: powerpoint
    action: save_presentation
    params:
      path: /reports/dashboard.pptx
    depends_on: [add-table]
```

---

## 2. Database Query to Presentation

Combine the **database** module with PowerPoint to create data-driven slides.

```yaml
plan_id: db-to-pptx
protocol_version: "2.0"
description: Query sales data and build a presentation with a chart.
execution_mode: sequential
actions:
  - id: query-sales
    module: database
    action: execute_query
    params:
      connection: sales_db
      query: "SELECT region, SUM(amount) as total FROM sales GROUP BY region"

  - id: create-pptx
    module: powerpoint
    action: create_presentation
    params:
      output_path: /reports/sales.pptx
    depends_on: [query-sales]

  - id: title
    module: powerpoint
    action: add_slide
    params:
      path: /reports/sales.pptx
      layout_index: 0
      title: "Regional Sales Report"
    depends_on: [create-pptx]

  - id: chart-slide
    module: powerpoint
    action: add_slide
    params:
      path: /reports/sales.pptx
      layout_index: 5
      title: "Sales by Region"
    depends_on: [title]

  - id: pie-chart
    module: powerpoint
    action: add_chart
    params:
      path: /reports/sales.pptx
      slide_index: 1
      chart_type: pie
      data: "{{result.query-sales.chart_data}}"
      left: 3
      top: 3
      title: "Revenue Distribution"
      has_data_labels: true
    depends_on: [chart-slide]

  - id: save
    module: powerpoint
    action: save_presentation
    params:
      path: /reports/sales.pptx
    depends_on: [pie-chart]
```

---

## 3. Complete Office Suite Workflow

Generate a full report package: Excel workbook, Word document, and
PowerPoint presentation, sharing data across all three.

```yaml
plan_id: full-office-suite
protocol_version: "2.0"
description: Generate an Excel workbook, Word report, and PowerPoint deck from the same data.
execution_mode: sequential
actions:
  # -- Excel: Data workbook --
  - id: create-excel
    module: excel
    action: create_workbook
    params:
      path: /reports/data.xlsx
      sheet_name: Sales

  - id: write-data
    module: excel
    action: write_range
    params:
      path: /reports/data.xlsx
      sheet: Sales
      start_cell: A1
      data:
        - ["Product", "Q1", "Q2", "Q3", "Q4"]
        - ["Widget A", 150, 200, 180, 220]
        - ["Widget B", 80, 100, 120, 140]
        - ["Widget C", 200, 190, 210, 250]
    depends_on: [create-excel]

  - id: save-excel
    module: excel
    action: save_workbook
    params:
      path: /reports/data.xlsx
    depends_on: [write-data]

  - id: read-data
    module: excel
    action: read_range
    params:
      path: /reports/data.xlsx
      sheet: Sales
      range: auto
    depends_on: [save-excel]

  # -- Word: Written report --
  - id: create-doc
    module: word
    action: create_document
    params:
      output_path: /reports/report.docx
      title: "Annual Sales Report"
    depends_on: [read-data]

  - id: doc-heading
    module: word
    action: write_paragraph
    params:
      path: /reports/report.docx
      text: "Annual Sales Report"
      style: "Heading 1"
    depends_on: [create-doc]

  - id: doc-table
    module: word
    action: insert_table
    params:
      path: /reports/report.docx
      rows: 4
      cols: 5
      data: "{{result.read-data.data}}"
      has_header: true
    depends_on: [doc-heading]

  - id: save-doc
    module: word
    action: save_document
    params:
      path: /reports/report.docx
    depends_on: [doc-table]

  # -- PowerPoint: Presentation deck --
  - id: create-pptx
    module: powerpoint
    action: create_presentation
    params:
      output_path: /reports/presentation.pptx
    depends_on: [read-data]

  - id: pptx-title
    module: powerpoint
    action: add_slide
    params:
      path: /reports/presentation.pptx
      layout_index: 0
      title: "Annual Sales Review"
    depends_on: [create-pptx]

  - id: pptx-chart
    module: powerpoint
    action: add_slide
    params:
      path: /reports/presentation.pptx
      layout_index: 5
      title: "Sales Trends"
    depends_on: [pptx-title]

  - id: add-bar-chart
    module: powerpoint
    action: add_chart
    params:
      path: /reports/presentation.pptx
      slide_index: 1
      chart_type: col
      data:
        categories: ["Q1", "Q2", "Q3", "Q4"]
        series:
          - name: "Widget A"
            values: [150, 200, 180, 220]
          - name: "Widget B"
            values: [80, 100, 120, 140]
          - name: "Widget C"
            values: [200, 190, 210, 250]
      left: 2
      top: 3
      title: "Quarterly Sales by Product"
    depends_on: [pptx-chart]

  - id: save-pptx
    module: powerpoint
    action: save_presentation
    params:
      path: /reports/presentation.pptx
    depends_on: [add-bar-chart]
```

---

## 4. Slide Thumbnails for Web Dashboard

Export slides as images and use **filesystem** to organize them for a web application.

```yaml
plan_id: slide-thumbnails
protocol_version: "2.0"
description: Export all slides as images for a web dashboard.
execution_mode: sequential
actions:
  - id: open-pptx
    module: powerpoint
    action: open_presentation
    params:
      path: /presentations/quarterly.pptx

  - id: list-slides
    module: powerpoint
    action: list_slides
    params:
      path: /presentations/quarterly.pptx
    depends_on: [open-pptx]

  - id: create-output-dir
    module: filesystem
    action: create_directory
    params:
      path: /web/static/slides
      parents: true
    depends_on: [list-slides]

  - id: export-slide-0
    module: powerpoint
    action: export_slide_as_image
    params:
      path: /presentations/quarterly.pptx
      slide_index: 0
      output_path: /web/static/slides/slide_0.png
      width: 1920
    depends_on: [create-output-dir]

  - id: export-slide-1
    module: powerpoint
    action: export_slide_as_image
    params:
      path: /presentations/quarterly.pptx
      slide_index: 1
      output_path: /web/static/slides/slide_1.png
      width: 1920
    depends_on: [create-output-dir]

  - id: export-slide-2
    module: powerpoint
    action: export_slide_as_image
    params:
      path: /presentations/quarterly.pptx
      slide_index: 2
      output_path: /web/static/slides/slide_2.png
      width: 1920
    depends_on: [create-output-dir]
```

---

## 5. Themed Presentation with Custom Backgrounds

Apply a corporate theme and set custom slide backgrounds.

```yaml
plan_id: themed-presentation
protocol_version: "2.0"
description: Create a themed presentation with custom backgrounds and transitions.
execution_mode: sequential
actions:
  - id: create-pptx
    module: powerpoint
    action: create_presentation
    params:
      output_path: /reports/branded.pptx

  - id: apply-theme
    module: powerpoint
    action: apply_theme
    params:
      path: /reports/branded.pptx
      theme_path: /templates/corporate.pptx
    depends_on: [create-pptx]

  - id: title-slide
    module: powerpoint
    action: add_slide
    params:
      path: /reports/branded.pptx
      layout_index: 0
      title: "Quarterly Business Review"
    depends_on: [apply-theme]

  - id: title-bg
    module: powerpoint
    action: set_slide_background
    params:
      path: /reports/branded.pptx
      slide_index: 0
      color: "1F3864"
    depends_on: [title-slide]

  - id: content-slide
    module: powerpoint
    action: add_slide
    params:
      path: /reports/branded.pptx
      layout_index: 1
      title: "Highlights"
    depends_on: [title-bg]

  - id: transitions
    module: powerpoint
    action: add_transition
    params:
      path: /reports/branded.pptx
      transition: fade
      duration: 0.5
      advance_on_click: true
    depends_on: [content-slide]

  - id: notes
    module: powerpoint
    action: add_slide_notes
    params:
      path: /reports/branded.pptx
      slide_index: 0
      notes: "Welcome the audience and introduce the agenda."
    depends_on: [transitions]

  - id: save
    module: powerpoint
    action: save_presentation
    params:
      path: /reports/branded.pptx
    depends_on: [notes]

  - id: export-pdf
    module: powerpoint
    action: export_to_pdf
    params:
      path: /reports/branded.pptx
      output_path: /reports/branded.pdf
    depends_on: [save]
```
