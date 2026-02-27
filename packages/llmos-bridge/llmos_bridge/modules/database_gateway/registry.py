"""Database Gateway — Adapter registry.

Central registry for database adapters and SQL driver profiles.
Provides decorator-based and function-based registration APIs,
with validation to catch errors early at registration time.

Built-in SQL drivers (sqlite, postgresql, mysql) are registered at the
bottom of this file and are always available.

Usage::

    # Register a new SQL driver (5 lines):
    from llmos_bridge.modules.database_gateway.registry import register_sql_driver
    register_sql_driver("oracle", dialect="oracle+cx_oracle", default_port=1521)

    # Register a non-SQL adapter (decorator):
    from llmos_bridge.modules.database_gateway.registry import register_adapter

    @register_adapter("mongodb")
    class MongoAdapter(BaseDbAdapter):
        ...

    # Auto-discover pip-installed adapter plugins:
    from llmos_bridge.modules.database_gateway.registry import discover_adapters
    discover_adapters()  # scans entry_points group "llmos_bridge.db_adapters"
"""

from __future__ import annotations

import logging
from typing import Any, Callable, overload

from llmos_bridge.modules.database_gateway.base_adapter import (
    BaseAsyncDbAdapter,
    BaseDbAdapter,
    DriverProfile,
)

logger = logging.getLogger("llmos_bridge.db_gateway")

# Union type for both sync and async adapters
AnyAdapterClass = type[BaseDbAdapter] | type[BaseAsyncDbAdapter]


class AdapterRegistry:
    """Singleton registry for database adapter classes and SQL driver profiles."""

    _adapters: dict[str, AnyAdapterClass] = {}
    _sql_drivers: dict[str, DriverProfile] = {}
    _default_sql_adapter_class: type[BaseDbAdapter] | None = None

    # ------------------------------------------------------------------
    # Non-SQL adapter registration (with validation)
    # ------------------------------------------------------------------

    @classmethod
    def register_adapter(
        cls, name: str, adapter_class: AnyAdapterClass
    ) -> None:
        """Register a non-SQL adapter class by driver name.

        Raises:
            ValueError: If *name* is empty.
            TypeError: If *adapter_class* is not a subclass of
                ``BaseDbAdapter`` or ``BaseAsyncDbAdapter``.
        """
        if not name or not isinstance(name, str):
            raise ValueError("Adapter name must be a non-empty string.")
        if not isinstance(adapter_class, type) or not (
            issubclass(adapter_class, BaseDbAdapter)
            or issubclass(adapter_class, BaseAsyncDbAdapter)
        ):
            raise TypeError(
                f"Adapter class must be a subclass of BaseDbAdapter or "
                f"BaseAsyncDbAdapter, got {adapter_class!r}"
            )
        if name in cls._adapters:
            logger.warning(
                "Overwriting existing adapter registration for '%s'", name
            )
        cls._adapters[name] = adapter_class

    # ------------------------------------------------------------------
    # SQL driver registration (with validation)
    # ------------------------------------------------------------------

    @classmethod
    def register_sql_driver(
        cls,
        name: str,
        *,
        dialect: str,
        default_port: int | None = None,
        engine_kwargs: dict[str, Any] | None = None,
        post_connect_hook: Callable[..., None] | None = None,
    ) -> None:
        """Register a SQL driver profile.

        The ``SQLAlchemyAdapter`` handles all query building automatically
        for any driver registered here.

        Raises:
            ValueError: If *name* or *dialect* are empty, or port is out of range.
            TypeError: If *post_connect_hook* is not callable.
        """
        if name in cls._sql_drivers:
            logger.warning(
                "Overwriting existing SQL driver registration for '%s'", name
            )
        # DriverProfile.__post_init__ handles field-level validation
        cls._sql_drivers[name] = DriverProfile(
            name=name,
            dialect=dialect,
            default_port=default_port,
            engine_kwargs=engine_kwargs or {},
            post_connect_hook=post_connect_hook,
        )

    @classmethod
    def set_default_sql_adapter(cls, adapter_class: type[BaseDbAdapter]) -> None:
        """Set the adapter class used for all SQL driver profiles."""
        cls._default_sql_adapter_class = adapter_class

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    @classmethod
    def get_adapter_class(cls, driver: str) -> AnyAdapterClass:
        """Return the adapter class for *driver*.

        Lookup order:
        1. Explicit non-SQL adapter registration (``_adapters``)
        2. SQL driver profile (``_sql_drivers``) → returns the default SQL adapter
        3. Raises ``ValueError``
        """
        if driver in cls._adapters:
            return cls._adapters[driver]
        if driver in cls._sql_drivers:
            if cls._default_sql_adapter_class is None:
                raise RuntimeError(
                    f"SQL driver '{driver}' is registered but no default SQL "
                    "adapter has been set. Import sql_adapter to fix this."
                )
            return cls._default_sql_adapter_class
        available = cls.list_drivers()
        raise ValueError(
            f"Unknown database driver '{driver}'. "
            f"Available drivers: {available}"
        )

    @classmethod
    def get_sql_driver_profile(cls, driver: str) -> DriverProfile | None:
        """Return the ``DriverProfile`` for *driver*, or ``None``."""
        return cls._sql_drivers.get(driver)

    @classmethod
    def is_sql_driver(cls, driver: str) -> bool:
        """Return ``True`` if *driver* is a registered SQL driver."""
        return driver in cls._sql_drivers

    @classmethod
    def list_drivers(cls) -> list[str]:
        """Return all registered driver names (SQL + non-SQL)."""
        all_drivers = set(cls._adapters.keys()) | set(cls._sql_drivers.keys())
        return sorted(all_drivers)

    @classmethod
    def reset(cls) -> None:
        """Clear all registrations. **For testing only.**"""
        cls._adapters.clear()
        cls._sql_drivers.clear()
        cls._default_sql_adapter_class = None


