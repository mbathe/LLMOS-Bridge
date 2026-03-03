# Excel Module -- Cross-Module Integration Guide

This document describes common multi-module workflows involving the Excel module.

---

## 1. Generate Report from Database Query

Combine the **database** module to query data and the **excel** module to build
a formatted workbook.

```yaml
plan_id: db-to-excel-report
protocol_version: "2.0"
description: Query sales data and build a formatted Excel report.
execution_mode: sequential
actions:
  - id: query-sales
    module: database
    action: execute_query
    params:
      connection: sales_db
      query: "SELECT product, region, amount FROM sales WHERE year = 2026"

  - id: create-wb
    module: excel
    action: create_workbook
    params:
      path: /reports/sales_2026.xlsx
      sheet_name: Sales
    depends_on: [query-sales]

  - id: write-headers
    module: excel
    action: write_range
    params:
      path: /reports/sales_2026.xlsx
      sheet: Sales
      start_cell: A1
      data:
        - ["Product", "Region", "Amount"]
    depends_on: [create-wb]

  - id: write-data
    module: excel
    action: write_range
    params:
      path: /reports/sales_2026.xlsx
      sheet: Sales
      start_cell: A2
      data: "{{result.query-sales.rows}}"
    depends_on: [write-headers]

  - id: format-headers
    module: excel
    action: format_range
    params:
      path: /reports/sales_2026.xlsx
      sheet: Sales
      range: "A1:C1"
      bold: true
      fill_color: "4472C4"
      font_color: "FFFFFF"
    depends_on: [write-data]

  - id: add-chart
    module: excel
    action: create_chart
    params:
      path: /reports/sales_2026.xlsx
      sheet: Sales
      chart_type: bar
      data_range: "A1:C100"
      title: "Sales by Product and Region"
    depends_on: [format-headers]

  - id: save-report
    module: excel
    action: save_workbook
    params:
      path: /reports/sales_2026.xlsx
    depends_on: [add-chart]
```

---

## 2. Excel to Word Report

Read data from an Excel workbook and embed it as a table in a Word document.

```yaml
plan_id: excel-to-word
protocol_version: "2.0"
description: Read Excel data and insert it into a Word report.
execution_mode: sequential
actions:
  - id: read-data
    module: excel
    action: read_range
    params:
      path: /data/quarterly.xlsx
      sheet: Q1
      range: "A1:E20"
      include_headers: true

  - id: create-doc
    module: word
    action: create_document
    params:
      output_path: /reports/quarterly.docx
      title: "Quarterly Report"
    depends_on: [read-data]

  - id: add-heading
    module: word
    action: write_paragraph
    params:
      path: /reports/quarterly.docx
      text: "Q1 Financial Summary"
      style: "Heading 1"
    depends_on: [create-doc]

  - id: insert-table
    module: word
    action: insert_table
    params:
      path: /reports/quarterly.docx
      rows: "{{result.read-data.row_count}}"
      cols: "{{result.read-data.col_count}}"
      data: "{{result.read-data.data}}"
      has_header: true
    depends_on: [add-heading]

  - id: save-doc
    module: word
    action: save_document
    params:
      path: /reports/quarterly.docx
    depends_on: [insert-table]
```

---

## 3. Excel Data to PowerPoint Dashboard

Extract Excel data and build a presentation with charts and tables.

```yaml
plan_id: excel-to-pptx
protocol_version: "2.0"
description: Build a dashboard presentation from Excel data.
execution_mode: sequential
actions:
  - id: read-metrics
    module: excel
    action: read_range
    params:
      path: /data/metrics.xlsx
      sheet: Dashboard
      range: auto
      as_dict: true

  - id: create-pptx
    module: powerpoint
    action: create_presentation
    params:
      output_path: /reports/dashboard.pptx
    depends_on: [read-metrics]

  - id: title-slide
    module: powerpoint
    action: add_slide
    params:
      path: /reports/dashboard.pptx
      layout_index: 0
      title: "Monthly Dashboard"
    depends_on: [create-pptx]

  - id: data-slide
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
        categories: ["Jan", "Feb", "Mar"]
        series:
          - name: Revenue
            values: [10000, 12000, 15000]
      left: 2
      top: 3
      title: "Revenue Trend"
    depends_on: [data-slide]

  - id: save-pptx
    module: powerpoint
    action: save_presentation
    params:
      path: /reports/dashboard.pptx
    depends_on: [add-chart]
```

---

## 4. CSV Import, Clean, and Export

Use the **filesystem** module to read a raw CSV, import it into Excel for
cleaning, then export the result.

```yaml
plan_id: csv-clean-export
protocol_version: "2.0"
description: Import CSV into Excel, remove duplicates, export cleaned data.
execution_mode: sequential
actions:
  - id: create-wb
    module: excel
    action: create_workbook
    params:
      path: /tmp/cleaning.xlsx

  - id: write-raw
    module: excel
    action: write_range
    params:
      path: /tmp/cleaning.xlsx
      sheet: Sheet1
      start_cell: A1
      data: "{{result.read-csv.data}}"
    depends_on: [create-wb]

  - id: dedup
    module: excel
    action: remove_duplicates
    params:
      path: /tmp/cleaning.xlsx
      sheet: Sheet1
      range: "A1:Z10000"
      keep: first
    depends_on: [write-raw]

  - id: export-clean
    module: excel
    action: export_to_csv
    params:
      path: /tmp/cleaning.xlsx
      sheet: Sheet1
      output_path: /data/cleaned.csv
    depends_on: [dedup]
```

---

## 5. Multi-Sheet Financial Report with Formulas

Build a multi-sheet workbook with data, formulas, and conditional formatting.

```yaml
plan_id: financial-report
protocol_version: "2.0"
description: Build a multi-sheet financial workbook with formulas.
execution_mode: sequential
actions:
  - id: create-wb
    module: excel
    action: create_workbook
    params:
      path: /reports/finance.xlsx
      sheet_name: Revenue

  - id: add-expenses
    module: excel
    action: create_sheet
    params:
      path: /reports/finance.xlsx
      name: Expenses
    depends_on: [create-wb]

  - id: add-summary
    module: excel
    action: create_sheet
    params:
      path: /reports/finance.xlsx
      name: Summary
    depends_on: [add-expenses]

  - id: sum-revenue
    module: excel
    action: apply_formula
    params:
      path: /reports/finance.xlsx
      sheet: Summary
      cell: B2
      formula: "=SUM(Revenue!B2:B100)"
    depends_on: [add-summary]

  - id: cond-format
    module: excel
    action: apply_conditional_format
    params:
      path: /reports/finance.xlsx
      sheet: Summary
      range: "C2:C20"
      format_type: color_scale
      min_color: "FF0000"
      max_color: "00FF00"
    depends_on: [sum-revenue]

  - id: save
    module: excel
    action: save_workbook
    params:
      path: /reports/finance.xlsx
    depends_on: [cond-format]
```
