"""Database Gateway — Schema introspection via ``sqlalchemy.inspect()``.

Discovers tables, columns (name/type/nullable/pk/autoincrement/default/fk),
indexes, and foreign key relationships. Results are cached with a configurable
TTL to avoid repeated reflection on every request.
"""

from __future__ import annotations

import time
from typing import Any

import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


# ---------------------------------------------------------------------------
# Schema cache
# ---------------------------------------------------------------------------


class SchemaCache:
    """TTL-cached schema introspection results."""

    def __init__(self, ttl_seconds: int = 300) -> None:
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._ttl = ttl_seconds

    def get(self, connection_id: str) -> dict[str, Any] | None:
        entry = self._cache.get(connection_id)
        if entry is None:
            return None
        ts, schema = entry
        if self._ttl > 0 and (time.time() - ts) > self._ttl:
            del self._cache[connection_id]
            return None
        return schema

    def set(self, connection_id: str, schema: dict[str, Any]) -> None:
        self._cache[connection_id] = (time.time(), schema)

    def invalidate(self, connection_id: str) -> None:
        self._cache.pop(connection_id, None)

    def invalidate_all(self) -> None:
        self._cache.clear()


# ---------------------------------------------------------------------------
# Schema introspection
# ---------------------------------------------------------------------------


def introspect_schema(
    engine: sa.Engine,
    schema_name: str | None = None,
) -> dict[str, Any]:
    """Full schema introspection via ``sqlalchemy.inspect()``.

    Returns a dict with ``tables``, ``table_count``, and ``schema`` keys.
    Each table entry includes columns, primary key, foreign keys, and indexes.
    """
    inspector = sa_inspect(engine)

    table_names = inspector.get_table_names(schema=schema_name)
    tables: list[dict[str, Any]] = []

    for tname in sorted(table_names):
        columns_info = inspector.get_columns(tname, schema=schema_name)
        pk_info = inspector.get_pk_constraint(tname, schema=schema_name)
        fk_info = inspector.get_foreign_keys(tname, schema=schema_name)
        idx_info = inspector.get_indexes(tname, schema=schema_name)

        pk_columns = pk_info.get("constrained_columns", []) if pk_info else []

        columns: list[dict[str, Any]] = []
        for col in columns_info:
            col_name = col["name"]
            col_fks = []
            for fk in fk_info:
                if col_name in fk.get("constrained_columns", []):
                    referred_cols = fk.get("referred_columns", [])
                    idx = fk["constrained_columns"].index(col_name)
                    target_col = referred_cols[idx] if idx < len(referred_cols) else ""
                    col_fks.append({
                        "target_table": fk.get("referred_table", ""),
                        "target_column": target_col,
                    })

            columns.append({
                "name": col_name,
                "type": str(col.get("type", "UNKNOWN")),
                "nullable": col.get("nullable", True),
                "primary_key": col_name in pk_columns,
                "autoincrement": col.get("autoincrement", False),
                "default": _safe_default(col.get("default")),
                "foreign_keys": col_fks,
            })

        foreign_keys = [
            {
                "columns": fk.get("constrained_columns", []),
                "referred_table": fk.get("referred_table", ""),
                "referred_columns": fk.get("referred_columns", []),
            }
            for fk in fk_info
        ]

        indexes = [
            {
                "name": idx.get("name", ""),
                "columns": idx.get("column_names", []),
                "unique": idx.get("unique", False),
            }
            for idx in idx_info
        ]

        tables.append({
            "name": tname,
            "columns": columns,
            "primary_key": pk_columns,
            "foreign_keys": foreign_keys,
            "indexes": indexes,
        })

    return {
        "tables": tables,
        "table_count": len(tables),
        "schema": schema_name or "default",
    }


def _safe_default(val: Any) -> Any:
    """Render a column default as a JSON-safe value."""
    if val is None:
        return None
    return str(val)


# ---------------------------------------------------------------------------
# DB-level privilege introspection
# ---------------------------------------------------------------------------


