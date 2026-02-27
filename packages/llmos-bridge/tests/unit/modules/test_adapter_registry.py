"""Unit tests — Database Gateway adapter registry, driver profiles, and extensibility.

Tests the pluggable adapter architecture:
- AdapterRegistry (registration, lookup, reset)
- DriverProfile dataclass
- register_adapter() decorator and direct call
- register_sql_driver() convenience function
- Built-in driver registrations (sqlite, postgresql, mysql)
- SQLAlchemyAdapter auto-registration
- BaseDbAdapter ABC contract enforcement
- Module integration (driver resolution, adapter creation)
"""

from __future__ import annotations

from abc import ABC
from typing import Any

import pytest

from llmos_bridge.modules.database_gateway.base_adapter import (
    BaseDbAdapter,
    DriverProfile,
)
from llmos_bridge.modules.database_gateway.registry import (
    AdapterRegistry,
    register_adapter,
    register_sql_driver,
)


# ---------------------------------------------------------------------------
# Helpers — minimal concrete adapter for testing
# ---------------------------------------------------------------------------


class StubAdapter(BaseDbAdapter):
    """Minimal concrete adapter that satisfies the ABC contract."""

    supports_transactions = False
    supports_foreign_keys = False
    supports_schema_enforcement = False

    def __init__(self) -> None:
        self._connections: dict[str, dict[str, Any]] = {}

    def connect(self, connection_id, **kwargs) -> dict[str, Any]:
        self._connections[connection_id] = kwargs
        return {"connection_id": connection_id, "status": "connected"}

    def disconnect(self, connection_id) -> dict[str, Any]:
        self._connections.pop(connection_id, None)
        return {"connection_id": connection_id, "status": "disconnected"}

    def introspect(self, connection_id, **kwargs) -> dict[str, Any]:
        return {"connection_id": connection_id, "tables": [], "table_count": 0}

    def find(self, connection_id, entity, **kwargs) -> dict[str, Any]:
        return {"entity": entity, "rows": [], "row_count": 0}

    def find_one(self, connection_id, entity, **kwargs) -> dict[str, Any]:
        return {"entity": entity, "found": False, "record": None}

    def count(self, connection_id, entity, **kwargs) -> dict[str, Any]:
        return {"entity": entity, "count": 0}

    def search(self, connection_id, entity, **kwargs) -> dict[str, Any]:
        return {"entity": entity, "rows": [], "row_count": 0}

    def create(self, connection_id, entity, data) -> dict[str, Any]:
        return {"entity": entity, "created": True, "inserted_id": 1}

    def create_many(self, connection_id, entity, records) -> dict[str, Any]:
        return {"entity": entity, "created": True, "inserted_count": len(records)}

    def update(self, connection_id, entity, filter_dict, values) -> dict[str, Any]:
        return {"entity": entity, "rows_affected": 0}

    def delete(self, connection_id, entity, filter_dict) -> dict[str, Any]:
        return {"entity": entity, "deleted": True, "rows_deleted": 0}

    def aggregate(self, connection_id, entity, **kwargs) -> dict[str, Any]:
        return {"entity": entity, "rows": [], "row_count": 0}

    def list_connections(self) -> list[str]:
        return list(self._connections.keys())

    def close_all(self) -> None:
        self._connections.clear()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_registry():
    """Snapshot and restore the registry around each test."""
    # Save current state
    orig_adapters = dict(AdapterRegistry._adapters)
    orig_sql_drivers = dict(AdapterRegistry._sql_drivers)
    orig_default = AdapterRegistry._default_sql_adapter_class
    yield
    # Restore
    AdapterRegistry._adapters = orig_adapters
    AdapterRegistry._sql_drivers = orig_sql_drivers
    AdapterRegistry._default_sql_adapter_class = orig_default


# ---------------------------------------------------------------------------
# Tests — DriverProfile dataclass
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDriverProfile:
    def test_basic_fields(self) -> None:
        p = DriverProfile(name="oracle", dialect="oracle+cx_oracle", default_port=1521)
        assert p.name == "oracle"
        assert p.dialect == "oracle+cx_oracle"
        assert p.default_port == 1521
        assert p.engine_kwargs == {}
        assert p.post_connect_hook is None

    def test_engine_kwargs_default_factory(self) -> None:
        p1 = DriverProfile(name="a", dialect="a")
        p2 = DriverProfile(name="b", dialect="b")
        assert p1.engine_kwargs is not p2.engine_kwargs  # independent dicts

    def test_post_connect_hook(self) -> None:
        hook_called = []

        def my_hook(engine: Any) -> None:
            hook_called.append(engine)

        p = DriverProfile(name="test", dialect="test", post_connect_hook=my_hook)
        p.post_connect_hook("fake_engine")  # type: ignore[arg-type]
        assert hook_called == ["fake_engine"]


