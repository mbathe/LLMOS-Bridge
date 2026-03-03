# Changelog

All notable changes to the **database** module will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this module adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-02-01

### Added

- Connection management: `connect` and `disconnect` actions with named connection IDs.
- Multi-driver support: SQLite (built-in), PostgreSQL (`psycopg2`), MySQL (`mysql-connector-python`).
- Direct SQL execution via `execute_query` (DDL, DML) with parameterised queries.
- SELECT queries via `fetch_results` with row-limit cap and column metadata.
- Convenience CRUD: `insert_record`, `update_record`, `delete_record`.
- Schema introspection: `list_tables`, `get_table_schema`, `create_table`.
- Transaction control: `begin_transaction`, `commit_transaction`, `rollback_transaction`.
- Per-connection threading locks for safe concurrent access across plan actions.
- All blocking I/O offloaded to `asyncio.to_thread`.
- SQLite WAL mode and foreign key enforcement enabled by default.
- Conflict resolution (`on_conflict`) for `insert_record` (error, ignore, replace).
- Safety flag (`confirm=true`) required for `delete_record`.
- Security decorators: `@requires_permission`, `@sensitive_action`, `@audit_trail`.
