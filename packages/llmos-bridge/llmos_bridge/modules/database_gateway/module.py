"""Database Gateway module — Semantic, SQL-free database operations.

The LLM interacts with databases through entity names and MongoDB-like
filter syntax. No SQL is ever written by the LLM.

This module is a **thin dispatcher** — it validates parameters, resolves
the correct adapter for each connection, and delegates all database
operations to :class:`BaseDbAdapter` or :class:`BaseAsyncDbAdapter`
implementations.

Built-in support: SQLite, PostgreSQL, MySQL (via SQLAlchemy).
Extensible: add any SQL database in ~5 lines, or non-SQL backends via ABC.
Pip-installable adapter plugins are auto-discovered via entry points.
"""

from __future__ import annotations

import asyncio
from typing import Any, Union

from llmos_bridge.exceptions import ActionExecutionError
from llmos_bridge.modules.base import BaseModule, Platform
from llmos_bridge.security.decorators import (
    audit_trail,
    rate_limited,
    requires_permission,
    sensitive_action,
)
from llmos_bridge.security.models import Permission, RiskLevel
from llmos_bridge.modules.database_gateway.base_adapter import (
    BaseAsyncDbAdapter,
    BaseDbAdapter,
)
from llmos_bridge.modules.database_gateway.registry import (
    AdapterRegistry,
    discover_adapters,
)
from llmos_bridge.modules.manifest import ActionSpec, ModuleManifest, ParamSpec
from llmos_bridge.protocol.params.database_gateway import (
    AggregateParams,
    CountParams,
    CreateManyParams,
    CreateParams,
    DeleteParams,
    FindOneParams,
    FindParams,
    GatewayConnectParams,
    GatewayDisconnectParams,
    IntrospectParams,
    SearchParams,
    UpdateParams,
)

AnyAdapter = Union[BaseDbAdapter, BaseAsyncDbAdapter]


