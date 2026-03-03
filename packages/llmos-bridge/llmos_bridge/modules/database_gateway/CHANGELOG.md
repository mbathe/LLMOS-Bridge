# Changelog

All notable changes to the **db_gateway** module will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this module adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0] - 2026-02-01

### Added

- `search` action for full-text search across specified columns using LIKE/ILIKE.
- `aggregate` action with GROUP BY and aggregate functions (sum, avg, min, max, count).
- `create_many` action for bulk record insertion.
- HAVING clause support in `aggregate` via post-aggregation filters.
- Context snippet injection: active database schemas are injected into the LLM prompt.
- Rate limiting (`@rate_limited`) on write actions (60 calls/minute).
- Detailed audit trail on `delete` action.

## [1.0.0] - 2026-01-15

### Added

- Initial release with 9 core actions: connect, disconnect, introspect, find, find_one, create, update, delete, count.
- Multi-backend support via SQLAlchemy: SQLite, PostgreSQL, MySQL.
- MongoDB-like filter syntax for all query operations.
- Adapter registry with pip-installable plugin auto-discovery via entry points.
- Schema introspection with TTL-based caching.
- Connection pooling via SQLAlchemy engine pools.
- Async and sync adapter support (`BaseAsyncDbAdapter`, `BaseDbAdapter`).
- Safety flag (`confirm=true`) required for `delete` action.
- Security decorators: `@requires_permission`, `@sensitive_action`, `@audit_trail`.