# ------------------------------------------------------------------
# Module-level convenience functions
# ------------------------------------------------------------------


@overload
def register_adapter(name: str) -> Callable[[AnyAdapterClass], AnyAdapterClass]: ...


@overload
def register_adapter(name: str, adapter_class: AnyAdapterClass) -> None: ...


def register_adapter(  # type: ignore[misc]
    name: str,
    adapter_class: AnyAdapterClass | None = None,
) -> Callable[[AnyAdapterClass], AnyAdapterClass] | None:
    """Register a non-SQL adapter.

    Can be used as a decorator or direct call::

        # Decorator:
        @register_adapter("mongodb")
        class MongoAdapter(BaseDbAdapter): ...

        # Direct:
        register_adapter("mongodb", MongoAdapter)
    """
    if adapter_class is not None:
        AdapterRegistry.register_adapter(name, adapter_class)
        return None

    def _decorator(cls: AnyAdapterClass) -> AnyAdapterClass:
        AdapterRegistry.register_adapter(name, cls)
        return cls

    return _decorator


def register_sql_driver(
    name: str,
    *,
    dialect: str,
    default_port: int | None = None,
    engine_kwargs: dict[str, Any] | None = None,
    post_connect_hook: Callable[..., None] | None = None,
) -> None:
    """Register a SQL driver profile.

    Example::

        register_sql_driver("oracle", dialect="oracle+cx_oracle", default_port=1521)
    """
    AdapterRegistry.register_sql_driver(
        name,
        dialect=dialect,
        default_port=default_port,
        engine_kwargs=engine_kwargs,
        post_connect_hook=post_connect_hook,
    )


# ------------------------------------------------------------------
# Entry-point plugin auto-discovery
# ------------------------------------------------------------------


def discover_adapters() -> list[str]:
    """Auto-discover database adapters from installed pip packages.

    Scans entry points in the ``llmos_bridge.db_adapters`` group.
    Each entry point should be a callable that performs registration
    (e.g., calls ``register_sql_driver`` or ``register_adapter``).

    Returns list of successfully loaded entry point names.

    Example ``pyproject.toml`` for a community plugin::

        [project.entry-points."llmos_bridge.db_adapters"]
        oracle = "llmos_oracle:register"

    Where ``llmos_oracle/__init__.py`` contains::

        def register():
            from llmos_bridge.modules.database_gateway import register_sql_driver
            register_sql_driver("oracle", dialect="oracle+cx_oracle", default_port=1521)
    """
    import importlib.metadata

    loaded: list[str] = []

    try:
        eps = importlib.metadata.entry_points(group="llmos_bridge.db_adapters")
    except TypeError:
        # Python 3.9 compat: entry_points() doesn't accept group= kwarg
        all_eps = importlib.metadata.entry_points()
        eps = all_eps.get("llmos_bridge.db_adapters", [])  # type: ignore[assignment]

    for ep in eps:
        try:
            register_fn = ep.load()
            register_fn()
            loaded.append(ep.name)
            logger.info("Loaded database adapter plugin: %s", ep.name)
        except Exception:
            logger.warning(
                "Failed to load database adapter plugin: %s",
                ep.name,
                exc_info=True,
            )

    return loaded


# ------------------------------------------------------------------
# Built-in SQL driver registrations
# ------------------------------------------------------------------


def _sqlite_post_connect(engine: Any) -> None:
    """Enable WAL journal mode and foreign keys for SQLite."""
    import sqlalchemy as sa

    with engine.connect() as conn:
        conn.execute(sa.text("PRAGMA journal_mode=WAL"))
        conn.execute(sa.text("PRAGMA foreign_keys=ON"))
        conn.commit()


register_sql_driver(
    "sqlite",
    dialect="sqlite",
    post_connect_hook=_sqlite_post_connect,
)
register_sql_driver(
    "postgresql",
    dialect="postgresql+psycopg2",
    default_port=5432,
)
register_sql_driver(
    "mysql",
    dialect="mysql+mysqlconnector",
    default_port=3306,
)
