# Database Module -- Action Reference

## connect

Open a database connection (SQLite, PostgreSQL, or MySQL).

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `driver` | string | No | `"sqlite"` | Database driver. One of: `sqlite`, `postgresql`, `mysql`. |
| `database` | string | Yes | -- | Database name or file path. For SQLite, use a file path or `:memory:`. |
| `host` | string | No | `"localhost"` | Database host (PostgreSQL/MySQL). |
| `port` | integer | No | -- | Database port. Defaults to 5432 (PostgreSQL) or 3306 (MySQL). |
| `user` | string | No | -- | Database user. |
| `password` | string | No | -- | Database password. |
| `connection_id` | string | No | `"default"` | Logical connection name, reusable across actions. |
| `timeout` | integer | No | `10` | Connection timeout in seconds (1--60). |

**Returns:** `{connection_id, driver, database, status}`

**Example:**
```json
{
  "action": "connect",
  "module": "database",
  "params": {
    "driver": "sqlite",
    "database": "/tmp/myapp.db"
  }
}
```

---

## disconnect

Close an active database connection.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `connection_id` | string | No | `"default"` | Connection to close. |

**Returns:** `{connection_id, status}`

---

## execute_query

Execute a SQL statement (INSERT, UPDATE, DELETE, DDL). Returns rows affected.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `sql` | string | Yes | -- | SQL statement to execute. |
| `params` | array | No | `[]` | Query parameters for parameterised queries. |
| `connection_id` | string | No | `"default"` | Connection to use. |
| `timeout` | integer | No | `30` | Query timeout in seconds (1--300). |

**Returns:** `{rows_affected, elapsed_ms, connection_id}`

**Example:**
```json
{
  "action": "execute_query",
  "module": "database",
  "params": {
    "sql": "INSERT INTO users (name, email) VALUES (?, ?)",
    "params": ["Alice", "alice@example.com"]
  }
}
```

---

## fetch_results

Execute a SELECT query and return rows as a list of dicts.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `sql` | string | Yes | -- | SELECT query to execute. |
| `params` | array | No | `[]` | Query parameters. |
| `connection_id` | string | No | `"default"` | Connection to use. |
| `max_rows` | integer | No | `1000` | Maximum rows to return (1--10,000). |
| `timeout` | integer | No | `30` | Query timeout in seconds (1--300). |

**Returns:** `{columns, rows, row_count, truncated, elapsed_ms, connection_id}`

**Example:**
```json
{
  "action": "fetch_results",
  "module": "database",
  "params": {
    "sql": "SELECT * FROM users WHERE age >= ?",
    "params": [18],
    "max_rows": 50
  }
}
```

---

## insert_record

Insert a record into a table using column-value mapping.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `table` | string | Yes | -- | Target table name. |
| `record` | object | Yes | -- | Column-to-value mapping. |
| `connection_id` | string | No | `"default"` | Connection to use. |
| `on_conflict` | string | No | `"error"` | Conflict resolution: `error`, `ignore`, or `replace`. |

**Returns:** `{table, inserted, last_row_id, connection_id}`

---

## update_record

Update records matching a WHERE clause.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `table` | string | Yes | -- | Target table name. |
| `values` | object | Yes | -- | Columns to update (column-value mapping). |
| `where` | object | Yes | -- | WHERE clause as column-value mapping. |
| `connection_id` | string | No | `"default"` | Connection to use. |

**Returns:** `{table, rows_affected, connection_id}`

---

## delete_record

Delete records matching a WHERE clause. Requires `confirm=true`.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `table` | string | Yes | -- | Target table name. |
| `where` | object | Yes | -- | WHERE clause as column-value mapping. |
| `connection_id` | string | No | `"default"` | Connection to use. |
| `confirm` | boolean | Yes | `false` | Safety flag -- must be `true` to execute. |

**Returns:** `{table, rows_deleted, deleted, connection_id}`

---

## create_table

Create a new table with column definitions.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `table` | string | Yes | -- | Table name. |
| `columns` | array | Yes | -- | Column definitions: `[{"name": "id", "type": "INTEGER PRIMARY KEY"}, ...]`. |
| `if_not_exists` | boolean | No | `true` | Skip creation if table already exists. |
| `connection_id` | string | No | `"default"` | Connection to use. |

**Returns:** `{table, created, connection_id}`

---

## list_tables

List all tables in the connected database.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `connection_id` | string | No | `"default"` | Connection to use. |
| `schema` | string | No | -- | Schema name (PostgreSQL only). |

**Returns:** `{tables, count, connection_id}`

---

## get_table_schema

Get column definitions for a table.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `table` | string | Yes | -- | Table name. |
| `connection_id` | string | No | `"default"` | Connection to use. |

**Returns:** `{table, columns: [{name, type, nullable, default, primary_key}], column_count, connection_id}`

---

## begin_transaction

Start an explicit transaction.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `connection_id` | string | No | `"default"` | Connection to use. |
| `isolation_level` | string | No | `"deferred"` | Isolation level: `deferred`, `immediate`, `exclusive`, `read_committed`, `serializable`. |

**Returns:** `{transaction, isolation_level, connection_id}`

---

## commit_transaction

Commit the current transaction.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `connection_id` | string | No | `"default"` | Connection to use. |

**Returns:** `{transaction, connection_id}`

---

## rollback_transaction

Roll back the current transaction.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `connection_id` | string | No | `"default"` | Connection to use. |

**Returns:** `{transaction, connection_id}`