# ---------------------------------------------------------------------------
# Tests — AdapterRegistry
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAdapterRegistryNonSQL:
    def test_register_and_lookup_adapter(self) -> None:
        AdapterRegistry.register_adapter("mongodb", StubAdapter)
        cls = AdapterRegistry.get_adapter_class("mongodb")
        assert cls is StubAdapter

    def test_unknown_driver_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown database driver 'redis'"):
            AdapterRegistry.get_adapter_class("redis")

    def test_error_message_lists_available_drivers(self) -> None:
        AdapterRegistry.register_adapter("redis", StubAdapter)
        with pytest.raises(ValueError, match="redis") as exc_info:
            AdapterRegistry.get_adapter_class("nonexistent")
        assert "redis" in str(exc_info.value)

    def test_non_sql_overrides_sql_driver(self) -> None:
        """Explicit adapter registration takes priority over SQL driver."""
        # sqlite is a built-in SQL driver
        AdapterRegistry.register_adapter("sqlite", StubAdapter)
        cls = AdapterRegistry.get_adapter_class("sqlite")
        assert cls is StubAdapter

    def test_is_sql_driver_false_for_non_sql(self) -> None:
        AdapterRegistry.register_adapter("mongodb", StubAdapter)
        assert not AdapterRegistry.is_sql_driver("mongodb")


@pytest.mark.unit
class TestAdapterRegistrySQL:
    def test_register_sql_driver(self) -> None:
        AdapterRegistry.register_sql_driver(
            "oracle", dialect="oracle+cx_oracle", default_port=1521
        )
        profile = AdapterRegistry.get_sql_driver_profile("oracle")
        assert profile is not None
        assert profile.name == "oracle"
        assert profile.dialect == "oracle+cx_oracle"
        assert profile.default_port == 1521

    def test_is_sql_driver(self) -> None:
        AdapterRegistry.register_sql_driver("testdb", dialect="sqlite")
        assert AdapterRegistry.is_sql_driver("testdb")

    def test_sql_driver_returns_default_adapter(self) -> None:
        """SQL driver lookup returns the default SQL adapter class."""
        AdapterRegistry.register_sql_driver("testdb", dialect="sqlite")
        AdapterRegistry.set_default_sql_adapter(StubAdapter)
        cls = AdapterRegistry.get_adapter_class("testdb")
        assert cls is StubAdapter

    def test_sql_driver_without_default_raises(self) -> None:
        """SQL driver with no default adapter set raises RuntimeError."""
        AdapterRegistry._default_sql_adapter_class = None
        AdapterRegistry.register_sql_driver("testdb", dialect="sqlite")
        with pytest.raises(RuntimeError, match="no default SQL adapter"):
            AdapterRegistry.get_adapter_class("testdb")

    def test_get_sql_driver_profile_none_for_unknown(self) -> None:
        assert AdapterRegistry.get_sql_driver_profile("nonexistent") is None

    def test_engine_kwargs_passed_through(self) -> None:
        AdapterRegistry.register_sql_driver(
            "custom", dialect="sqlite", engine_kwargs={"echo": True}
        )
        profile = AdapterRegistry.get_sql_driver_profile("custom")
        assert profile is not None
        assert profile.engine_kwargs == {"echo": True}

    def test_post_connect_hook_stored(self) -> None:
        def hook(engine: Any) -> None:
            pass

        AdapterRegistry.register_sql_driver(
            "custom", dialect="sqlite", post_connect_hook=hook
        )
        profile = AdapterRegistry.get_sql_driver_profile("custom")
        assert profile is not None
        assert profile.post_connect_hook is hook


@pytest.mark.unit
class TestAdapterRegistryListAndReset:
    def test_list_drivers_sorted(self) -> None:
        drivers = AdapterRegistry.list_drivers()
        assert drivers == sorted(drivers)

    def test_list_drivers_includes_both_sql_and_non_sql(self) -> None:
        AdapterRegistry.register_adapter("mongodb", StubAdapter)
        drivers = AdapterRegistry.list_drivers()
        assert "mongodb" in drivers
        assert "sqlite" in drivers  # built-in SQL

    def test_reset_clears_all(self) -> None:
        AdapterRegistry.register_adapter("test1", StubAdapter)
        AdapterRegistry.register_sql_driver("test2", dialect="sqlite")
        AdapterRegistry.set_default_sql_adapter(StubAdapter)
        AdapterRegistry.reset()
        assert AdapterRegistry.list_drivers() == []
        assert AdapterRegistry._default_sql_adapter_class is None


