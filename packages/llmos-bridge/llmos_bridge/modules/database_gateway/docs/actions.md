# Database Gateway Module -- Action Reference

## connect

Open a database connection.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `url` | string | No | -- | Full connection URL (e.g. `sqlite:///mydb.db`, `postgresql://user:pass@host:5432/dbname`). If provided, driver/host/port/database/user/password are ignored. |
| `driver` | string | No | `"sqlite"` | Database driver. Built-in: `sqlite`, `postgresql`, `mysql`. Extensible via `register_sql_driver()`. |
| `database` | string | No | `""` | Database name or file path (for SQLite). |
| `host` | string | No | `"localhost"` | Database host. |
| `port` | integer | No | -- | Database port. |
| `user` | string | No | -- | Database user. |
| `password` | string | No | -- | Database password. |
| `connection_id` | string | No | `"default"` | Logical connection name. |
| `pool_size` | integer | No | `5` | Connection pool size (1--20). |

**Returns:** `{connection_id, driver, database, tables, table_count, status}`

**Example:**
```json
{
  "action": "connect",
  "module": "db_gateway",
  "params": {
    "driver": "sqlite",
    "database": "/tmp/myapp.db"
  }
}
```

---

## disconnect

Close a database connection.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `connection_id` | string | No | `"default"` | Connection to close. |

**Returns:** `{connection_id, status}`

---

## introspect

Get full schema: tables, columns, types, foreign keys, indexes.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `connection_id` | string | No | `"default"` | Connection to inspect. |
| `schema_name` | string | No | -- | Schema name (PostgreSQL only). |
| `refresh` | boolean | No | `false` | Force re-introspection, bypassing the cache. |

**Returns:** `{connection_id, cached, tables: [{name, columns, primary_key, foreign_keys, indexes}], table_count, schema}`

---

## find

Find records matching a filter, with projection, ordering, and pagination.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `entity` | string | Yes | -- | Table/entity name. |
| `filter` | object | No | `{}` | MongoDB-like filter (e.g. `{"age": {"$gte": 18}}`). |
| `select` | array | No | -- | Columns to return (null = all). |
| `order_by` | array | No | -- | Sort order. Prefix `-` for descending. |
| `limit` | integer | No | `100` | Max rows to return (1--10,000). |
| `offset` | integer | No | `0` | Skip first N rows. |
| `connection_id` | string | No | `"default"` | Connection to use. |

**Returns:** `{entity, rows, row_count, truncated, elapsed_ms, connection_id}`

**Example:**
```json
{
  "action": "find",
  "module": "db_gateway",
  "params": {
    "entity": "users",
    "filter": {"age": {"$gte": 18}, "status": "active"},
    "order_by": ["name"],
    "limit": 50
  }
}
```

---

## find_one

Find a single record matching a filter.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `entity` | string | Yes | -- | Table/entity name. |
| `filter` | object | No | `{}` | MongoDB-like filter. |
| `select` | array | No | -- | Columns to return. |
| `connection_id` | string | No | `"default"` | Connection to use. |

**Returns:** `{entity, found, record, connection_id}`

---

## create

Create a new record in an entity.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `entity` | string | Yes | -- | Table/entity name. |
| `data` | object | Yes | -- | Column-value mapping for the new record. |
| `connection_id` | string | No | `"default"` | Connection to use. |

**Returns:** `{entity, created, inserted_id, connection_id}`

**Example:**
```json
{
  "action": "create",
  "module": "db_gateway",
  "params": {
    "entity": "users",
    "data": {"name": "Alice", "email": "alice@example.com", "age": 30}
  }
}
```

---

## create_many

Create multiple records in a single batch.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `entity` | string | Yes | -- | Table/entity name. |
| `records` | array | Yes | -- | List of column-value mappings (min 1). |
| `connection_id` | string | No | `"default"` | Connection to use. |

**Returns:** `{entity, created, inserted_count, connection_id}`

---

## update

Update records matching a filter.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `entity` | string | Yes | -- | Table/entity name. |
| `filter` | object | Yes | -- | MongoDB-like filter to select records. |
| `values` | object | Yes | -- | Column-value mapping with new values. |
| `connection_id` | string | No | `"default"` | Connection to use. |

**Returns:** `{entity, rows_affected, connection_id}`

---

## delete

Delete records matching a filter. Requires `confirm=true`.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `entity` | string | Yes | -- | Table/entity name. |
| `filter` | object | Yes | -- | MongoDB-like filter to select records. |
| `confirm` | boolean | Yes | `false` | Safety flag -- must be `true` to execute. |
| `connection_id` | string | No | `"default"` | Connection to use. |

**Returns:** `{entity, deleted, rows_deleted, connection_id}`

---

## count

Count records matching a filter.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `entity` | string | Yes | -- | Table/entity name. |
| `filter` | object | No | `{}` | MongoDB-like filter. Empty = count all. |
| `connection_id` | string | No | `"default"` | Connection to use. |

**Returns:** `{entity, count, connection_id}`

---

## aggregate

Aggregate records with GROUP BY and aggregate functions.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `entity` | string | Yes | -- | Table/entity name. |
| `group_by` | array | Yes | -- | Columns to group by. |
| `aggregations` | object | Yes | -- | Column-to-function mapping (e.g. `{"salary": "avg", "id": "count"}`). Supported: `sum`, `avg`, `min`, `max`, `count`. |
| `filter` | object | No | `{}` | Pre-aggregation WHERE filter. |
| `having` | object | No | `{}` | Post-aggregation HAVING filter on aliases (e.g. `{"count_id": {"$gte": 5}}`). |
| `order_by` | array | No | -- | Sort order. |
| `limit` | integer | No | `1000` | Max rows (1--10,000). |
| `connection_id` | string | No | `"default"` | Connection to use. |

**Returns:** `{entity, rows, row_count, elapsed_ms, connection_id}`

Aggregated column aliases follow the pattern `{func}_{col}` (e.g. `avg_salary`, `count_id`).

**Example:**
```json
{
  "action": "aggregate",
  "module": "db_gateway",
  "params": {
    "entity": "employees",
    "group_by": ["department"],
    "aggregations": {"salary": "avg", "id": "count"},
    "having": {"count_id": {"$gte": 5}}
  }
}
```

---

## search

Full-text search across specified columns using LIKE/ILIKE.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `entity` | string | Yes | -- | Table/entity name. |
| `query` | string | Yes | -- | Text to search for. |
| `columns` | array | Yes | -- | Columns to search across. |
| `case_sensitive` | boolean | No | `false` | Case-sensitive search. |
| `limit` | integer | No | `100` | Max results (1--10,000). |
| `connection_id` | string | No | `"default"` | Connection to use. |

**Returns:** `{entity, query, rows, row_count, elapsed_ms, connection_id}`
