"""Database Gateway — SQLAlchemy adapter layer.

Manages SQLAlchemy engines, connections, and MetaData objects per connection_id.
Thread-safe; all blocking calls are expected to be wrapped in ``asyncio.to_thread()``.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy import MetaData

from llmos_bridge.exceptions import ActionExecutionError

MODULE_ID = "db_gateway"


@dataclass
class ConnectionEntry:
    """Internal state for one logical connection."""

    engine: sa.Engine
    metadata: MetaData
    url: str
    driver_name: str  # "sqlite", "postgresql", "mysql"
    database: str
    lock: threading.Lock = field(default_factory=threading.Lock)


class AdapterManager:
    """Manages named SQLAlchemy connections.

    Each connection is identified by a ``connection_id`` string. Callers must
    wrap blocking methods in ``asyncio.to_thread()`` as all SQLAlchemy I/O
    here is synchronous.
    """

    def __init__(self, max_connections: int = 10) -> None:
        self._connections: dict[str, ConnectionEntry] = {}
        self._meta_lock = threading.Lock()
        self._max_connections = max_connections

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def connect(
        self,
        connection_id: str,
        url: str | None = None,
        *,
        driver: str = "sqlite",
        host: str = "localhost",
        port: int | None = None,
        database: str = "",
        user: str | None = None,
        password: str | None = None,
        pool_size: int = 5,
        auto_introspect: bool = True,
    ) -> dict[str, Any]:
        """Create a SQLAlchemy engine for *connection_id*.

        If *url* is provided, use it directly. Otherwise build from parts.
        """
        with self._meta_lock:
            # Close existing if any
            if connection_id in self._connections:
                self._dispose_entry(connection_id)

            if len(self._connections) >= self._max_connections:
                raise ActionExecutionError(
                    module_id=MODULE_ID,
                    action="connect",
                    cause=RuntimeError(
                        f"Maximum connections ({self._max_connections}) reached. "
                        "Disconnect an existing connection first."
                    ),
                )

        if url is None:
            url = self._build_url(driver, host, port, database, user, password)
            driver_name = driver
        else:
            driver_name = self._detect_driver(url)

        engine_kwargs: dict[str, Any] = {}
        if driver_name == "sqlite":
            # SQLite needs special pool for thread safety
            from sqlalchemy.pool import StaticPool

            engine_kwargs["poolclass"] = StaticPool
            engine_kwargs["connect_args"] = {"check_same_thread": False}
        else:
            engine_kwargs["pool_size"] = pool_size
            engine_kwargs["max_overflow"] = pool_size

        engine = sa.create_engine(url, **engine_kwargs)

        # Enable WAL + FK for SQLite
        if driver_name == "sqlite":
            with engine.connect() as conn:
                conn.execute(sa.text("PRAGMA journal_mode=WAL"))
                conn.execute(sa.text("PRAGMA foreign_keys=ON"))
                conn.commit()

        metadata = MetaData()
        if auto_introspect:
            metadata.reflect(bind=engine)

        entry = ConnectionEntry(
            engine=engine,
            metadata=metadata,
            url=url,
            driver_name=driver_name,
            database=database or url,
        )

        with self._meta_lock:
            self._connections[connection_id] = entry

        return {
            "connection_id": connection_id,
            "driver": driver_name,
            "database": database or url,
            "tables": [t.name for t in metadata.sorted_tables],
            "table_count": len(metadata.tables),
            "status": "connected",
        }

    def disconnect(self, connection_id: str) -> dict[str, Any]:
        """Dispose engine and remove entry."""
        with self._meta_lock:
            if connection_id not in self._connections:
                return {
                    "connection_id": connection_id,
                    "status": "not_connected",
                }
            self._dispose_entry(connection_id)

        return {
            "connection_id": connection_id,
            "status": "disconnected",
        }

    def _dispose_entry(self, connection_id: str) -> None:
        """Dispose engine, remove entry. Must be called under ``_meta_lock``."""
        entry = self._connections.pop(connection_id, None)
        if entry is not None:
            try:
                entry.engine.dispose()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def get_entry(self, connection_id: str) -> ConnectionEntry:
        """Return the entry for *connection_id* or raise."""
        entry = self._connections.get(connection_id)
        if entry is None:
            raise ActionExecutionError(
                module_id=MODULE_ID,
                action="",
                cause=RuntimeError(
                    f"No active connection with id '{connection_id}'. "
                    "Use the 'connect' action first."
                ),
            )
        return entry

    def get_table(self, connection_id: str, table_name: str) -> sa.Table:
        """Return a reflected ``Table`` object, raising on unknown names."""
        entry = self.get_entry(connection_id)
        if table_name not in entry.metadata.tables:
            # Try refreshing metadata in case the table was created after connect
            try:
                entry.metadata.reflect(bind=entry.engine, only=[table_name])
            except sa.exc.InvalidRequestError:
                pass  # Table genuinely does not exist
        if table_name not in entry.metadata.tables:
            available = list(entry.metadata.tables.keys())
            raise ActionExecutionError(
                module_id=MODULE_ID,
                action="",
                cause=RuntimeError(
                    f"Unknown entity '{table_name}' in connection '{connection_id}'. "
                    f"Available entities: {available}"
                ),
            )
        return entry.metadata.tables[table_name]

    def refresh_metadata(self, connection_id: str) -> None:
        """Re-reflect all tables (used after schema changes)."""
        entry = self.get_entry(connection_id)
        entry.metadata.clear()
        entry.metadata.reflect(bind=entry.engine)

    def list_connections(self) -> list[str]:
        """Return active connection IDs."""
        return list(self._connections.keys())

    def close_all(self) -> None:
        """Dispose all engines. Called on module teardown."""
        with self._meta_lock:
            for conn_id in list(self._connections.keys()):
                self._dispose_entry(conn_id)

    # ------------------------------------------------------------------
    # URL building
    # ------------------------------------------------------------------

    @staticmethod
    def _build_url(
        driver: str,
        host: str,
        port: int | None,
        database: str,
        user: str | None,
        password: str | None,
    ) -> str:
        """Build a SQLAlchemy URL from individual parameters."""
        if driver == "sqlite":
            if database == ":memory:":
                return "sqlite:///:memory:"
            path = Path(database).resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            return f"sqlite:///{path}"

        if driver == "postgresql":
            dialect = "postgresql+psycopg2"
            default_port = 5432
        elif driver == "mysql":
            dialect = "mysql+mysqlconnector"
            default_port = 3306
        else:
            raise ActionExecutionError(
                module_id=MODULE_ID,
                action="connect",
                cause=ValueError(f"Unsupported driver: {driver}"),
            )

        effective_port = port or default_port
        auth = ""
        if user:
            auth = user
            if password:
                auth += f":{password}"
            auth += "@"

        return f"{dialect}://{auth}{host}:{effective_port}/{database}"

    @staticmethod
    def _detect_driver(url: str) -> str:
        """Detect the driver name from a SQLAlchemy URL string."""
        lower = url.lower()
        if lower.startswith("sqlite"):
            return "sqlite"
        if lower.startswith("postgresql") or lower.startswith("postgres"):
            return "postgresql"
        if lower.startswith("mysql"):
            return "mysql"
        return "unknown"