# ---------------------------------------------------------------------------
# Tests — Module-level convenience functions
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestConvenienceFunctions:
    def test_register_adapter_decorator(self) -> None:
        @register_adapter("influxdb")
        class InfluxAdapter(StubAdapter):
            pass

        cls = AdapterRegistry.get_adapter_class("influxdb")
        assert cls is InfluxAdapter

    def test_register_adapter_direct_call(self) -> None:
        register_adapter("redis", StubAdapter)
        cls = AdapterRegistry.get_adapter_class("redis")
        assert cls is StubAdapter

    def test_register_adapter_decorator_returns_class(self) -> None:
        @register_adapter("test")
        class TestAdapter(StubAdapter):
            pass

        # Decorator should return the class itself
        assert issubclass(TestAdapter, BaseDbAdapter)

    def test_register_sql_driver_function(self) -> None:
        register_sql_driver(
            "cockroachdb",
            dialect="cockroachdb",
            default_port=26257,
        )
        assert AdapterRegistry.is_sql_driver("cockroachdb")
        profile = AdapterRegistry.get_sql_driver_profile("cockroachdb")
        assert profile is not None
        assert profile.default_port == 26257


# ---------------------------------------------------------------------------
# Tests — Built-in driver registrations
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuiltinDrivers:
    def test_sqlite_registered(self) -> None:
        assert AdapterRegistry.is_sql_driver("sqlite")

    def test_postgresql_registered(self) -> None:
        assert AdapterRegistry.is_sql_driver("postgresql")

    def test_mysql_registered(self) -> None:
        assert AdapterRegistry.is_sql_driver("mysql")

    def test_sqlite_profile(self) -> None:
        profile = AdapterRegistry.get_sql_driver_profile("sqlite")
        assert profile is not None
        assert profile.dialect == "sqlite"
        assert profile.post_connect_hook is not None

    def test_postgresql_profile(self) -> None:
        profile = AdapterRegistry.get_sql_driver_profile("postgresql")
        assert profile is not None
        assert profile.dialect == "postgresql+psycopg2"
        assert profile.default_port == 5432

    def test_mysql_profile(self) -> None:
        profile = AdapterRegistry.get_sql_driver_profile("mysql")
        assert profile is not None
        assert profile.dialect == "mysql+mysqlconnector"
        assert profile.default_port == 3306

    def test_builtin_count(self) -> None:
        """At least 3 built-in SQL drivers should be present."""
        sql_drivers = [
            d for d in AdapterRegistry.list_drivers()
            if AdapterRegistry.is_sql_driver(d)
        ]
        assert len(sql_drivers) >= 3


# ---------------------------------------------------------------------------
# Tests — SQLAlchemyAdapter auto-registration
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSQLAlchemyAdapterAutoRegistration:
    def test_default_sql_adapter_is_set(self) -> None:
        """Importing sql_adapter sets the default SQL adapter."""
        from llmos_bridge.modules.database_gateway.sql_adapter import (
            SQLAlchemyAdapter,
        )

        assert AdapterRegistry._default_sql_adapter_class is SQLAlchemyAdapter

    def test_sql_driver_resolves_to_sqlalchemy_adapter(self) -> None:
        from llmos_bridge.modules.database_gateway.sql_adapter import (
            SQLAlchemyAdapter,
        )

        cls = AdapterRegistry.get_adapter_class("sqlite")
        assert cls is SQLAlchemyAdapter

    def test_custom_sql_driver_resolves_to_sqlalchemy_adapter(self) -> None:
        from llmos_bridge.modules.database_gateway.sql_adapter import (
            SQLAlchemyAdapter,
        )

        register_sql_driver("oracle", dialect="oracle+cx_oracle", default_port=1521)
        cls = AdapterRegistry.get_adapter_class("oracle")
        assert cls is SQLAlchemyAdapter


# ---------------------------------------------------------------------------
# Tests — BaseDbAdapter ABC contract
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBaseDbAdapterABC:
    def test_is_abstract(self) -> None:
        assert issubclass(BaseDbAdapter, ABC)

    def test_cannot_instantiate_abc(self) -> None:
        with pytest.raises(TypeError):
            BaseDbAdapter()  # type: ignore[abstract]

    def test_default_capability_flags(self) -> None:
        """Default flags are all True (SQL-oriented)."""
        adapter = StubAdapter()
        # StubAdapter overrides some to False, but let's check the class defaults
        assert BaseDbAdapter.supports_transactions is True
        assert BaseDbAdapter.supports_foreign_keys is True
        assert BaseDbAdapter.supports_schema_enforcement is True
        assert BaseDbAdapter.supports_native_aggregation is True
        assert BaseDbAdapter.supports_native_search is True

    def test_stub_overrides_flags(self) -> None:
        adapter = StubAdapter()
        assert adapter.supports_transactions is False
        assert adapter.supports_foreign_keys is False
        assert adapter.supports_schema_enforcement is False
        # Not overridden → inherited defaults
        assert adapter.supports_native_aggregation is True
        assert adapter.supports_native_search is True

    def test_get_context_snippet_default_none(self) -> None:
        adapter = StubAdapter()
        assert adapter.get_context_snippet("any_conn") is None

    def test_concrete_adapter_operations(self) -> None:
        """A concrete adapter can be instantiated and its methods called."""
        adapter = StubAdapter()
        result = adapter.connect("conn1", driver="test", database="test.db")
        assert result["status"] == "connected"
        assert "conn1" in adapter.list_connections()

        adapter.disconnect("conn1")
        assert "conn1" not in adapter.list_connections()

    def test_close_all(self) -> None:
        adapter = StubAdapter()
        adapter.connect("c1")
        adapter.connect("c2")
        assert len(adapter.list_connections()) == 2
        adapter.close_all()
        assert len(adapter.list_connections()) == 0


