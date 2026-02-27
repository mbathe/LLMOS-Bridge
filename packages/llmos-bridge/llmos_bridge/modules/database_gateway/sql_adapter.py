"""Database Gateway — SQLAlchemy adapter implementation.

Handles **all** SQL databases supported by SQLAlchemy dialects.
New SQL drivers are added by registering a :class:`DriverProfile` —
this adapter handles query building, filtering, introspection, and
schema formatting automatically.

Internal dependencies (unchanged from the original implementation):

- ``adapters.py``     → ``AdapterManager`` (engine + MetaData management)
- ``filters.py``      → ``compile_filter()`` (MongoDB-like dict → SQLAlchemy expr)
- ``introspector.py`` → ``introspect_schema()`` + ``schema_to_context_string()``
"""

from __future__ import annotations

import time
from typing import Any

import sqlalchemy as sa

from llmos_bridge.exceptions import ActionExecutionError
from llmos_bridge.modules.database_gateway.adapters import AdapterManager
from llmos_bridge.modules.database_gateway.base_adapter import BaseDbAdapter
from llmos_bridge.modules.database_gateway.filters import (
    FilterCompilationError,
    compile_filter,
)
from llmos_bridge.modules.database_gateway.introspector import (
    SchemaCache,
    introspect_privileges,
    introspect_schema,
    schema_to_context_string,
)
from llmos_bridge.modules.database_gateway.registry import AdapterRegistry

MODULE_ID = "db_gateway"

_AGG_FUNCS = {
    "sum": sa.func.sum,
    "avg": sa.func.avg,
    "min": sa.func.min,
    "max": sa.func.max,
    "count": sa.func.count,
}


