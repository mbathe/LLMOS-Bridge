"""Database Gateway — Base adapter ABCs and driver profile.

All database adapters (SQL and non-SQL) implement :class:`BaseDbAdapter`
(synchronous) or :class:`BaseAsyncDbAdapter` (natively async).

SQL drivers can be registered with a :class:`DriverProfile` — a lightweight
descriptor that lets ``SQLAlchemyAdapter`` handle all query logic automatically.

Community authors add new database support by either:

1. **SQL database** (~5 lines) — register a ``DriverProfile``::

       from llmos_bridge.modules.database_gateway import register_sql_driver
       register_sql_driver("oracle", dialect="oracle+cx_oracle", default_port=1521)

2. **Non-SQL backend (sync)** — subclass ``BaseDbAdapter``::

       from llmos_bridge.modules.database_gateway import BaseDbAdapter, register_adapter

       @register_adapter("mongodb")
       class MongoAdapter(BaseDbAdapter):
           supports_foreign_keys = False
           ...

3. **Non-SQL backend (async)** — subclass ``BaseAsyncDbAdapter``::

       from llmos_bridge.modules.database_gateway import BaseAsyncDbAdapter, register_adapter

       @register_adapter("aio_redis")
       class AsyncRedisAdapter(BaseAsyncDbAdapter):
           supports_schema_enforcement = False
           ...
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class DriverProfile:
    """Describes a SQL database driver for automatic SQLAlchemy integration.

    The ``dialect`` string is passed to ``sqlalchemy.create_engine()``.
    """

    name: str
    """Driver name used in ``connect(driver=...)``, e.g. ``"oracle"``."""

    dialect: str
    """SQLAlchemy dialect string, e.g. ``"oracle+cx_oracle"``."""

    default_port: int | None = None
    """Default TCP port (e.g. 1521 for Oracle, 5432 for PostgreSQL)."""

    engine_kwargs: dict[str, Any] = field(default_factory=dict)
    """Extra keyword arguments passed to ``sqlalchemy.create_engine()``."""

    post_connect_hook: Callable[..., None] | None = None
    """Optional hook called with the engine after connection (e.g. PRAGMAs)."""

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("DriverProfile.name must be non-empty")
        if not self.dialect:
            raise ValueError("DriverProfile.dialect must be non-empty")
        if self.default_port is not None and not (1 <= self.default_port <= 65535):
            raise ValueError(
                f"DriverProfile.default_port must be 1-65535, got {self.default_port}"
            )
        if self.post_connect_hook is not None and not callable(
            self.post_connect_hook
        ):
            raise TypeError("DriverProfile.post_connect_hook must be callable")


class BaseDbAdapter(ABC):
    """Abstract base class for **synchronous** database adapters.

    Each adapter manages its own connections and implements 12 operations
    matching the ``db_gateway`` IML actions. All methods are **synchronous** —
    the gateway module wraps them in ``asyncio.to_thread()``.

    Return-value contracts:

    - ``connect()``     → ``{connection_id, driver, database, tables, table_count, status}``
    - ``disconnect()``  → ``{connection_id, status}``
    - ``introspect()``  → ``{connection_id, cached, tables, table_count, schema}``
    - ``find()``        → ``{entity, rows, row_count, truncated, elapsed_ms, connection_id}``
    - ``find_one()``    → ``{entity, found, record, connection_id}``
    - ``create()``      → ``{entity, created, inserted_id, connection_id}``
    - ``create_many()`` → ``{entity, created, inserted_count, connection_id}``
    - ``update()``      → ``{entity, rows_affected, connection_id}``
    - ``delete()``      → ``{entity, deleted, rows_deleted, connection_id}``
    - ``count()``       → ``{entity, count, connection_id}``
    - ``aggregate()``   → ``{entity, rows, row_count, elapsed_ms, connection_id}``
    - ``search()``      → ``{entity, query, rows, row_count, elapsed_ms, connection_id}``
    """

    # ------------------------------------------------------------------
    # Capability flags — override in subclasses for non-SQL backends
    # ------------------------------------------------------------------

    supports_transactions: bool = True
    """Whether the backend supports multi-statement transactions."""

    supports_foreign_keys: bool = True
    """Whether the backend enforces foreign-key constraints."""

    supports_schema_enforcement: bool = True
    """Whether the backend has a fixed schema (False for document stores)."""

    supports_native_aggregation: bool = True
    """Whether the backend supports server-side GROUP BY / aggregation."""

    supports_native_search: bool = True
    """Whether the backend supports server-side text search."""

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    @abstractmethod
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
        """Open a connection identified by *connection_id*."""

    @abstractmethod
    def disconnect(self, connection_id: str) -> dict[str, Any]:
        """Close the connection identified by *connection_id*."""

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @abstractmethod
    def introspect(
        self,
        connection_id: str,
        *,
        schema_name: str | None = None,
        refresh: bool = False,
    ) -> dict[str, Any]:
        """Return full schema metadata for the connection."""

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    @abstractmethod
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
        """Find records matching *filter_dict*."""

    @abstractmethod
    def find_one(
        self,
        connection_id: str,
        entity: str,
        *,
        filter_dict: dict[str, Any] | None = None,
        select: list[str] | None = None,
    ) -> dict[str, Any]:
        """Find a single record matching *filter_dict*."""

    @abstractmethod
    def count(
        self,
        connection_id: str,
        entity: str,
        *,
        filter_dict: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Count records matching *filter_dict*."""

    @abstractmethod
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
        """Text search across *columns* for *query*."""

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    @abstractmethod
    def create(
        self,
        connection_id: str,
        entity: str,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        """Insert one record."""

    @abstractmethod
    def create_many(
        self,
        connection_id: str,
        entity: str,
        records: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Insert multiple records in a batch."""

    @abstractmethod
    def update(
        self,
        connection_id: str,
        entity: str,
        filter_dict: dict[str, Any],
        values: dict[str, Any],
    ) -> dict[str, Any]:
        """Update records matching *filter_dict* with *values*."""

    @abstractmethod
    def delete(
        self,
        connection_id: str,
        entity: str,
        filter_dict: dict[str, Any],
    ) -> dict[str, Any]:
        """Delete records matching *filter_dict*."""

    # ------------------------------------------------------------------
    # Aggregation
    # ------------------------------------------------------------------

    @abstractmethod
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
        """Aggregate records with GROUP BY and aggregate functions."""

    # ------------------------------------------------------------------
    # Shared concrete methods
    # ------------------------------------------------------------------

    @abstractmethod
    def list_connections(self) -> list[str]:
        """Return active connection IDs managed by this adapter."""

    @abstractmethod
    def close_all(self) -> None:
        """Dispose all connections. Called on module teardown."""

    def get_context_snippet(self, connection_id: str) -> str | None:
        """Return an LLM-friendly schema string for prompt injection.

        Default returns ``None``. Override in adapters that support
        schema introspection.
        """
        return None


class BaseAsyncDbAdapter(ABC):
    """Abstract base class for **natively async** database adapters.

    Use this when the underlying library (motor, aioredis, etc.) is
    natively async.  The gateway module will call methods directly with
    ``await`` instead of wrapping in ``asyncio.to_thread()``.

    Same return-value contracts as :class:`BaseDbAdapter`.
    """

    # ------------------------------------------------------------------
    # Capability flags — same as BaseDbAdapter
    # ------------------------------------------------------------------

    supports_transactions: bool = True
    supports_foreign_keys: bool = True
    supports_schema_enforcement: bool = True
    supports_native_aggregation: bool = True
    supports_native_search: bool = True

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    @abstractmethod
    async def connect(
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
        """Open a connection identified by *connection_id*."""

    @abstractmethod
    async def disconnect(self, connection_id: str) -> dict[str, Any]:
        """Close the connection identified by *connection_id*."""

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @abstractmethod
    async def introspect(
        self,
        connection_id: str,
        *,
        schema_name: str | None = None,
        refresh: bool = False,
    ) -> dict[str, Any]:
        """Return full schema metadata for the connection."""

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    @abstractmethod
    async def find(
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
        """Find records matching *filter_dict*."""

    @abstractmethod
    async def find_one(
        self,
        connection_id: str,
        entity: str,
        *,
        filter_dict: dict[str, Any] | None = None,
        select: list[str] | None = None,
    ) -> dict[str, Any]:
        """Find a single record matching *filter_dict*."""

    @abstractmethod
    async def count(
        self,
        connection_id: str,
        entity: str,
        *,
        filter_dict: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Count records matching *filter_dict*."""

    @abstractmethod
    async def search(
        self,
        connection_id: str,
        entity: str,
        *,
        query: str,
        columns: list[str],
        case_sensitive: bool = False,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Text search across *columns* for *query*."""

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    @abstractmethod
    async def create(
        self,
        connection_id: str,
        entity: str,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        """Insert one record."""

    @abstractmethod
    async def create_many(
        self,
        connection_id: str,
        entity: str,
        records: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Insert multiple records in a batch."""

    @abstractmethod
    async def update(
        self,
        connection_id: str,
        entity: str,
        filter_dict: dict[str, Any],
        values: dict[str, Any],
    ) -> dict[str, Any]:
        """Update records matching *filter_dict* with *values*."""

    @abstractmethod
    async def delete(
        self,
        connection_id: str,
        entity: str,
        filter_dict: dict[str, Any],
    ) -> dict[str, Any]:
        """Delete records matching *filter_dict*."""

    # ------------------------------------------------------------------
    # Aggregation
    # ------------------------------------------------------------------

    @abstractmethod
    async def aggregate(
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
        """Aggregate records with GROUP BY and aggregate functions."""

    # ------------------------------------------------------------------
    # Shared concrete methods
    # ------------------------------------------------------------------

    @abstractmethod
    async def list_connections(self) -> list[str]:
        """Return active connection IDs managed by this adapter."""

    @abstractmethod
    async def close_all(self) -> None:
        """Dispose all connections. Called on module teardown."""

    async def get_context_snippet(self, connection_id: str) -> str | None:
        """Return an LLM-friendly schema string for prompt injection (async).

        Default returns ``None``. Override in adapters that support
        schema introspection.
        """
        return None

    def get_context_snippet_sync(self, connection_id: str) -> str | None:
        """Return a cached schema string for synchronous prompt injection.

        The gateway module calls this (not the async version) when building
        the LLM system prompt, because prompt generation is synchronous.
        Override this to return a cached schema string.

        Default returns ``None``.
        """
        return None