# ---------------------------------------------------------------------------
# Tests — Module-level integration (driver resolution)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestModuleDriverResolution:
    def test_module_resolves_builtin_driver(self) -> None:
        from llmos_bridge.modules.database_gateway.module import (
            DatabaseGatewayModule,
        )

        gw = DatabaseGatewayModule(max_connections=2)
        adapter = gw._get_or_create_adapter("sqlite")
        assert adapter is not None

    def test_module_resolves_custom_sql_driver(self) -> None:
        from llmos_bridge.modules.database_gateway.module import (
            DatabaseGatewayModule,
        )

        register_sql_driver("testdb", dialect="sqlite")
        gw = DatabaseGatewayModule(max_connections=2)
        adapter = gw._get_or_create_adapter("testdb")
        assert adapter is not None

    def test_module_resolves_non_sql_adapter(self) -> None:
        from llmos_bridge.modules.database_gateway.module import (
            DatabaseGatewayModule,
        )

        register_adapter("stub_nosql", StubAdapter)
        gw = DatabaseGatewayModule(max_connections=2)
        adapter = gw._get_or_create_adapter("stub_nosql")
        assert isinstance(adapter, StubAdapter)

    def test_module_reuses_adapter_instance(self) -> None:
        from llmos_bridge.modules.database_gateway.module import (
            DatabaseGatewayModule,
        )

        gw = DatabaseGatewayModule(max_connections=2)
        a1 = gw._get_or_create_adapter("sqlite")
        a2 = gw._get_or_create_adapter("sqlite")
        assert a1 is a2

    def test_module_unknown_driver_raises(self) -> None:
        from llmos_bridge.modules.database_gateway.module import (
            DatabaseGatewayModule,
        )

        gw = DatabaseGatewayModule(max_connections=2)
        with pytest.raises(ValueError, match="Unknown database driver"):
            gw._get_or_create_adapter("nonexistent_db")

    def test_detect_driver_from_url_sqlite(self) -> None:
        from llmos_bridge.modules.database_gateway.module import (
            DatabaseGatewayModule,
        )

        driver = DatabaseGatewayModule._detect_driver_from_url("sqlite:///test.db")
        assert driver == "sqlite"

    def test_detect_driver_from_url_postgresql(self) -> None:
        from llmos_bridge.modules.database_gateway.module import (
            DatabaseGatewayModule,
        )

        driver = DatabaseGatewayModule._detect_driver_from_url(
            "postgresql://user:pass@localhost/db"
        )
        assert driver == "postgresql"

    def test_detect_driver_from_url_custom_registered(self) -> None:
        from llmos_bridge.modules.database_gateway.module import (
            DatabaseGatewayModule,
        )

        register_sql_driver("cockroachdb", dialect="cockroachdb", default_port=26257)
        driver = DatabaseGatewayModule._detect_driver_from_url(
            "cockroachdb://user:pass@localhost:26257/mydb"
        )
        assert driver == "cockroachdb"


# ---------------------------------------------------------------------------
# Tests — Public API exports
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPublicExports:
    def test_init_exports_base_adapter(self) -> None:
        from llmos_bridge.modules.database_gateway import BaseDbAdapter as BA

        assert BA is BaseDbAdapter

    def test_init_exports_driver_profile(self) -> None:
        from llmos_bridge.modules.database_gateway import DriverProfile as DP

        assert DP is DriverProfile

    def test_init_exports_registry(self) -> None:
        from llmos_bridge.modules.database_gateway import AdapterRegistry as AR

        assert AR is AdapterRegistry

    def test_init_exports_register_adapter(self) -> None:
        from llmos_bridge.modules.database_gateway import (
            register_adapter as ra,
        )

        assert callable(ra)

    def test_init_exports_register_sql_driver(self) -> None:
        from llmos_bridge.modules.database_gateway import (
            register_sql_driver as rsd,
        )

        assert callable(rsd)
