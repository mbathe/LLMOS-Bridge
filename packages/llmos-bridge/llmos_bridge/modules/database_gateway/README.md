# Database Gateway Module

Semantic database gateway -- query databases using entity names and MongoDB-like filters instead of raw SQL.

## Overview

The Database Gateway module (`db_gateway`) provides a semantic, SQL-free interface
for database operations. Instead of writing raw SQL, the LLM interacts with databases
through entity names and MongoDB-like filter syntax (`$gte`, `$in`, `$like`, etc.).
The module is a thin dispatcher that validates parameters, resolves the correct
adapter for each connection, and delegates all database operations to
`BaseDbAdapter` or `BaseAsyncDbAdapter` implementations.

Built-in support covers SQLite, PostgreSQL, and MySQL via SQLAlchemy. The adapter
registry is extensible: add any SQLAlchemy-compatible database in a few lines, or
implement a custom adapter for non-SQL backends (MongoDB, Redis, etc.). Pip-installed
adapter plugins are auto-discovered via entry points.

The module provides automatic schema introspection with caching, connection pooling
via SQLAlchemy, and context snippets that inject database schemas into the LLM prompt
for informed query generation.

## Actions

| Action | Description | Risk | Permission |
|--------|-------------|------|------------|
| `connect` | Open a database connection | Medium | `database.read` |
| `disconnect` | Close a database connection | Low | `database.read` |
| `introspect` | Get full schema: tables, columns, types, foreign keys, indexes | Low | `database.read` |
| `find` | Find records matching a filter with projection, ordering, pagination | Low | `database.read` |
| `find_one` | Find a single record matching a filter | Low | `database.read` |
| `create` | Create a new record in an entity | Medium | `database.write` |
| `create_many` | Create multiple records in a single batch | Medium | `database.write` |
| `update` | Update records matching a filter | Medium | `database.write` |
| `delete` | Delete records matching a filter (requires confirm) | High | `database.delete` |
| `count` | Count records matching a filter | Low | `database.read` |
| `aggregate` | Aggregate with GROUP BY and functions (sum, avg, min, max, count) | Low | `database.read` |
| `search` | Full-text search across specified columns | Low | `database.read` |

## Quick Start

```yaml
actions:
  - id: connect-db
    module: db_gateway
    action: connect
    params:
      driver: sqlite
      database: /tmp/myapp.db

  - id: create-user
    module: db_gateway
    action: create
    params:
      entity: users
      data: { name: Alice, email: alice@example.com, age: 30 }
    depends_on: [connect-db]

  - id: find-active-adults
    module: db_gateway
    action: find
    params:
      entity: users
      filter: { age: { "$gte": 18 }, status: active }
      order_by: [name]
      limit: 50
    depends_on: [connect-db]
```

## Requirements

| Dependency | Required | Notes |
|-----------|----------|-------|
| `sqlalchemy>=2.0` | Yes | Core ORM and connection pooling |
| `psycopg2-binary` | Optional | PostgreSQL driver |
| `mysql-connector-python` | Optional | MySQL driver |

Community adapter plugins can be installed via pip and are auto-discovered through
entry points.

## Configuration

| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_connections` | `10` | Maximum connection pool size per adapter |
| `schema_cache_ttl` | `300` | Schema cache time-to-live in seconds |

These are constructor parameters passed when the module is instantiated.

## Filter Syntax

The gateway uses a MongoDB-like filter syntax:

| Operator | Description | Example |
|----------|-------------|---------|
| (equality) | Exact match | `{"status": "active"}` |
| `$gte` | Greater than or equal | `{"age": {"$gte": 18}}` |
| `$gt` | Greater than | `{"price": {"$gt": 100}}` |
| `$lte` | Less than or equal | `{"score": {"$lte": 50}}` |
| `$lt` | Less than | `{"count": {"$lt": 10}}` |
| `$ne` | Not equal | `{"status": {"$ne": "deleted"}}` |
| `$in` | In list | `{"role": {"$in": ["admin", "editor"]}}` |
| `$like` | LIKE pattern | `{"name": {"$like": "A%"}}` |

## Platform Support

| Platform | Status |
|----------|--------|
| Linux | Supported |
| macOS | Supported |
| Windows | Supported |

## Related Modules

- **database** -- Raw SQL database access with transaction control. Use when you
  need DDL operations, complex joins, or explicit transactions.
- **api_http** -- Fetch data from external APIs to feed into database operations.
- **filesystem** -- Read/write CSV or JSON files alongside database operations.
- **excel** -- Export query results to Excel spreadsheets.
