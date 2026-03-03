# Database Module

SQL database operations -- connect, query, CRUD, schema introspection, and transaction management. Supports SQLite (built-in), PostgreSQL, and MySQL.

## Overview

The Database module provides direct SQL database access for IML plans. It manages
named connections to SQLite, PostgreSQL, and MySQL databases, executes parameterised
queries, offers convenience CRUD actions (insert/update/delete), introspects schemas,
and supports explicit transaction control. All blocking I/O runs in
`asyncio.to_thread` so the event loop is never starved. Connections are protected by
per-connection threading locks for safe concurrent access across plan actions.

## Actions

| Action | Description | Risk | Permission |
|--------|-------------|------|------------|
| `connect` | Open a database connection (SQLite, PostgreSQL, or MySQL) | Medium | `database.read` |
| `disconnect` | Close an active database connection | Low | `database.read` |
| `execute_query` | Execute a SQL statement (INSERT, UPDATE, DELETE, DDL) | High | `database.write` |
| `fetch_results` | Execute a SELECT query and return rows as dicts | Low | `database.read` |
| `insert_record` | Insert a record using column-value mapping | Medium | `database.write` |
| `update_record` | Update records matching a WHERE clause | Medium | `database.write` |
| `delete_record` | Delete records matching a WHERE clause (requires confirm) | High | `database.delete` |
| `create_table` | Create a new table with column definitions | Medium | `database.write` |
| `list_tables` | List all tables in the connected database | Low | `database.read` |
| `get_table_schema` | Get column definitions for a table | Low | `database.read` |
| `begin_transaction` | Start an explicit transaction | Medium | `database.write` |
| `commit_transaction` | Commit the current transaction | Medium | `database.write` |
| `rollback_transaction` | Roll back the current transaction | Low | `database.write` |

## Quick Start

```yaml
actions:
  - id: connect-db
    module: database
    action: connect
    params:
      driver: sqlite
      database: /tmp/myapp.db

  - id: create-users
    module: database
    action: create_table
    params:
      table: users
      columns:
        - { name: id, type: "INTEGER PRIMARY KEY AUTOINCREMENT" }
        - { name: name, type: "TEXT NOT NULL" }
        - { name: email, type: "TEXT UNIQUE" }
    depends_on: [connect-db]

  - id: insert-user
    module: database
    action: insert_record
    params:
      table: users
      record: { name: Alice, email: alice@example.com }
    depends_on: [create-users]

  - id: query-users
    module: database
    action: fetch_results
    params:
      sql: "SELECT * FROM users"
    depends_on: [insert-user]
```

## Requirements

| Dependency | Required | Notes |
|-----------|----------|-------|
| `aiosqlite` | Yes | Async SQLite support |
| `psycopg2-binary` | Optional | Required for PostgreSQL connections |
| `mysql-connector-python` | Optional | Required for MySQL connections |

SQLite support uses the Python standard library `sqlite3` module and is always
available. PostgreSQL and MySQL drivers are imported lazily and only required when
connecting to those respective backends.

## Configuration

Uses default LLMOS Bridge configuration. The module manages its own connection pool
internally via named connection IDs. No additional configuration keys are needed
beyond the standard LLMOS Bridge settings.

## Platform Support

| Platform | Status |
|----------|--------|
| Linux | Supported |
| macOS | Supported |
| Windows | Supported |

## Related Modules

- **db_gateway** -- Semantic, SQL-free database gateway with MongoDB-like filter
  syntax. Preferred when the LLM should not write raw SQL.
- **filesystem** -- Read/write files that may be used alongside database exports
  or CSV imports.
- **api_http** -- Fetch data from external APIs to insert into local databases.
