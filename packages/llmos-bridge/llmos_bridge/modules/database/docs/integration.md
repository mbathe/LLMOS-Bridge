# Database Module -- Integration Guide

Cross-module workflows and integration patterns for the `database` module.

---

## Database to Excel Report

Query data from a SQLite database and write it to an Excel spreadsheet.

```yaml
actions:
  - id: connect
    module: database
    action: connect
    params:
      driver: sqlite
      database: /data/analytics.db

  - id: fetch-sales
    module: database
    action: fetch_results
    params:
      sql: "SELECT region, product, SUM(revenue) as total FROM sales GROUP BY region, product"
      max_rows: 5000
    depends_on: [connect]

  - id: write-report
    module: excel
    action: write_cells
    params:
      path: /tmp/sales_report.xlsx
      sheet: Sales Summary
      start_cell: A1
      data: "{{result.fetch-sales.rows}}"
    depends_on: [fetch-sales]
```

---

## API Fetch to Database Sync

Fetch records from an external REST API and insert them into a local database.

```yaml
actions:
  - id: connect-db
    module: database
    action: connect
    params:
      driver: sqlite
      database: /data/contacts.db

  - id: create-table
    module: database
    action: create_table
    params:
      table: contacts
      columns:
        - { name: id, type: "INTEGER PRIMARY KEY" }
        - { name: name, type: "TEXT NOT NULL" }
        - { name: email, type: "TEXT" }
        - { name: company, type: "TEXT" }
    depends_on: [connect-db]

  - id: fetch-api
    module: api_http
    action: http_get
    params:
      url: https://api.example.com/contacts
      headers: { Authorization: "Bearer {{env.API_TOKEN}}" }

  - id: insert-contacts
    module: database
    action: execute_query
    params:
      sql: "INSERT OR REPLACE INTO contacts (id, name, email, company) VALUES (?, ?, ?, ?)"
      params: "{{result.fetch-api.body_json}}"
    depends_on: [create-table, fetch-api]
```

---

## Transactional Batch Insert

Use explicit transactions for atomic multi-row inserts with rollback on failure.

```yaml
actions:
  - id: connect
    module: database
    action: connect
    params:
      driver: sqlite
      database: /data/inventory.db

  - id: begin-tx
    module: database
    action: begin_transaction
    params:
      isolation_level: immediate
    depends_on: [connect]

  - id: insert-item-1
    module: database
    action: insert_record
    params:
      table: inventory
      record: { sku: "WIDGET-001", quantity: 100, price: 9.99 }
    depends_on: [begin-tx]
    on_error: abort

  - id: insert-item-2
    module: database
    action: insert_record
    params:
      table: inventory
      record: { sku: "WIDGET-002", quantity: 50, price: 14.99 }
    depends_on: [insert-item-1]
    on_error: abort

  - id: commit-tx
    module: database
    action: commit_transaction
    depends_on: [insert-item-2]

  - id: rollback-tx
    module: database
    action: rollback_transaction
    rollback: [insert-item-1, insert-item-2]
```

---

## Database Schema Introspection to Documentation

List tables and their schemas, then write the output to a markdown file.

```yaml
actions:
  - id: connect
    module: database
    action: connect
    params:
      driver: postgresql
      host: localhost
      database: myapp
      user: admin
      password: "{{env.DB_PASSWORD}}"

  - id: list-tables
    module: database
    action: list_tables
    depends_on: [connect]

  - id: write-docs
    module: filesystem
    action: write_file
    params:
      path: /tmp/schema_docs.md
      content: "# Database Schema\n\nTables: {{result.list-tables.tables}}"
    depends_on: [list-tables]
```

---

## Database with db_gateway Comparison

The `database` module provides raw SQL access, while `db_gateway` offers a semantic,
SQL-free interface. Choose based on your use case:

| Feature | `database` | `db_gateway` |
|---------|-----------|--------------|
| Raw SQL | Yes | No |
| MongoDB-like filters | No | Yes |
| Transaction control | Yes (explicit) | No (auto-commit) |
| Schema introspection | Basic (per-table) | Full (all tables, FK, indexes) |
| Connection pooling | Single connection per ID | SQLAlchemy pool per driver |
| Aggregations | Via raw SQL | Built-in aggregate action |
| Best for | Complex queries, DDL, migrations | Simple CRUD, LLM-safe operations |
