# Database Gateway Module -- Integration Guide

Cross-module workflows and integration patterns for the `db_gateway` module.

---

## Semantic CRUD with Excel Export

Use the gateway for clean CRUD operations, then export results to Excel.

```yaml
actions:
  - id: connect
    module: db_gateway
    action: connect
    params:
      driver: sqlite
      database: /data/hr.db

  - id: aggregate-salaries
    module: db_gateway
    action: aggregate
    params:
      entity: employees
      group_by: [department]
      aggregations: { salary: avg, id: count }
      having: { count_id: { "$gte": 3 } }
      order_by: ["-avg_salary"]
    depends_on: [connect]

  - id: export-to-excel
    module: excel
    action: write_cells
    params:
      path: /tmp/salary_report.xlsx
      sheet: Department Salaries
      start_cell: A1
      data: "{{result.aggregate-salaries.rows}}"
    depends_on: [aggregate-salaries]
```

---

## API Data Ingestion Pipeline

Fetch records from a REST API and bulk-insert into the gateway.

```yaml
actions:
  - id: connect-db
    module: db_gateway
    action: connect
    params:
      driver: postgresql
      host: localhost
      database: crm
      user: app
      password: "{{env.DB_PASSWORD}}"

  - id: fetch-contacts
    module: api_http
    action: http_get
    params:
      url: https://api.example.com/v2/contacts
      headers: { Authorization: "Bearer {{env.API_TOKEN}}" }

  - id: bulk-insert
    module: db_gateway
    action: create_many
    params:
      entity: contacts
      records: "{{result.fetch-contacts.body_json.data}}"
    depends_on: [connect-db, fetch-contacts]
```

---

## Schema-Driven Code Generation

Introspect a database schema and generate documentation or code.

```yaml
actions:
  - id: connect
    module: db_gateway
    action: connect
    params:
      url: "postgresql://admin:secret@localhost:5432/production"

  - id: introspect-schema
    module: db_gateway
    action: introspect
    params:
      refresh: true
    depends_on: [connect]

  - id: write-schema-doc
    module: filesystem
    action: write_file
    params:
      path: /tmp/schema.json
      content: "{{result.introspect-schema}}"
    depends_on: [introspect-schema]
```

---

## Search and Notification Workflow

Search for records and send an email notification with the results.

```yaml
actions:
  - id: connect
    module: db_gateway
    action: connect
    params:
      driver: sqlite
      database: /data/support.db

  - id: search-tickets
    module: db_gateway
    action: search
    params:
      entity: tickets
      query: "critical error"
      columns: [title, description]
      limit: 10
    depends_on: [connect]

  - id: notify-team
    module: api_http
    action: send_email
    params:
      to: ["ops@example.com"]
      subject: "Critical tickets found"
      body: "Found {{result.search-tickets.row_count}} critical tickets."
      smtp_host: smtp.example.com
      smtp_port: 587
      smtp_user: "{{env.SMTP_USER}}"
      smtp_password: "{{env.SMTP_PASS}}"
      use_tls: true
    depends_on: [search-tickets]
```

---

## Gateway vs Raw Database Module

When to use `db_gateway` vs `database`:

| Scenario | Recommended Module |
|----------|-------------------|
| LLM-driven CRUD without SQL knowledge | `db_gateway` |
| Complex multi-table JOINs | `database` |
| DDL operations (CREATE/ALTER/DROP) | `database` |
| Explicit transaction control | `database` |
| Aggregation with GROUP BY | `db_gateway` |
| Full-text search across columns | `db_gateway` |
| Schema introspection (tables, FK, indexes) | `db_gateway` |
| Database migrations | `database` |
| Non-SQL backends (MongoDB, Redis) | `db_gateway` (with custom adapter) |

---

## Custom Adapter Registration

Extend the gateway with a custom database backend:

```python
from llmos_bridge.modules.database_gateway.registry import AdapterRegistry
from llmos_bridge.modules.database_gateway.base_adapter import BaseDbAdapter

class MongoAdapter(BaseDbAdapter):
    def connect(self, connection_id, **kwargs):
        # ...
    def find(self, connection_id, entity, **kwargs):
        # ...
    # Implement all BaseDbAdapter methods

AdapterRegistry.register_adapter("mongodb", MongoAdapter)
```

Or register a SQL driver that works with SQLAlchemy:

```python
AdapterRegistry.register_sql_driver(
    name="cockroachdb",
    dialect="cockroachdb",
    default_port=26257,
    pip_package="sqlalchemy-cockroachdb",
)
```