class DatabaseGatewayModule(BaseModule):
    """Semantic database gateway — 12 IML actions, zero SQL.

    Routes each connection to the appropriate :class:`BaseDbAdapter` based
    on the ``driver`` parameter. All SQL databases share a single
    ``SQLAlchemyAdapter``; non-SQL backends get their own adapter instances.
    """

    MODULE_ID = "db_gateway"
    VERSION = "1.1.0"
    SUPPORTED_PLATFORMS = [Platform.ALL]

    def __init__(
        self,
        max_connections: int = 10,
        schema_cache_ttl: int = 300,
    ) -> None:
        self._max_connections = max_connections
        self._schema_cache_ttl = schema_cache_ttl
        # connection_id → adapter instance
        self._connection_adapters: dict[str, AnyAdapter] = {}
        # driver name → adapter instance (reuse across connections)
        self._adapter_instances: dict[str, AnyAdapter] = {}
        # Auto-discover pip-installed adapter plugins
        discover_adapters()
        super().__init__()

    def _check_dependencies(self) -> None:
        # SQLAlchemy is required for the built-in SQL adapter.
        # Non-SQL-only deployments could skip this in the future.
        try:
            import sqlalchemy  # noqa: F401
        except ImportError as exc:
            from llmos_bridge.exceptions import ModuleLoadError

            raise ModuleLoadError(
                module_id=self.MODULE_ID,
                reason="sqlalchemy is required: pip install 'sqlalchemy>=2.0'",
            ) from exc

    # ------------------------------------------------------------------
    # Adapter resolution
    # ------------------------------------------------------------------

    def _resolve_driver(self, params: GatewayConnectParams) -> str:
        """Determine driver name from URL or explicit driver param."""
        if params.url:
            return self._detect_driver_from_url(params.url)
        return params.driver

    @staticmethod
    def _detect_driver_from_url(url: str) -> str:
        """Detect driver name from a SQLAlchemy URL string."""
        lower = url.lower()
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

    def _get_or_create_adapter(self, driver: str) -> AnyAdapter:
        """Get existing adapter instance or create one for *driver*."""
        if driver not in self._adapter_instances:
            adapter_cls = AdapterRegistry.get_adapter_class(driver)
            # SQL adapters accept max_connections and cache config
            if AdapterRegistry.is_sql_driver(driver):
                self._adapter_instances[driver] = adapter_cls(
                    max_connections=self._max_connections,
                    schema_cache_ttl=self._schema_cache_ttl,
                )
            else:
                self._adapter_instances[driver] = adapter_cls()
        return self._adapter_instances[driver]

    def _get_adapter_for_connection(
        self, connection_id: str
    ) -> AnyAdapter:
        """Look up the adapter managing *connection_id*."""
        adapter = self._connection_adapters.get(connection_id)
        if adapter is None:
            raise ActionExecutionError(
                module_id=self.MODULE_ID,
                action="",
                cause=RuntimeError(
                    f"No active connection with id '{connection_id}'. "
                    "Use the 'connect' action first."
                ),
            )
        return adapter

    # ------------------------------------------------------------------
    # Sync / async adapter dispatch
    # ------------------------------------------------------------------

    async def _call_adapter(
        self, adapter: AnyAdapter, method: str, *args: Any, **kwargs: Any
    ) -> Any:
        """Call adapter method — async directly or sync via to_thread."""
        func = getattr(adapter, method)
        if isinstance(adapter, BaseAsyncDbAdapter):
            return await func(*args, **kwargs)
        return await asyncio.to_thread(func, *args, **kwargs)

    # ------------------------------------------------------------------
    # Context snippet for LLM prompt injection
    # ------------------------------------------------------------------

    def get_context_snippet(self) -> str | None:
        """Return current database schemas for all active connections.

        For sync adapters, calls ``get_context_snippet(conn_id)`` directly.
        For async adapters, calls the synchronous ``get_context_snippet_sync()``
        method, which adapters should override to return a cached schema string.
        """
        snippets: list[str] = []
        for conn_id, adapter in self._connection_adapters.items():
            if isinstance(adapter, BaseAsyncDbAdapter):
                # Try the sync cache accessor for async adapters
                if hasattr(adapter, "get_context_snippet_sync"):
                    snippet = adapter.get_context_snippet_sync(conn_id)
                else:
                    snippet = None
            else:
                snippet = adapter.get_context_snippet(conn_id)
            if snippet:
                snippets.append(snippet)
        if not snippets:
            return None
        return "## Database Context\n\n" + "\n\n".join(snippets)

    # ------------------------------------------------------------------
    # Actions — Connection management
    # ------------------------------------------------------------------

    @requires_permission(Permission.DATABASE_READ, reason="Connect to database")
    async def _action_connect(self, params: dict[str, Any]) -> dict[str, Any]:
        p = GatewayConnectParams.model_validate(params)
        driver = self._resolve_driver(p)
        adapter = self._get_or_create_adapter(driver)
        result = await self._call_adapter(
            adapter, "connect",
            p.connection_id,
            url=p.url,
            driver=driver,
            host=p.host,
            port=p.port,
            database=p.database,
            user=p.user,
            password=p.password,
            pool_size=p.pool_size,
        )
        self._connection_adapters[p.connection_id] = adapter
        return result

    async def _action_disconnect(self, params: dict[str, Any]) -> dict[str, Any]:
        p = GatewayDisconnectParams.model_validate(params)
        adapter = self._connection_adapters.pop(p.connection_id, None)
        if adapter is None:
            return {
                "connection_id": p.connection_id,
                "status": "not_connected",
            }
        return await self._call_adapter(adapter, "disconnect", p.connection_id)

    # ------------------------------------------------------------------
    # Actions — Introspection
    # ------------------------------------------------------------------

    @requires_permission(Permission.DATABASE_READ, reason="Introspect database schema")
    async def _action_introspect(self, params: dict[str, Any]) -> dict[str, Any]:
        p = IntrospectParams.model_validate(params)
        adapter = self._get_adapter_for_connection(p.connection_id)
        return await self._call_adapter(
            adapter, "introspect",
            p.connection_id,
            schema_name=p.schema_name,
            refresh=p.refresh,
        )

    # ------------------------------------------------------------------
    # Actions — Read
    # ------------------------------------------------------------------

    @requires_permission(Permission.DATABASE_READ, reason="Query database records")
    async def _action_find(self, params: dict[str, Any]) -> dict[str, Any]:
        p = FindParams.model_validate(params)
        adapter = self._get_adapter_for_connection(p.connection_id)
        return await self._call_adapter(
            adapter, "find",
            p.connection_id, p.entity,
            filter_dict=p.filter, select=p.select,
            order_by=p.order_by, limit=p.limit, offset=p.offset,
        )

    @requires_permission(Permission.DATABASE_READ, reason="Query single record")
    async def _action_find_one(self, params: dict[str, Any]) -> dict[str, Any]:
        p = FindOneParams.model_validate(params)
        adapter = self._get_adapter_for_connection(p.connection_id)
        return await self._call_adapter(
            adapter, "find_one",
            p.connection_id, p.entity,
            filter_dict=p.filter, select=p.select,
        )

    @requires_permission(Permission.DATABASE_READ, reason="Count records")
    async def _action_count(self, params: dict[str, Any]) -> dict[str, Any]:
        p = CountParams.model_validate(params)
        adapter = self._get_adapter_for_connection(p.connection_id)
        return await self._call_adapter(
            adapter, "count",
            p.connection_id, p.entity,
            filter_dict=p.filter,
        )

    @requires_permission(Permission.DATABASE_READ, reason="Full-text search")
    async def _action_search(self, params: dict[str, Any]) -> dict[str, Any]:
        p = SearchParams.model_validate(params)
        adapter = self._get_adapter_for_connection(p.connection_id)
        return await self._call_adapter(
            adapter, "search",
            p.connection_id, p.entity,
            query=p.query, columns=p.columns,
            case_sensitive=p.case_sensitive, limit=p.limit,
        )

    # ------------------------------------------------------------------
    # Actions — Write
    # ------------------------------------------------------------------

    @requires_permission(Permission.DATABASE_WRITE, reason="Create database record")
    @rate_limited(calls_per_minute=60)
    @audit_trail("standard")
    async def _action_create(self, params: dict[str, Any]) -> dict[str, Any]:
        p = CreateParams.model_validate(params)
        adapter = self._get_adapter_for_connection(p.connection_id)
        return await self._call_adapter(
            adapter, "create", p.connection_id, p.entity, p.data,
        )

    @requires_permission(Permission.DATABASE_WRITE, reason="Bulk create records")
    @rate_limited(calls_per_minute=60)
    @audit_trail("standard")
    async def _action_create_many(self, params: dict[str, Any]) -> dict[str, Any]:
        p = CreateManyParams.model_validate(params)
        adapter = self._get_adapter_for_connection(p.connection_id)
        return await self._call_adapter(
            adapter, "create_many", p.connection_id, p.entity, p.records,
        )

    @requires_permission(Permission.DATABASE_WRITE, reason="Update database records")
    @rate_limited(calls_per_minute=60)
    @audit_trail("standard")
    async def _action_update(self, params: dict[str, Any]) -> dict[str, Any]:
        p = UpdateParams.model_validate(params)
        adapter = self._get_adapter_for_connection(p.connection_id)
        return await self._call_adapter(
            adapter, "update", p.connection_id, p.entity, p.filter, p.values,
        )

    @requires_permission(Permission.DATABASE_DELETE, reason="Delete database records")
    @sensitive_action(RiskLevel.HIGH, irreversible=True)
    @rate_limited(calls_per_minute=60)
    @audit_trail("detailed")
    async def _action_delete(self, params: dict[str, Any]) -> dict[str, Any]:
        p = DeleteParams.model_validate(params)

        if not p.confirm:
            return {
                "entity": p.entity,
                "deleted": False,
                "reason": "confirm must be True to execute a DELETE.",
                "connection_id": p.connection_id,
            }

        adapter = self._get_adapter_for_connection(p.connection_id)
        return await self._call_adapter(
            adapter, "delete", p.connection_id, p.entity, p.filter,
        )

    # ------------------------------------------------------------------
    # Actions — Aggregate
    # ------------------------------------------------------------------

    @requires_permission(Permission.DATABASE_READ, reason="Aggregate query")
    async def _action_aggregate(self, params: dict[str, Any]) -> dict[str, Any]:
        p = AggregateParams.model_validate(params)
        adapter = self._get_adapter_for_connection(p.connection_id)
        return await self._call_adapter(
            adapter, "aggregate",
            p.connection_id, p.entity,
            group_by=p.group_by, aggregations=p.aggregations,
            filter_dict=p.filter, having=p.having,
            order_by=p.order_by, limit=p.limit,
        )

    # ------------------------------------------------------------------
    # Manifest
    # ------------------------------------------------------------------

    def get_manifest(self) -> ModuleManifest:
        return ModuleManifest(
            module_id=self.MODULE_ID,
            version=self.VERSION,
            description=(
                "Semantic database gateway — query databases using entity names "
                "and MongoDB-like filters instead of raw SQL. "
                "Extensible: supports any SQLAlchemy-compatible database, plus "
                "custom adapters for non-SQL backends (MongoDB, Redis, etc.)."
            ),
            platforms=["all"],
            declared_permissions=["database_access"],
            tags=["database", "gateway", "sql", "sqlite", "postgresql", "mysql", "orm"],
            actions=[
                ActionSpec(
                    name="connect",
                    description="Open a database connection.",
                    params=[
                        ParamSpec(name="url", type="string", description="Full connection URL.", required=False),
                        ParamSpec(name="driver", type="string", description="Database driver. Built-in: sqlite, postgresql, mysql. Extensible via register_sql_driver().", required=False, default="sqlite"),
                        ParamSpec(name="database", type="string", description="Database name or file path (for SQLite).", required=False, default=""),
                        ParamSpec(name="host", type="string", description="Database host.", required=False, default="localhost"),
                        ParamSpec(name="port", type="integer", description="Database port.", required=False),
                        ParamSpec(name="user", type="string", description="Database user.", required=False),
                        ParamSpec(name="password", type="string", description="Database password.", required=False),
                        ParamSpec(name="connection_id", type="string", description="Logical connection name.", required=False, default="default"),
                        ParamSpec(name="pool_size", type="integer", description="Connection pool size.", required=False, default=5),
                    ],
                    returns="object",
                    returns_description='{"connection_id": str, "driver": str, "database": str, "tables": [str], "table_count": int, "status": "connected"}',
                    permission_required="local_worker",
                    examples=[{
                        "description": "Connect to SQLite",
                        "params": {"driver": "sqlite", "database": "/tmp/myapp.db"},
                    }],
                ),
                ActionSpec(
                    name="disconnect",
                    description="Close a database connection.",
                    params=[
                        ParamSpec(name="connection_id", type="string", description="Connection to close.", required=False, default="default"),
                    ],
                    returns="object",
                    returns_description='{"connection_id": str, "status": "disconnected"}',
                    permission_required="local_worker",
                ),
                ActionSpec(
                    name="introspect",
                    description="Get full schema: tables, columns, types, foreign keys, indexes.",
                    params=[
                        ParamSpec(name="connection_id", type="string", description="Connection to inspect.", required=False, default="default"),
                        ParamSpec(name="schema_name", type="string", description="Schema name (PostgreSQL only).", required=False),
                        ParamSpec(name="refresh", type="boolean", description="Force re-introspection.", required=False, default=False),
                    ],
                    returns="object",
                    returns_description='{"connection_id": str, "cached": bool, "tables": [{name, columns, primary_key, foreign_keys, indexes}], "table_count": int, "schema": str}',
                    permission_required="readonly",
                ),
                ActionSpec(
                    name="find",
                    description="Find records matching a filter, with projection, ordering, and pagination.",
                    params=[
                        ParamSpec(name="entity", type="string", description="Table/entity name."),
                        ParamSpec(name="filter", type="object", description='MongoDB-like filter. Example: {"age": {"$gte": 18}, "status": "active"}', required=False, default={}),
                        ParamSpec(name="select", type="array", description="Columns to return (null = all).", required=False),
                        ParamSpec(name="order_by", type="array", description='Sort order. Prefix "-" for descending.', required=False),
                        ParamSpec(name="limit", type="integer", description="Max rows to return.", required=False, default=100),
                        ParamSpec(name="offset", type="integer", description="Skip first N rows.", required=False, default=0),
                        ParamSpec(name="connection_id", type="string", description="Connection to use.", required=False, default="default"),
                    ],
                    returns="object",
                    returns_description='{"entity": str, "rows": [dict], "row_count": int, "truncated": bool, "elapsed_ms": float, "connection_id": str}. If truncated=true, more rows exist — use offset for pagination.',
                    permission_required="readonly",
                    examples=[{
                        "description": "Find active adults",
                        "params": {"entity": "users", "filter": {"age": {"$gte": 18}, "status": "active"}, "order_by": ["name"], "limit": 50},
                    }],
                ),
                ActionSpec(
                    name="find_one",
                    description="Find a single record matching a filter.",
                    params=[
                        ParamSpec(name="entity", type="string", description="Table/entity name."),
                        ParamSpec(name="filter", type="object", description="MongoDB-like filter.", required=False, default={}),
                        ParamSpec(name="select", type="array", description="Columns to return.", required=False),
                        ParamSpec(name="connection_id", type="string", description="Connection to use.", required=False, default="default"),
                    ],
                    returns="object",
                    returns_description='{"entity": str, "found": bool, "record": dict|null, "connection_id": str}',
                    permission_required="readonly",
                ),
                ActionSpec(
                    name="create",
                    description="Create a new record in an entity.",
                    params=[
                        ParamSpec(name="entity", type="string", description="Table/entity name."),
                        ParamSpec(name="data", type="object", description="Column-value mapping."),
                        ParamSpec(name="connection_id", type="string", description="Connection to use.", required=False, default="default"),
                    ],
                    returns="object",
                    returns_description='{"entity": str, "created": true, "inserted_id": int|str, "connection_id": str}',
                    permission_required="local_worker",
                    examples=[{
                        "description": "Create a user",
                        "params": {"entity": "users", "data": {"name": "Alice", "email": "alice@example.com", "age": 30}},
                    }],
                ),
                ActionSpec(
                    name="create_many",
                    description="Create multiple records in a single batch.",
                    params=[
                        ParamSpec(name="entity", type="string", description="Table/entity name."),
                        ParamSpec(name="records", type="array", description="List of column-value mappings."),
                        ParamSpec(name="connection_id", type="string", description="Connection to use.", required=False, default="default"),
                    ],
                    returns="object",
                    returns_description='{"entity": str, "created": true, "inserted_count": int, "connection_id": str}',
                    permission_required="local_worker",
                ),
                ActionSpec(
                    name="update",
                    description="Update records matching a filter.",
                    params=[
                        ParamSpec(name="entity", type="string", description="Table/entity name."),
                        ParamSpec(name="filter", type="object", description="MongoDB-like filter to select records."),
                        ParamSpec(name="values", type="object", description="Column-value mapping with new values."),
                        ParamSpec(name="connection_id", type="string", description="Connection to use.", required=False, default="default"),
                    ],
                    returns="object",
                    returns_description='{"entity": str, "rows_affected": int, "connection_id": str}',
                    permission_required="local_worker",
                ),
                ActionSpec(
                    name="delete",
                    description="Delete records matching a filter. Requires confirm=true.",
                    params=[
                        ParamSpec(name="entity", type="string", description="Table/entity name."),
                        ParamSpec(name="filter", type="object", description="MongoDB-like filter to select records."),
                        ParamSpec(name="confirm", type="boolean", description="Safety flag — must be true.", required=True),
                        ParamSpec(name="connection_id", type="string", description="Connection to use.", required=False, default="default"),
                    ],
                    returns="object",
                    returns_description='{"entity": str, "deleted": true, "rows_deleted": int, "connection_id": str}',
                    permission_required="power_user",
                ),
                ActionSpec(
                    name="count",
                    description="Count records matching a filter.",
                    params=[
                        ParamSpec(name="entity", type="string", description="Table/entity name."),
                        ParamSpec(name="filter", type="object", description="MongoDB-like filter. Empty = count all.", required=False, default={}),
                        ParamSpec(name="connection_id", type="string", description="Connection to use.", required=False, default="default"),
                    ],
                    returns="object",
                    returns_description='{"entity": str, "count": int, "connection_id": str}',
                    permission_required="readonly",
                ),
                ActionSpec(
                    name="aggregate",
                    description="Aggregate records with GROUP BY and aggregate functions (sum, avg, min, max, count).",
                    params=[
                        ParamSpec(name="entity", type="string", description="Table/entity name."),
                        ParamSpec(name="group_by", type="array", description="Columns to group by."),
                        ParamSpec(name="aggregations", type="object", description='Column → function mapping, e.g. {"salary": "avg", "id": "count"}.'),
                        ParamSpec(name="filter", type="object", description="Pre-aggregation WHERE filter.", required=False, default={}),
                        ParamSpec(name="having", type="object", description='Post-aggregation HAVING filter on aliases. E.g. {"count_id": {"$gte": 5}}', required=False, default={}),
                        ParamSpec(name="order_by", type="array", description="Sort order.", required=False),
                        ParamSpec(name="limit", type="integer", description="Max rows.", required=False, default=1000),
                        ParamSpec(name="connection_id", type="string", description="Connection to use.", required=False, default="default"),
                    ],
                    returns="object",
                    returns_description='{"entity": str, "rows": [dict], "row_count": int, "elapsed_ms": float, "connection_id": str}. Aggregated column aliases: {func}_{col} (e.g. avg_salary, count_id).',
                    permission_required="readonly",
                    examples=[{
                        "description": "Average salary by department",
                        "params": {
                            "entity": "employees",
                            "group_by": ["department"],
                            "aggregations": {"salary": "avg", "id": "count"},
                            "having": {"count_id": {"$gte": 5}},
                        },
                    }],
                ),
                ActionSpec(
                    name="search",
                    description="Full-text search across specified columns using LIKE/ILIKE.",
                    params=[
                        ParamSpec(name="entity", type="string", description="Table/entity name."),
                        ParamSpec(name="query", type="string", description="Text to search for."),
                        ParamSpec(name="columns", type="array", description="Columns to search across."),
                        ParamSpec(name="case_sensitive", type="boolean", description="Case-sensitive search.", required=False, default=False),
                        ParamSpec(name="limit", type="integer", description="Max results.", required=False, default=100),
                        ParamSpec(name="connection_id", type="string", description="Connection to use.", required=False, default="default"),
                    ],
                    returns="object",
                    returns_description='{"entity": str, "query": str, "rows": [dict], "row_count": int, "elapsed_ms": float, "connection_id": str}',
                    permission_required="readonly",
                ),
            ],
        )