class SQLAlchemyAdapter(BaseDbAdapter):
    """Adapter for all SQL databases via SQLAlchemy Core.

    Wraps ``AdapterManager`` (engine management), ``compile_filter``
    (MongoDB-like filters), and ``introspect_schema`` (schema discovery).
    """

    def __init__(
        self,
        max_connections: int = 10,
        schema_cache_ttl: int = 300,
    ) -> None:
        self._manager = AdapterManager(max_connections=max_connections)
        self._schema_cache = SchemaCache(ttl_seconds=schema_cache_ttl)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _wrap_filter_error(
        exc: FilterCompilationError, action: str
    ) -> ActionExecutionError:
        return ActionExecutionError(
            module_id=MODULE_ID,
            action=action,
            cause=RuntimeError(str(exc)),
        )

    @staticmethod
    def _build_select(
        table: sa.Table, columns: list[str] | None
    ) -> sa.Select:  # type: ignore[type-arg]
        if columns:
            col_objs = [table.c[c] for c in columns]
            return sa.select(*col_objs)
        return sa.select(table)

    @staticmethod
    def _apply_order_by(
        stmt: sa.Select,  # type: ignore[type-arg]
        table: sa.Table,
        order_by: list[str] | None,
    ) -> sa.Select:  # type: ignore[type-arg]
        if not order_by:
            return stmt
        for col_spec in order_by:
            if col_spec.startswith("-"):
                stmt = stmt.order_by(table.c[col_spec[1:]].desc())
            else:
                stmt = stmt.order_by(table.c[col_spec].asc())
        return stmt

    def _build_connect_url(
        self,
        driver: str,
        host: str,
        port: int | None,
        database: str,
        user: str | None,
        password: str | None,
    ) -> str:
        """Build a SQLAlchemy URL using the registered DriverProfile."""
        profile = AdapterRegistry.get_sql_driver_profile(driver)
        if profile is None:
            # Fallback to AdapterManager's built-in logic
            return AdapterManager._build_url(
                driver, host, port, database, user, password
            )

        dialect = profile.dialect

        # SQLite special case — check dialect, not driver name, so custom
        # drivers using the sqlite dialect also get correct URL format
        if dialect.startswith("sqlite"):
            from pathlib import Path

            if database == ":memory:":
                return "sqlite:///:memory:"
            path = Path(database).resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            return f"sqlite:///{path}"

        effective_port = port or profile.default_port or 0
        auth = ""
        if user:
            auth = user
            if password:
                auth += f":{password}"
            auth += "@"

        return f"{dialect}://{auth}{host}:{effective_port}/{database}"

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def connect(
        self,
        connection_id: str,
        *,
        url: str | None = None,
        driver: str = "",
        host: str = "localhost",
        port: int | None = None,
        database: str = "",
        user: str | None = None,
        password: str | None = None,
        pool_size: int = 5,
        auto_introspect: bool = True,
    ) -> dict[str, Any]:
        # Build URL from profile if not provided directly
        if url is None:
            url = self._build_connect_url(
                driver, host, port, database, user, password
            )

        # Determine driver name for profile lookup
        if not driver and url:
            driver = self._detect_driver(url)

        profile = AdapterRegistry.get_sql_driver_profile(driver)

        # Merge profile engine_kwargs
        extra_kwargs: dict[str, Any] = {}
        if profile and profile.engine_kwargs:
            extra_kwargs.update(profile.engine_kwargs)

        # Use AdapterManager.connect for engine + metadata management
        result = self._manager.connect(
            connection_id=connection_id,
            url=url,
            driver=driver,
            host=host,
            port=port,
            database=database,
            user=user,
            password=password,
            pool_size=pool_size,
            auto_introspect=auto_introspect,
        )

        # Apply post-connect hook from profile (overrides default)
        if profile and profile.post_connect_hook:
            entry = self._manager.get_entry(connection_id)
            profile.post_connect_hook(entry.engine)

        # Cache schema
        if auto_introspect:
            entry = self._manager.get_entry(connection_id)
            schema = introspect_schema(entry.engine)
            self._schema_cache.set(connection_id, schema)

        return result

    def disconnect(self, connection_id: str) -> dict[str, Any]:
        self._schema_cache.invalidate(connection_id)
        return self._manager.disconnect(connection_id)

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def introspect(
        self,
        connection_id: str,
        *,
        schema_name: str | None = None,
        refresh: bool = False,
    ) -> dict[str, Any]:
        if not refresh:
            cached = self._schema_cache.get(connection_id)
            if cached is not None:
                return {
                    "connection_id": connection_id,
                    "cached": True,
                    **cached,
                }

        entry = self._manager.get_entry(connection_id)
        self._manager.refresh_metadata(connection_id)
        schema = introspect_schema(entry.engine, schema_name=schema_name)
        self._schema_cache.set(connection_id, schema)
        return {
            "connection_id": connection_id,
            "cached": False,
            **schema,
        }

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def find(
        self,
        connection_id: str,
        entity: str,
        *,
        filter_dict: dict[str, Any] | None = None,
        select: list[str] | None = None,
        order_by: list[str] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        entry = self._manager.get_entry(connection_id)
        with entry.lock:
            table = self._manager.get_table(connection_id, entity)
            stmt = self._build_select(table, select)

            if filter_dict:
                try:
                    where = compile_filter(table, filter_dict)
                except FilterCompilationError as exc:
                    raise self._wrap_filter_error(exc, "find") from exc
                stmt = stmt.where(where)

            stmt = self._apply_order_by(stmt, table, order_by)
            stmt = stmt.limit(limit).offset(offset)

            start = time.monotonic()
            with entry.engine.connect() as conn:
                result = conn.execute(stmt)
                rows = [dict(row._mapping) for row in result]
            elapsed = time.monotonic() - start

            return {
                "entity": entity,
                "rows": rows,
                "row_count": len(rows),
                "truncated": len(rows) >= limit,
                "elapsed_ms": round(elapsed * 1000, 2),
                "connection_id": connection_id,
            }

    def find_one(
        self,
        connection_id: str,
        entity: str,
        *,
        filter_dict: dict[str, Any] | None = None,
        select: list[str] | None = None,
    ) -> dict[str, Any]:
        entry = self._manager.get_entry(connection_id)
        with entry.lock:
            table = self._manager.get_table(connection_id, entity)
            stmt = self._build_select(table, select)

            if filter_dict:
                try:
                    where = compile_filter(table, filter_dict)
                except FilterCompilationError as exc:
                    raise self._wrap_filter_error(exc, "find_one") from exc
                stmt = stmt.where(where)

            stmt = stmt.limit(1)

            with entry.engine.connect() as conn:
                result = conn.execute(stmt)
                row = result.fetchone()

            if row is None:
                return {
                    "entity": entity,
                    "found": False,
                    "record": None,
                    "connection_id": connection_id,
                }
            return {
                "entity": entity,
                "found": True,
                "record": dict(row._mapping),
                "connection_id": connection_id,
            }

    def count(
        self,
        connection_id: str,
        entity: str,
        *,
        filter_dict: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        entry = self._manager.get_entry(connection_id)
        with entry.lock:
            table = self._manager.get_table(connection_id, entity)
            stmt = sa.select(sa.func.count()).select_from(table)

            if filter_dict:
                try:
                    where = compile_filter(table, filter_dict)
                except FilterCompilationError as exc:
                    raise self._wrap_filter_error(exc, "count") from exc
                stmt = stmt.where(where)

            with entry.engine.connect() as conn:
                result = conn.execute(stmt)
                count_val = result.scalar()

            return {
                "entity": entity,
                "count": count_val or 0,
                "connection_id": connection_id,
            }

    def search(
        self,
        connection_id: str,
        entity: str,
        *,
        query: str,
        columns: list[str],
        case_sensitive: bool = False,
        limit: int = 100,
    ) -> dict[str, Any]:
        entry = self._manager.get_entry(connection_id)
        with entry.lock:
            table = self._manager.get_table(connection_id, entity)
            stmt = sa.select(table)

            search_clauses = []
            for col_name in columns:
                if col_name not in table.c:
                    raise ActionExecutionError(
                        module_id=MODULE_ID,
                        action="search",
                        cause=RuntimeError(
                            f"Unknown column '{col_name}' in entity '{entity}'."
                        ),
                    )
                col = table.c[col_name]
                pattern = f"%{query}%"
                if case_sensitive:
                    search_clauses.append(col.like(pattern))
                else:
                    search_clauses.append(col.ilike(pattern))

            if search_clauses:
                stmt = stmt.where(sa.or_(*search_clauses))

            stmt = stmt.limit(limit)

            start = time.monotonic()
            with entry.engine.connect() as conn:
                result = conn.execute(stmt)
                rows = [dict(row._mapping) for row in result]
            elapsed = time.monotonic() - start

            return {
                "entity": entity,
                "query": query,
                "rows": rows,
                "row_count": len(rows),
                "elapsed_ms": round(elapsed * 1000, 2),
                "connection_id": connection_id,
            }

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def create(
        self,
        connection_id: str,
        entity: str,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        entry = self._manager.get_entry(connection_id)
        with entry.lock:
            table = self._manager.get_table(connection_id, entity)
            stmt = table.insert().values(**data)

            with entry.engine.connect() as conn:
                result = conn.execute(stmt)
                conn.commit()
                pk = result.inserted_primary_key
                lastrowid = pk[0] if pk else None

            return {
                "entity": entity,
                "created": True,
                "inserted_id": lastrowid,
                "connection_id": connection_id,
            }

    def create_many(
        self,
        connection_id: str,
        entity: str,
        records: list[dict[str, Any]],
    ) -> dict[str, Any]:
        entry = self._manager.get_entry(connection_id)
        with entry.lock:
            table = self._manager.get_table(connection_id, entity)

            with entry.engine.connect() as conn:
                result = conn.execute(table.insert(), records)
                conn.commit()
                rowcount = result.rowcount

            return {
                "entity": entity,
                "created": True,
                "inserted_count": rowcount,
                "connection_id": connection_id,
            }

    def update(
        self,
        connection_id: str,
        entity: str,
        filter_dict: dict[str, Any],
        values: dict[str, Any],
    ) -> dict[str, Any]:
        entry = self._manager.get_entry(connection_id)
        with entry.lock:
            table = self._manager.get_table(connection_id, entity)

            try:
                where = compile_filter(table, filter_dict)
            except FilterCompilationError as exc:
                raise self._wrap_filter_error(exc, "update") from exc

            stmt = table.update().where(where).values(**values)

            with entry.engine.connect() as conn:
                result = conn.execute(stmt)
                conn.commit()
                rowcount = result.rowcount

            return {
                "entity": entity,
                "rows_affected": rowcount,
                "connection_id": connection_id,
            }

    def delete(
        self,
        connection_id: str,
        entity: str,
        filter_dict: dict[str, Any],
    ) -> dict[str, Any]:
        entry = self._manager.get_entry(connection_id)
        with entry.lock:
            table = self._manager.get_table(connection_id, entity)

            try:
                where = compile_filter(table, filter_dict)
            except FilterCompilationError as exc:
                raise self._wrap_filter_error(exc, "delete") from exc

            stmt = table.delete().where(where)

            with entry.engine.connect() as conn:
                result = conn.execute(stmt)
                conn.commit()
                rowcount = result.rowcount

            return {
                "entity": entity,
                "deleted": True,
                "rows_deleted": rowcount,
                "connection_id": connection_id,
            }

    # ------------------------------------------------------------------
    # Aggregation
    # ------------------------------------------------------------------

    def aggregate(
        self,
        connection_id: str,
        entity: str,
        *,
        group_by: list[str],
        aggregations: dict[str, str],
        filter_dict: dict[str, Any] | None = None,
        having: dict[str, Any] | None = None,
        order_by: list[str] | None = None,
        limit: int = 1000,
    ) -> dict[str, Any]:
        entry = self._manager.get_entry(connection_id)
        with entry.lock:
            table = self._manager.get_table(connection_id, entity)

            # Build columns: group_by columns + aggregated columns
            select_cols: list[Any] = []
            for col_name in group_by:
                select_cols.append(table.c[col_name])

            agg_labels: dict[str, Any] = {}
            for col_name, func_name in aggregations.items():
                func_name_lower = func_name.lower()
                if func_name_lower not in _AGG_FUNCS:
                    raise ActionExecutionError(
                        module_id=MODULE_ID,
                        action="aggregate",
                        cause=RuntimeError(
                            f"Unknown aggregate function '{func_name}'. "
                            f"Supported: {list(_AGG_FUNCS.keys())}"
                        ),
                    )
                agg_func = _AGG_FUNCS[func_name_lower]
                label = f"{func_name_lower}_{col_name}"
                agg_col = agg_func(table.c[col_name]).label(label)
                select_cols.append(agg_col)
                agg_labels[label] = agg_col

            stmt = sa.select(*select_cols)

            # WHERE (pre-aggregation filter)
            if filter_dict:
                try:
                    where = compile_filter(table, filter_dict)
                except FilterCompilationError as exc:
                    raise self._wrap_filter_error(exc, "aggregate") from exc
                stmt = stmt.where(where)

            # GROUP BY
            for col_name in group_by:
                stmt = stmt.group_by(table.c[col_name])

            # HAVING (post-aggregation filter on aliases)
            if having:
                having_clauses = []
                for alias_name, condition in having.items():
                    if alias_name not in agg_labels:
                        raise ActionExecutionError(
                            module_id=MODULE_ID,
                            action="aggregate",
                            cause=RuntimeError(
                                f"Unknown aggregation alias '{alias_name}' in HAVING. "
                                f"Available aliases: {list(agg_labels.keys())}"
                            ),
                        )
                    label_col = agg_labels[alias_name]
                    if isinstance(condition, dict):
                        for op, val in condition.items():
                            having_clauses.append(
                                _apply_having_op(label_col, op, val)
                            )
                    else:
                        having_clauses.append(label_col == condition)
                if having_clauses:
                    stmt = stmt.having(sa.and_(*having_clauses))

            # ORDER BY
            if order_by:
                for col_spec in order_by:
                    desc = col_spec.startswith("-")
                    name = col_spec.lstrip("-")
                    if name in agg_labels:
                        col_ref = agg_labels[name]
                    elif name in [c.name for c in table.columns]:
                        col_ref = table.c[name]
                    else:
                        raise ActionExecutionError(
                            module_id=MODULE_ID,
                            action="aggregate",
                            cause=RuntimeError(
                                f"Unknown column/alias '{name}' in order_by."
                            ),
                        )
                    stmt = stmt.order_by(
                        col_ref.desc() if desc else col_ref.asc()
                    )

            stmt = stmt.limit(limit)

            start = time.monotonic()
            with entry.engine.connect() as conn:
                result = conn.execute(stmt)
                rows = [dict(row._mapping) for row in result]
            elapsed = time.monotonic() - start

            return {
                "entity": entity,
                "rows": rows,
                "row_count": len(rows),
                "elapsed_ms": round(elapsed * 1000, 2),
                "connection_id": connection_id,
            }

    # ------------------------------------------------------------------
    # Shared concrete methods
    # ------------------------------------------------------------------

    def list_connections(self) -> list[str]:
        return self._manager.list_connections()

    def close_all(self) -> None:
        self._schema_cache.invalidate_all()
        self._manager.close_all()

    def get_context_snippet(self, connection_id: str) -> str | None:
        cached = self._schema_cache.get(connection_id)
        if cached is None:
            return None
        entry = self._manager.get_entry(connection_id)
        parts = [
            f"### Connection: {connection_id} ({entry.driver_name})",
        ]

        # Include DB user privileges so the LLM knows what it can/cannot do
        try:
            privs = introspect_privileges(entry.engine)
            if privs:
                priv_lines = [f"**DB user:** {privs['user']}"]
                flags = []
                if privs["can_select"]:
                    flags.append("SELECT")
                if privs["can_insert"]:
                    flags.append("INSERT")
                if privs["can_update"]:
                    flags.append("UPDATE")
                if privs["can_delete"]:
                    flags.append("DELETE")
                if privs["can_create_table"]:
                    flags.append("CREATE TABLE")
                priv_lines.append(f"**DB privileges:** {', '.join(flags)}")
                if not privs["can_delete"]:
                    priv_lines.append(
                        "**WARNING:** The DB user does NOT have DELETE privilege — "
                        "DELETE operations will fail at the database level."
                    )
                if not privs["can_insert"]:
                    priv_lines.append(
                        "**WARNING:** The DB user does NOT have INSERT privilege — "
                        "CREATE operations will fail at the database level."
                    )
                parts.append("\n".join(priv_lines))
        except Exception:
            pass  # Don't fail context generation on privilege errors

        parts.append(schema_to_context_string(cached))
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_driver(url: str) -> str:
        """Detect driver name from a SQLAlchemy URL string."""
        lower = url.lower()
        # Check registered SQL drivers
        for driver_name in AdapterRegistry.list_drivers():
            profile = AdapterRegistry.get_sql_driver_profile(driver_name)
            if profile:
                dialect_prefix = profile.dialect.split("+")[0]
                if lower.startswith(dialect_prefix):
                    return driver_name
        # Fallback heuristics
        if lower.startswith("sqlite"):
            return "sqlite"
        if lower.startswith("postgresql") or lower.startswith("postgres"):
            return "postgresql"
        if lower.startswith("mysql"):
            return "mysql"
        return "unknown"


def _apply_having_op(col: Any, op: str, val: Any) -> Any:
    """Apply a comparison operator for HAVING clauses."""
    ops = {
        "$eq": lambda c, v: c == v,
        "$ne": lambda c, v: c != v,
        "$gt": lambda c, v: c > v,
        "$gte": lambda c, v: c >= v,
        "$lt": lambda c, v: c < v,
        "$lte": lambda c, v: c <= v,
    }
    if op not in ops:
        raise ActionExecutionError(
            module_id=MODULE_ID,
            action="aggregate",
            cause=RuntimeError(f"Unsupported HAVING operator: {op}"),
        )
    return ops[op](col, val)


# Auto-register as the default SQL adapter
AdapterRegistry.set_default_sql_adapter(SQLAlchemyAdapter)
