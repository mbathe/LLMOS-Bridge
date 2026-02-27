"""Database Gateway module — semantic, SQL-free database operations.

Public API for extending database support::

    # Add a SQL database in 5 lines:
    from llmos_bridge.modules.database_gateway import register_sql_driver
    register_sql_driver("oracle", dialect="oracle+cx_oracle", default_port=1521)

    # Add a non-SQL backend (sync) via ABC:
    from llmos_bridge.modules.database_gateway import BaseDbAdapter, register_adapter

    @register_adapter("mongodb")
    class MongoAdapter(BaseDbAdapter):
        ...

    # Add a non-SQL backend (async) via ABC:
    from llmos_bridge.modules.database_gateway import BaseAsyncDbAdapter, register_adapter

    @register_adapter("aio_redis")
    class AsyncRedisAdapter(BaseAsyncDbAdapter):
        ...

    # Auto-discover pip-installed adapter plugins:
    from llmos_bridge.modules.database_gateway import discover_adapters
    discover_adapters()
"""

from llmos_bridge.modules.database_gateway.base_adapter import (
    BaseAsyncDbAdapter,
    BaseDbAdapter,
    DriverProfile,
)
from llmos_bridge.modules.database_gateway.module import DatabaseGatewayModule
from llmos_bridge.modules.database_gateway.registry import (
    AdapterRegistry,
    discover_adapters,
    register_adapter,
    register_sql_driver,
)

# Ensure built-in SQL drivers + SQLAlchemyAdapter are registered on import
import llmos_bridge.modules.database_gateway.sql_adapter  # noqa: F401

__all__ = [
    "DatabaseGatewayModule",
    "BaseDbAdapter",
    "BaseAsyncDbAdapter",
    "DriverProfile",
    "AdapterRegistry",
    "register_adapter",
    "register_sql_driver",
    "discover_adapters",
]
