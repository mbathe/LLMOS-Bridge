# Word Module -- Cross-Module Integration Guide

This document describes common multi-module workflows involving the Word module.

---

## 1. Template-Based Document Generation

Use **find_replace** to fill in template placeholders from external data.

```yaml
plan_id: word-template
protocol_version: "2.0"
description: Generate a contract from a Word template.
execution_mode: sequential
actions:
  - id: open-template
    module: word
    action: open_document
    params:
      path: /templates/contract.docx

  - id: fill-name
    module: word
    action: find_replace
    params:
      path: /templates/contract.docx
      find: "{{client_name}}"
      replace: "Acme Corp"
    depends_on: [open-template]

  - id: fill-date
    module: word
    action: find_replace
    params:
      path: /templates/contract.docx
      find: "{{date}}"
      replace: "2026-03-01"
    depends_on: [fill-name]

  - id: save-contract
    module: word
    action: save_document
    params:
      path: /templates/contract.docx
      output_path: /output/contract_acme.docx
    depends_on: [fill-date]

  - id: export-pdf
    module: word
    action: export_to_pdf
    params:
      path: /output/contract_acme.docx
      output_path: /output/contract_acme.pdf
    depends_on: [save-contract]
```

---

## 2. Excel Data into Word Report

Read data from an **excel** workbook and insert it as a table in a Word document.

```yaml
plan_id: excel-to-word-report
protocol_version: "2.0"
description: Build a Word report with data from an Excel workbook.
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
      author: "Finance Team"
    depends_on: [read-data]

  - id: add-title
    module: word
    action: write_paragraph
    params:
      path: /reports/quarterly.docx
      text: "Q1 2026 Financial Summary"
      style: "Heading 1"
    depends_on: [create-doc]

  - id: add-intro
    module: word
    action: write_paragraph
    params:
      path: /reports/quarterly.docx
      text: "The following table summarizes Q1 performance metrics."
    depends_on: [add-title]

  - id: insert-data-table
    module: word
    action: insert_table
    params:
      path: /reports/quarterly.docx
      rows: "{{result.read-data.row_count}}"
      cols: "{{result.read-data.col_count}}"
      data: "{{result.read-data.data}}"
      has_header: true
    depends_on: [add-intro]

  - id: save-report
    module: word
    action: save_document
    params:
      path: /reports/quarterly.docx
    depends_on: [insert-data-table]
```

---

## 3. Database Query to Word Document

Combine the **database** module with Word to create data-driven reports.

```yaml
plan_id: db-to-word
protocol_version: "2.0"
description: Query employee data and generate a formatted Word report.
execution_mode: sequential
actions:
  - id: query-employees
    module: database
    action: execute_query
    params:
      connection: hr_db
      query: "SELECT name, department, start_date FROM employees WHERE active = true"

  - id: create-doc
    module: word
    action: create_document
    params:
      output_path: /reports/employees.docx
      title: "Active Employee Directory"
    depends_on: [query-employees]

  - id: heading
    module: word
    action: write_paragraph
    params:
      path: /reports/employees.docx
      text: "Active Employee Directory"
      style: "Heading 1"
    depends_on: [create-doc]

  - id: toc
    module: word
    action: insert_toc
    params:
      path: /reports/employees.docx
      title: "Table of Contents"
    depends_on: [heading]

  - id: emp-table
    module: word
    action: insert_table
    params:
      path: /reports/employees.docx
      rows: 50
      cols: 3
      data: "{{result.query-employees.rows}}"
      has_header: true
    depends_on: [toc]

  - id: footer
    module: word
    action: add_header_footer
    params:
      path: /reports/employees.docx
      footer_text: "Confidential -- HR Department"
      page_numbers: true
    depends_on: [emp-table]

  - id: save
    module: word
    action: save_document
    params:
      path: /reports/employees.docx
    depends_on: [footer]
```

---

## 4. Multi-Section Document with Images

Build a structured document combining text, images from the **filesystem**,
and formatted tables.

```yaml
plan_id: multi-section-doc
protocol_version: "2.0"
description: Build a multi-section document with images and tables.
execution_mode: sequential
actions:
  - id: create-doc
    module: word
    action: create_document
    params:
      output_path: /reports/product.docx
      title: "Product Specification"
      default_font: "Calibri"
      default_font_size: 11

  - id: margins
    module: word
    action: set_margins
    params:
      path: /reports/product.docx
      top: 2.0
      bottom: 2.0
      left: 2.5
      right: 2.5
    depends_on: [create-doc]

  - id: title
    module: word
    action: write_paragraph
    params:
      path: /reports/product.docx
      text: "Product Specification Document"
      style: "Title"
      alignment: center
    depends_on: [margins]

  - id: overview-heading
    module: word
    action: write_paragraph
    params:
      path: /reports/product.docx
      text: "1. Overview"
      style: "Heading 1"
    depends_on: [title]

  - id: overview-text
    module: word
    action: write_paragraph
    params:
      path: /reports/product.docx
      text: "This document describes the technical specifications for the product."
    depends_on: [overview-heading]

  - id: product-image
    module: word
    action: insert_image
    params:
      path: /reports/product.docx
      image_path: /assets/product_photo.png
      width_cm: 12
      caption: "Figure 1: Product overview"
      alignment: center
    depends_on: [overview-text]

  - id: section-break
    module: word
    action: insert_section_break
    params:
      path: /reports/product.docx
      break_type: nextPage
    depends_on: [product-image]

  - id: specs-heading
    module: word
    action: write_paragraph
    params:
      path: /reports/product.docx
      text: "2. Technical Specifications"
      style: "Heading 1"
    depends_on: [section-break]

  - id: specs-table
    module: word
    action: insert_table
    params:
      path: /reports/product.docx
      rows: 5
      cols: 2
      data:
        - ["Attribute", "Value"]
        - ["Weight", "1.2 kg"]
        - ["Dimensions", "30 x 20 x 5 cm"]
        - ["Power", "USB-C, 65W"]
        - ["Warranty", "2 years"]
      has_header: true
    depends_on: [specs-heading]

  - id: save
    module: word
    action: save_document
    params:
      path: /reports/product.docx
    depends_on: [specs-table]
```

---

## 5. Word to PDF Pipeline with Filesystem Cleanup

Generate a document, export to PDF, then use **filesystem** to move the PDF
and clean up the intermediate .docx file.

```yaml
plan_id: word-to-pdf-pipeline
protocol_version: "2.0"
description: Generate a Word document, export to PDF, clean up.
execution_mode: sequential
actions:
  - id: create-doc
    module: word
    action: create_document
    params:
      output_path: /tmp/invoice.docx
      title: "Invoice"

  - id: content
    module: word
    action: write_paragraph
    params:
      path: /tmp/invoice.docx
      text: "Invoice #12345 -- Total: $1,500.00"
      bold: true
      font_size: 14
    depends_on: [create-doc]

  - id: save
    module: word
    action: save_document
    params:
      path: /tmp/invoice.docx
    depends_on: [content]

  - id: export-pdf
    module: word
    action: export_to_pdf
    params:
      path: /tmp/invoice.docx
      output_path: /tmp/invoice.pdf
    depends_on: [save]

  - id: move-pdf
    module: filesystem
    action: move_file
    params:
      path: /tmp/invoice.pdf
      destination: /invoices/invoice_12345.pdf
    depends_on: [export-pdf]

  - id: cleanup
    module: filesystem
    action: delete_file
    params:
      path: /tmp/invoice.docx
    depends_on: [move-pdf]
```