def introspect_privileges(engine: sa.Engine) -> dict[str, Any] | None:
    """Query the database to discover the connected user's privileges.

    Currently supports PostgreSQL. Returns ``None`` for unsupported dialects.

    Returns::

        {
            "user": "eyeflow",
            "can_select": True,
            "can_insert": True,
            "can_update": True,
            "can_delete": True,
            "can_create_table": True,
            "can_drop_table": False,
            "is_superuser": False,
            "privileges": ["SELECT", "INSERT", "UPDATE", "DELETE", "CREATE"],
        }
    """
    dialect_name = engine.dialect.name

    if dialect_name == "postgresql":
        return _introspect_pg_privileges(engine)

    # SQLite — always full access (file-based)
    if dialect_name == "sqlite":
        return {
            "user": "local",
            "can_select": True,
            "can_insert": True,
            "can_update": True,
            "can_delete": True,
            "can_create_table": True,
            "can_drop_table": True,
            "is_superuser": True,
            "privileges": ["ALL (SQLite — file-based, no user privileges)"],
        }

    return None


def _introspect_pg_privileges(engine: sa.Engine) -> dict[str, Any]:
    """PostgreSQL privilege introspection."""
    with engine.connect() as conn:
        # Get current user
        row = conn.execute(sa.text("SELECT current_user")).fetchone()
        current_user = row[0] if row else "unknown"

        # Check superuser status
        row = conn.execute(sa.text(
            "SELECT usesuper FROM pg_user WHERE usename = current_user"
        )).fetchone()
        is_superuser = bool(row and row[0])

        if is_superuser:
            return {
                "user": current_user,
                "can_select": True,
                "can_insert": True,
                "can_update": True,
                "can_delete": True,
                "can_create_table": True,
                "can_drop_table": True,
                "is_superuser": True,
                "privileges": ["ALL (superuser)"],
            }

        # Check schema-level CREATE privilege
        row = conn.execute(sa.text(
            "SELECT has_schema_privilege(current_user, 'public', 'CREATE')"
        )).fetchone()
        can_create = bool(row and row[0])

        # Aggregate table privileges across all public tables
        rows = conn.execute(sa.text("""
            SELECT privilege_type
            FROM information_schema.table_privileges
            WHERE grantee = current_user
              AND table_schema = 'public'
            GROUP BY privilege_type
        """)).fetchall()
        privilege_set = {r[0] for r in rows}

        return {
            "user": current_user,
            "can_select": "SELECT" in privilege_set,
            "can_insert": "INSERT" in privilege_set,
            "can_update": "UPDATE" in privilege_set,
            "can_delete": "DELETE" in privilege_set,
            "can_create_table": can_create,
            "can_drop_table": "DROP" in privilege_set or can_create,
            "is_superuser": False,
            "privileges": sorted(privilege_set),
        }


# ---------------------------------------------------------------------------
# LLM-friendly text formatting
# ---------------------------------------------------------------------------


def schema_to_context_string(
    schema: dict[str, Any],
    *,
    max_tables: int = 50,
    max_columns_per_table: int = 30,
) -> str:
    """Format introspected schema as a compact LLM-friendly text snippet.

    Example output::

        ### users
        - id: INTEGER (PK, autoincrement)
        - name: TEXT (not null)
        - email: TEXT (unique index)
        - department_id: INTEGER (FK → departments.id)

        ### departments
        - id: INTEGER (PK, autoincrement)
        - name: TEXT (not null)
    """
    tables = schema.get("tables", [])
    if not tables:
        return "(no tables)"

    lines: list[str] = []

    for i, tbl in enumerate(tables):
        if i >= max_tables:
            lines.append(f"\n... and {len(tables) - max_tables} more tables")
            break

        lines.append(f"\n#### {tbl['name']}")

        # Build index lookup for unique markers
        unique_cols: set[str] = set()
        for idx in tbl.get("indexes", []):
            if idx.get("unique"):
                for col_name in idx.get("columns", []):
                    unique_cols.add(col_name)

        for j, col in enumerate(tbl.get("columns", [])):
            if j >= max_columns_per_table:
                remaining = len(tbl["columns"]) - max_columns_per_table
                lines.append(f"  ... and {remaining} more columns")
                break

            annotations: list[str] = []
            if col.get("primary_key"):
                annotations.append("PK")
            if col.get("autoincrement") and col.get("autoincrement") != "auto":
                annotations.append("autoincrement")
            if not col.get("nullable", True) and not col.get("primary_key"):
                annotations.append("not null")
            if col["name"] in unique_cols:
                annotations.append("unique")
            if col.get("default") is not None:
                annotations.append(f"default: {col['default']}")
            for fk in col.get("foreign_keys", []):
                annotations.append(
                    f"FK → {fk['target_table']}.{fk['target_column']}"
                )

            suffix = f" ({', '.join(annotations)})" if annotations else ""
            lines.append(f"- {col['name']}: {col['type']}{suffix}")

    return "\n".join(lines)
