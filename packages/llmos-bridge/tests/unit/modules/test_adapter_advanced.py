"""Advanced tests — Database Gateway adapter architecture hardening.

Guarantees the pluggable adapter architecture works for ANY database type
and for user-created adapter modules. Covers:

A. Registration validation (bad classes, invalid profiles, overwrite warnings)
B. FakeMongoAdapter — full NoSQL lifecycle (in-memory document store)
C. Custom SQL driver E2E (registered with SQLite dialect, all 12 actions)
D. Mixed driver routing (SQL + NoSQL simultaneously)
E. Error handling & edge cases
F. Entry-point plugin auto-discovery
G. Async adapter support (BaseAsyncDbAdapter)
H. Return contract validation
"""

from __future__ import annotations

import logging
import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import sqlalchemy as sa

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


# ---------------------------------------------------------------------------
# FakeMongoAdapter — realistic in-memory document store
# ---------------------------------------------------------------------------


class FakeMongoAdapter(BaseDbAdapter):
    """In-memory document store simulating MongoDB behavior.

    Demonstrates that a community adapter can implement the full ABC
    contract without any SQL knowledge. Filters use the same MongoDB-like
    syntax that the gateway module already speaks.
    """

    supports_foreign_keys = False
    supports_schema_enforcement = False

    def __init__(self) -> None:
        # connection_id → {collection → [documents]}
        self._connections: dict[str, dict[str, list[dict[str, Any]]]] = {}
        self._next_id: dict[str, int] = {}  # per-connection auto-increment

    def connect(
        self, connection_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        self._connections[connection_id] = {}
        self._next_id[connection_id] = 1
        return {
            "connection_id": connection_id,
            "driver": "mongodb",
            "database": kwargs.get("database", ""),
            "tables": [],
            "table_count": 0,
            "status": "connected",
        }

    def disconnect(self, connection_id: str) -> dict[str, Any]:
        self._connections.pop(connection_id, None)
        self._next_id.pop(connection_id, None)
        return {"connection_id": connection_id, "status": "disconnected"}

    def introspect(
        self, connection_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        collections = list(self._connections.get(connection_id, {}).keys())
        return {
            "connection_id": connection_id,
            "cached": False,
            "tables": collections,
            "table_count": len(collections),
        }

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
        docs = list(self._get_collection(connection_id, entity))
        if filter_dict:
            docs = [d for d in docs if self._matches(d, filter_dict)]
        if order_by:
            for col_spec in reversed(order_by):
                desc = col_spec.startswith("-")
                key = col_spec.lstrip("-")
                docs.sort(key=lambda d: d.get(key, ""), reverse=desc)
        docs = docs[offset : offset + limit]
        if select:
            docs = [{k: d.get(k) for k in select} for d in docs]
        start = time.monotonic()
        elapsed = time.monotonic() - start
        return {
            "entity": entity,
            "rows": docs,
            "row_count": len(docs),
            "truncated": len(docs) >= limit,
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
        docs = list(self._get_collection(connection_id, entity))
        if filter_dict:
            docs = [d for d in docs if self._matches(d, filter_dict)]
        if not docs:
            return {
                "entity": entity,
                "found": False,
                "record": None,
                "connection_id": connection_id,
            }
        record = docs[0]
        if select:
            record = {k: record.get(k) for k in select}
        return {
            "entity": entity,
            "found": True,
            "record": record,
            "connection_id": connection_id,
        }

    def count(
        self,
        connection_id: str,
        entity: str,
        *,
        filter_dict: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        docs = list(self._get_collection(connection_id, entity))
        if filter_dict:
            docs = [d for d in docs if self._matches(d, filter_dict)]
        return {
            "entity": entity,
            "count": len(docs),
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
        docs = list(self._get_collection(connection_id, entity))
        results = []
        for doc in docs:
            for col in columns:
                val = str(doc.get(col, ""))
                q = query if case_sensitive else query.lower()
                v = val if case_sensitive else val.lower()
                if q in v:
                    results.append(doc)
                    break
        start = time.monotonic()
        elapsed = time.monotonic() - start
        return {
            "entity": entity,
            "query": query,
            "rows": results[:limit],
            "row_count": min(len(results), limit),
            "elapsed_ms": round(elapsed * 1000, 2),
            "connection_id": connection_id,
        }

    def create(
        self, connection_id: str, entity: str, data: dict[str, Any]
    ) -> dict[str, Any]:
        coll = self._ensure_collection(connection_id, entity)
        doc_id = self._next_id[connection_id]
        self._next_id[connection_id] += 1
        doc = {"_id": doc_id, **data}
        coll.append(doc)
        return {
            "entity": entity,
            "created": True,
            "inserted_id": doc_id,
            "connection_id": connection_id,
        }

    def create_many(
        self,
        connection_id: str,
        entity: str,
        records: list[dict[str, Any]],
    ) -> dict[str, Any]:
        coll = self._ensure_collection(connection_id, entity)
        count = 0
        for data in records:
            doc_id = self._next_id[connection_id]
            self._next_id[connection_id] += 1
            coll.append({"_id": doc_id, **data})
            count += 1
        return {
            "entity": entity,
            "created": True,
            "inserted_count": count,
            "connection_id": connection_id,
        }

    def update(
        self,
        connection_id: str,
        entity: str,
        filter_dict: dict[str, Any],
        values: dict[str, Any],
    ) -> dict[str, Any]:
        coll = self._get_collection(connection_id, entity)
        affected = 0
        for doc in coll:
            if self._matches(doc, filter_dict):
                doc.update(values)
                affected += 1
        return {
            "entity": entity,
            "rows_affected": affected,
            "connection_id": connection_id,
        }

    def delete(
        self,
        connection_id: str,
        entity: str,
        filter_dict: dict[str, Any],
    ) -> dict[str, Any]:
        coll = self._get_collection(connection_id, entity)
        to_keep = [d for d in coll if not self._matches(d, filter_dict)]
        deleted = len(coll) - len(to_keep)
        self._connections[connection_id][entity] = to_keep
        return {
            "entity": entity,
            "deleted": True,
            "rows_deleted": deleted,
            "connection_id": connection_id,
        }

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
        docs = list(self._get_collection(connection_id, entity))
        if filter_dict:
            docs = [d for d in docs if self._matches(d, filter_dict)]

        # Group
        groups: dict[tuple, list[dict]] = {}
        for doc in docs:
            key = tuple(doc.get(col) for col in group_by)
            groups.setdefault(key, []).append(doc)

        # Aggregate
        rows = []
        for key, group_docs in groups.items():
            row: dict[str, Any] = {}
            for i, col_name in enumerate(group_by):
                row[col_name] = key[i]
            for col_name, func_name in aggregations.items():
                vals = [d.get(col_name, 0) for d in group_docs]
                label = f"{func_name.lower()}_{col_name}"
                if func_name.lower() == "count":
                    row[label] = len(vals)
                elif func_name.lower() == "sum":
                    row[label] = sum(vals)
                elif func_name.lower() == "avg":
                    row[label] = sum(vals) / len(vals) if vals else 0
                elif func_name.lower() == "min":
                    row[label] = min(vals) if vals else 0
                elif func_name.lower() == "max":
                    row[label] = max(vals) if vals else 0
            rows.append(row)

        start = time.monotonic()
        elapsed = time.monotonic() - start
        return {
            "entity": entity,
            "rows": rows[:limit],
            "row_count": min(len(rows), limit),
            "elapsed_ms": round(elapsed * 1000, 2),
            "connection_id": connection_id,
        }

    def list_connections(self) -> list[str]:
        return list(self._connections.keys())

    def close_all(self) -> None:
        self._connections.clear()
        self._next_id.clear()

    def get_context_snippet(self, connection_id: str) -> str | None:
        colls = list(self._connections.get(connection_id, {}).keys())
        if not colls:
            return None
        return f"### Connection: {connection_id} (mongodb)\nCollections: {', '.join(colls)}"

    # --- Internal helpers ---

    def _get_collection(
        self, connection_id: str, entity: str
    ) -> list[dict[str, Any]]:
        return self._connections.get(connection_id, {}).get(entity, [])

    def _ensure_collection(
        self, connection_id: str, entity: str
    ) -> list[dict[str, Any]]:
        if connection_id not in self._connections:
            self._connections[connection_id] = {}
        if entity not in self._connections[connection_id]:
            self._connections[connection_id][entity] = []
        return self._connections[connection_id][entity]

    @staticmethod
    def _matches(doc: dict[str, Any], filter_dict: dict[str, Any]) -> bool:
        for key, val in filter_dict.items():
            if isinstance(val, dict):
                for op, operand in val.items():
                    doc_val = doc.get(key)
                    if op == "$gt" and not (doc_val is not None and doc_val > operand):
                        return False
                    if op == "$gte" and not (doc_val is not None and doc_val >= operand):
                        return False
                    if op == "$lt" and not (doc_val is not None and doc_val < operand):
                        return False
                    if op == "$lte" and not (doc_val is not None and doc_val <= operand):
                        return False
                    if op == "$ne" and doc_val == operand:
                        return False
                    if op == "$in" and doc_val not in operand:
                        return False
            else:
                if doc.get(key) != val:
                    return False
        return True


# ---------------------------------------------------------------------------
# FakeAsyncRedisAdapter — async adapter for testing BaseAsyncDbAdapter
# ---------------------------------------------------------------------------


class FakeAsyncRedisAdapter(BaseAsyncDbAdapter):
    """In-memory async key-value store simulating Redis behavior."""

    supports_transactions = False
    supports_foreign_keys = False
    supports_schema_enforcement = False
    supports_native_aggregation = False

    def __init__(self) -> None:
        self._connections: dict[str, dict[str, list[dict[str, Any]]]] = {}
        self._next_id: dict[str, int] = {}

    async def connect(self, connection_id: str, **kwargs: Any) -> dict[str, Any]:
        self._connections[connection_id] = {}
        self._next_id[connection_id] = 1
        return {
            "connection_id": connection_id,
            "driver": "redis",
            "database": kwargs.get("database", "0"),
            "tables": [],
            "table_count": 0,
            "status": "connected",
        }

    async def disconnect(self, connection_id: str) -> dict[str, Any]:
        self._connections.pop(connection_id, None)
        self._next_id.pop(connection_id, None)
        return {"connection_id": connection_id, "status": "disconnected"}

    async def introspect(self, connection_id: str, **kwargs: Any) -> dict[str, Any]:
        keys = list(self._connections.get(connection_id, {}).keys())
        return {
            "connection_id": connection_id,
            "cached": False,
            "tables": keys,
            "table_count": len(keys),
        }

    async def find(self, connection_id: str, entity: str, **kwargs: Any) -> dict[str, Any]:
        docs = list(self._connections.get(connection_id, {}).get(entity, []))
        limit = kwargs.get("limit", 100)
        offset = kwargs.get("offset", 0)
        docs = docs[offset : offset + limit]
        return {
            "entity": entity,
            "rows": docs,
            "row_count": len(docs),
            "truncated": len(docs) >= limit,
            "elapsed_ms": 0.01,
            "connection_id": connection_id,
        }

    async def find_one(self, connection_id: str, entity: str, **kwargs: Any) -> dict[str, Any]:
        docs = self._connections.get(connection_id, {}).get(entity, [])
        if not docs:
            return {"entity": entity, "found": False, "record": None, "connection_id": connection_id}
        return {"entity": entity, "found": True, "record": docs[0], "connection_id": connection_id}

    async def count(self, connection_id: str, entity: str, **kwargs: Any) -> dict[str, Any]:
        docs = self._connections.get(connection_id, {}).get(entity, [])
        return {"entity": entity, "count": len(docs), "connection_id": connection_id}

    async def search(self, connection_id: str, entity: str, **kwargs: Any) -> dict[str, Any]:
        return {"entity": entity, "query": kwargs.get("query", ""), "rows": [], "row_count": 0, "elapsed_ms": 0.01, "connection_id": connection_id}

    async def create(self, connection_id: str, entity: str, data: dict[str, Any]) -> dict[str, Any]:
        if connection_id not in self._connections:
            self._connections[connection_id] = {}
        if entity not in self._connections[connection_id]:
            self._connections[connection_id][entity] = []
        doc_id = self._next_id.get(connection_id, 1)
        self._next_id[connection_id] = doc_id + 1
        self._connections[connection_id][entity].append({"_id": doc_id, **data})
        return {"entity": entity, "created": True, "inserted_id": doc_id, "connection_id": connection_id}

    async def create_many(self, connection_id: str, entity: str, records: list[dict[str, Any]]) -> dict[str, Any]:
        for data in records:
            await self.create(connection_id, entity, data)
        return {"entity": entity, "created": True, "inserted_count": len(records), "connection_id": connection_id}

    async def update(self, connection_id: str, entity: str, filter_dict: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
        return {"entity": entity, "rows_affected": 0, "connection_id": connection_id}

    async def delete(self, connection_id: str, entity: str, filter_dict: dict[str, Any]) -> dict[str, Any]:
        return {"entity": entity, "deleted": True, "rows_deleted": 0, "connection_id": connection_id}

    async def aggregate(self, connection_id: str, entity: str, **kwargs: Any) -> dict[str, Any]:
        return {"entity": entity, "rows": [], "row_count": 0, "elapsed_ms": 0.01, "connection_id": connection_id}

    async def list_connections(self) -> list[str]:
        return list(self._connections.keys())

    async def close_all(self) -> None:
        self._connections.clear()
        self._next_id.clear()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_registry():
    """Snapshot and restore the registry around each test."""
    orig_adapters = dict(AdapterRegistry._adapters)
    orig_sql_drivers = dict(AdapterRegistry._sql_drivers)
    orig_default = AdapterRegistry._default_sql_adapter_class
    yield
    AdapterRegistry._adapters = orig_adapters
    AdapterRegistry._sql_drivers = orig_sql_drivers
    AdapterRegistry._default_sql_adapter_class = orig_default


@pytest.fixture()
def gw() -> DatabaseGatewayModule:
    return DatabaseGatewayModule(max_connections=5, schema_cache_ttl=300)


@pytest.fixture()
def mongo_gw(gw: DatabaseGatewayModule) -> DatabaseGatewayModule:
    """Gateway with FakeMongoAdapter registered."""
    register_adapter("mongodb", FakeMongoAdapter)
    return gw


@pytest.fixture()
async def connected_mongo(mongo_gw: DatabaseGatewayModule) -> DatabaseGatewayModule:
    """Gateway with an active MongoDB connection."""
    await mongo_gw._action_connect({
        "driver": "mongodb",
        "database": "testdb",
        "connection_id": "mongo1",
    })
    return mongo_gw


@pytest.fixture()
async def connected_sqlite(gw: DatabaseGatewayModule, tmp_path) -> tuple[DatabaseGatewayModule, str]:
    """Gateway with an active SQLite connection containing test data."""
    db_path = str(tmp_path / "test.db")
    engine = sa.create_engine(f"sqlite:///{db_path}")
    with engine.connect() as conn:
        conn.execute(sa.text(
            "CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "name TEXT NOT NULL, age INTEGER, dept TEXT)"
        ))
        conn.execute(sa.text(
            "INSERT INTO users (name, age, dept) VALUES "
            "('Alice', 30, 'eng'), ('Bob', 25, 'mkt'), ('Charlie', 40, 'eng')"
        ))
        conn.commit()
    engine.dispose()
    await gw._action_connect({
        "driver": "sqlite",
        "database": db_path,
        "connection_id": "sql1",
    })
    return gw, db_path


# ===========================================================================
# A. Registration Validation Tests
# ===========================================================================


@pytest.mark.unit
class TestRegistrationValidation:
    def test_register_non_subclass_raises_type_error(self) -> None:
        class NotAnAdapter:
            pass

        with pytest.raises(TypeError, match="subclass of BaseDbAdapter"):
            register_adapter("bad", NotAnAdapter)  # type: ignore[arg-type]

    def test_register_non_class_raises_type_error(self) -> None:
        with pytest.raises(TypeError, match="subclass of BaseDbAdapter"):
            register_adapter("bad", lambda: None)  # type: ignore[arg-type]

    def test_register_empty_name_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="non-empty string"):
            register_adapter("", FakeMongoAdapter)

    def test_register_overwrite_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        register_adapter("test_db", FakeMongoAdapter)
        with caplog.at_level(logging.WARNING, logger="llmos_bridge.db_gateway"):
            register_adapter("test_db", FakeMongoAdapter)
        assert "Overwriting" in caplog.text

    def test_sql_driver_empty_dialect_raises(self) -> None:
        with pytest.raises(ValueError, match="dialect"):
            register_sql_driver("bad", dialect="")

    def test_sql_driver_invalid_port_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="1-65535"):
            register_sql_driver("bad", dialect="sqlite", default_port=0)

    def test_sql_driver_invalid_port_too_high_raises(self) -> None:
        with pytest.raises(ValueError, match="1-65535"):
            register_sql_driver("bad", dialect="sqlite", default_port=99999)

    def test_sql_driver_non_callable_hook_raises(self) -> None:
        with pytest.raises(TypeError, match="callable"):
            register_sql_driver("bad", dialect="sqlite", post_connect_hook="not_a_function")  # type: ignore[arg-type]

    def test_sql_driver_overwrite_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        register_sql_driver("test_driver", dialect="sqlite")
        with caplog.at_level(logging.WARNING, logger="llmos_bridge.db_gateway"):
            register_sql_driver("test_driver", dialect="sqlite")
        assert "Overwriting" in caplog.text

    def test_driver_profile_post_init_validates_name(self) -> None:
        with pytest.raises(ValueError, match="name"):
            DriverProfile(name="", dialect="sqlite")

    def test_driver_profile_post_init_validates_dialect(self) -> None:
        with pytest.raises(ValueError, match="dialect"):
            DriverProfile(name="test", dialect="")

    def test_driver_profile_post_init_validates_port(self) -> None:
        with pytest.raises(ValueError, match="1-65535"):
            DriverProfile(name="test", dialect="sqlite", default_port=-1)

    def test_driver_profile_post_init_validates_hook(self) -> None:
        with pytest.raises(TypeError, match="callable"):
            DriverProfile(name="test", dialect="sqlite", post_connect_hook=42)  # type: ignore[arg-type]

    def test_register_adapter_accepts_async_adapter(self) -> None:
        register_adapter("async_redis", FakeAsyncRedisAdapter)
        cls = AdapterRegistry.get_adapter_class("async_redis")
        assert cls is FakeAsyncRedisAdapter

    def test_valid_port_boundaries(self) -> None:
        """Ports 1 and 65535 should be accepted."""
        p1 = DriverProfile(name="lo", dialect="sqlite", default_port=1)
        assert p1.default_port == 1
        p2 = DriverProfile(name="hi", dialect="sqlite", default_port=65535)
        assert p2.default_port == 65535


# ===========================================================================
# B. FakeMongoAdapter — Full NoSQL Lifecycle
# ===========================================================================


@pytest.mark.unit
class TestFakeMongoFullLifecycle:
    async def test_register_and_connect(self, mongo_gw: DatabaseGatewayModule) -> None:
        result = await mongo_gw._action_connect({
            "driver": "mongodb",
            "database": "mydb",
            "connection_id": "m1",
        })
        assert result["status"] == "connected"
        assert result["driver"] == "mongodb"

    async def test_create_document(self, connected_mongo: DatabaseGatewayModule) -> None:
        result = await connected_mongo._action_create({
            "connection_id": "mongo1",
            "entity": "users",
            "data": {"name": "Alice", "age": 30},
        })
        assert result["created"] is True
        assert result["inserted_id"] is not None

    async def test_create_many_documents(self, connected_mongo: DatabaseGatewayModule) -> None:
        result = await connected_mongo._action_create_many({
            "connection_id": "mongo1",
            "entity": "users",
            "records": [
                {"name": "Alice", "age": 30},
                {"name": "Bob", "age": 25},
                {"name": "Charlie", "age": 40},
            ],
        })
        assert result["created"] is True
        assert result["inserted_count"] == 3

    async def test_find_all(self, connected_mongo: DatabaseGatewayModule) -> None:
        await connected_mongo._action_create_many({
            "connection_id": "mongo1",
            "entity": "items",
            "records": [{"x": 1}, {"x": 2}, {"x": 3}],
        })
        result = await connected_mongo._action_find({
            "connection_id": "mongo1",
            "entity": "items",
        })
        assert result["row_count"] == 3

    async def test_find_with_filter(self, connected_mongo: DatabaseGatewayModule) -> None:
        await connected_mongo._action_create_many({
            "connection_id": "mongo1",
            "entity": "users",
            "records": [
                {"name": "Alice", "age": 30, "dept": "eng"},
                {"name": "Bob", "age": 25, "dept": "mkt"},
            ],
        })
        result = await connected_mongo._action_find({
            "connection_id": "mongo1",
            "entity": "users",
            "filter": {"dept": "eng"},
        })
        assert result["row_count"] == 1
        assert result["rows"][0]["name"] == "Alice"

    async def test_find_with_comparison_operators(self, connected_mongo: DatabaseGatewayModule) -> None:
        await connected_mongo._action_create_many({
            "connection_id": "mongo1",
            "entity": "users",
            "records": [
                {"name": "Young", "age": 20},
                {"name": "Mid", "age": 30},
                {"name": "Old", "age": 50},
            ],
        })
        result = await connected_mongo._action_find({
            "connection_id": "mongo1",
            "entity": "users",
            "filter": {"age": {"$gte": 30}},
        })
        assert result["row_count"] == 2

    async def test_find_with_pagination(self, connected_mongo: DatabaseGatewayModule) -> None:
        await connected_mongo._action_create_many({
            "connection_id": "mongo1",
            "entity": "items",
            "records": [{"x": i} for i in range(10)],
        })
        result = await connected_mongo._action_find({
            "connection_id": "mongo1",
            "entity": "items",
            "limit": 3,
            "offset": 2,
        })
        assert result["row_count"] == 3
        assert result["rows"][0]["x"] == 2

    async def test_find_one_found(self, connected_mongo: DatabaseGatewayModule) -> None:
        await connected_mongo._action_create({
            "connection_id": "mongo1",
            "entity": "users",
            "data": {"name": "Alice"},
        })
        result = await connected_mongo._action_find_one({
            "connection_id": "mongo1",
            "entity": "users",
            "filter": {"name": "Alice"},
        })
        assert result["found"] is True
        assert result["record"]["name"] == "Alice"

    async def test_find_one_not_found(self, connected_mongo: DatabaseGatewayModule) -> None:
        result = await connected_mongo._action_find_one({
            "connection_id": "mongo1",
            "entity": "users",
            "filter": {"name": "Nobody"},
        })
        assert result["found"] is False
        assert result["record"] is None

    async def test_count_all(self, connected_mongo: DatabaseGatewayModule) -> None:
        await connected_mongo._action_create_many({
            "connection_id": "mongo1",
            "entity": "items",
            "records": [{"x": 1}, {"x": 2}, {"x": 3}],
        })
        result = await connected_mongo._action_count({
            "connection_id": "mongo1",
            "entity": "items",
        })
        assert result["count"] == 3

    async def test_count_with_filter(self, connected_mongo: DatabaseGatewayModule) -> None:
        await connected_mongo._action_create_many({
            "connection_id": "mongo1",
            "entity": "users",
            "records": [{"status": "active"}, {"status": "active"}, {"status": "banned"}],
        })
        result = await connected_mongo._action_count({
            "connection_id": "mongo1",
            "entity": "users",
            "filter": {"status": "active"},
        })
        assert result["count"] == 2

    async def test_update_documents(self, connected_mongo: DatabaseGatewayModule) -> None:
        await connected_mongo._action_create({
            "connection_id": "mongo1",
            "entity": "users",
            "data": {"name": "Alice", "age": 30},
        })
        result = await connected_mongo._action_update({
            "connection_id": "mongo1",
            "entity": "users",
            "filter": {"name": "Alice"},
            "values": {"age": 31},
        })
        assert result["rows_affected"] == 1
        # Verify update applied
        check = await connected_mongo._action_find_one({
            "connection_id": "mongo1",
            "entity": "users",
            "filter": {"name": "Alice"},
        })
        assert check["record"]["age"] == 31

    async def test_delete_documents(self, connected_mongo: DatabaseGatewayModule) -> None:
        await connected_mongo._action_create_many({
            "connection_id": "mongo1",
            "entity": "users",
            "records": [{"name": "Alice"}, {"name": "Bob"}],
        })
        result = await connected_mongo._action_delete({
            "connection_id": "mongo1",
            "entity": "users",
            "filter": {"name": "Alice"},
            "confirm": True,
        })
        assert result["rows_deleted"] == 1
        count = await connected_mongo._action_count({
            "connection_id": "mongo1",
            "entity": "users",
        })
        assert count["count"] == 1

    async def test_search_text(self, connected_mongo: DatabaseGatewayModule) -> None:
        await connected_mongo._action_create_many({
            "connection_id": "mongo1",
            "entity": "articles",
            "records": [
                {"title": "Python Guide", "body": "Learn Python"},
                {"title": "Rust Book", "body": "Learn Rust"},
            ],
        })
        result = await connected_mongo._action_search({
            "connection_id": "mongo1",
            "entity": "articles",
            "query": "python",
            "columns": ["title", "body"],
        })
        assert result["row_count"] == 1

    async def test_aggregate_group_by(self, connected_mongo: DatabaseGatewayModule) -> None:
        await connected_mongo._action_create_many({
            "connection_id": "mongo1",
            "entity": "sales",
            "records": [
                {"dept": "eng", "amount": 100},
                {"dept": "eng", "amount": 200},
                {"dept": "mkt", "amount": 50},
            ],
        })
        result = await connected_mongo._action_aggregate({
            "connection_id": "mongo1",
            "entity": "sales",
            "group_by": ["dept"],
            "aggregations": {"amount": "sum"},
        })
        assert result["row_count"] == 2
        by_dept = {r["dept"]: r for r in result["rows"]}
        assert by_dept["eng"]["sum_amount"] == 300
        assert by_dept["mkt"]["sum_amount"] == 50

    async def test_introspect_returns_collections(self, connected_mongo: DatabaseGatewayModule) -> None:
        await connected_mongo._action_create({
            "connection_id": "mongo1",
            "entity": "users",
            "data": {"name": "Alice"},
        })
        await connected_mongo._action_create({
            "connection_id": "mongo1",
            "entity": "orders",
            "data": {"item": "widget"},
        })
        result = await connected_mongo._action_introspect({
            "connection_id": "mongo1",
        })
        assert "users" in result["tables"]
        assert "orders" in result["tables"]
        assert result["table_count"] == 2

    async def test_disconnect_and_cleanup(self, connected_mongo: DatabaseGatewayModule) -> None:
        result = await connected_mongo._action_disconnect({
            "connection_id": "mongo1",
        })
        assert result["status"] == "disconnected"

    async def test_context_snippet(self, connected_mongo: DatabaseGatewayModule) -> None:
        await connected_mongo._action_create({
            "connection_id": "mongo1",
            "entity": "users",
            "data": {"name": "Alice"},
        })
        snippet = connected_mongo.get_context_snippet()
        assert snippet is not None
        assert "mongo1" in snippet
        assert "mongodb" in snippet


# ===========================================================================
# C. Custom SQL Driver E2E Tests
# ===========================================================================


@pytest.mark.unit
class TestCustomSQLDriverE2E:
    """Register a custom SQL driver (using SQLite dialect) and run all 12 actions."""

    @pytest.fixture(autouse=True)
    async def _setup(self, gw: DatabaseGatewayModule, tmp_path):
        hook_called = []

        def custom_hook(engine: Any) -> None:
            hook_called.append(True)

        register_sql_driver(
            "custom_testdb",
            dialect="sqlite",
            post_connect_hook=custom_hook,
            engine_kwargs={"echo": False},
        )
        self.hook_called = hook_called

        db_path = str(tmp_path / "custom.db")
        engine = sa.create_engine(f"sqlite:///{db_path}")
        with engine.connect() as conn:
            conn.execute(sa.text(
                "CREATE TABLE items (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "name TEXT, price REAL, category TEXT)"
            ))
            conn.execute(sa.text(
                "INSERT INTO items (name, price, category) VALUES "
                "('Widget', 10.0, 'tools'), ('Gadget', 25.0, 'electronics'), "
                "('Doohickey', 5.0, 'tools')"
            ))
            conn.commit()
        engine.dispose()

        await gw._action_connect({
            "driver": "custom_testdb",
            "database": db_path,
            "connection_id": "custom1",
        })
        self.gw = gw

    async def test_connect_works(self) -> None:
        assert "custom1" in [
            cid for cid in self.gw._connection_adapters
        ]

    async def test_post_connect_hook_called(self) -> None:
        assert len(self.hook_called) > 0

    async def test_find(self) -> None:
        result = await self.gw._action_find({
            "connection_id": "custom1",
            "entity": "items",
        })
        assert result["row_count"] == 3

    async def test_find_one(self) -> None:
        result = await self.gw._action_find_one({
            "connection_id": "custom1",
            "entity": "items",
            "filter": {"name": "Widget"},
        })
        assert result["found"] is True

    async def test_create(self) -> None:
        result = await self.gw._action_create({
            "connection_id": "custom1",
            "entity": "items",
            "data": {"name": "Thingamajig", "price": 15.0, "category": "tools"},
        })
        assert result["created"] is True

    async def test_update(self) -> None:
        result = await self.gw._action_update({
            "connection_id": "custom1",
            "entity": "items",
            "filter": {"name": "Widget"},
            "values": {"price": 12.0},
        })
        assert result["rows_affected"] == 1

    async def test_delete(self) -> None:
        result = await self.gw._action_delete({
            "connection_id": "custom1",
            "entity": "items",
            "filter": {"name": "Doohickey"},
            "confirm": True,
        })
        assert result["rows_deleted"] == 1

    async def test_count(self) -> None:
        result = await self.gw._action_count({
            "connection_id": "custom1",
            "entity": "items",
        })
        assert result["count"] == 3

    async def test_search(self) -> None:
        result = await self.gw._action_search({
            "connection_id": "custom1",
            "entity": "items",
            "query": "get",
            "columns": ["name"],
        })
        # Should find "Widget" and "Gadget"
        assert result["row_count"] >= 1

    async def test_aggregate(self) -> None:
        result = await self.gw._action_aggregate({
            "connection_id": "custom1",
            "entity": "items",
            "group_by": ["category"],
            "aggregations": {"price": "avg"},
        })
        assert result["row_count"] >= 1

    async def test_introspect(self) -> None:
        result = await self.gw._action_introspect({
            "connection_id": "custom1",
        })
        table_names = [t["name"] for t in result["tables"]]
        assert "items" in table_names


# ===========================================================================
# D. Mixed Driver Routing Tests
# ===========================================================================


@pytest.mark.unit
class TestMixedDriverRouting:
    async def test_three_drivers_simultaneously(
        self, gw: DatabaseGatewayModule, tmp_path
    ) -> None:
        """SQLite + custom SQL + FakeMongo on different connections."""
        register_adapter("mongodb", FakeMongoAdapter)
        register_sql_driver("custom_sql", dialect="sqlite")

        # SQLite connection
        db1 = str(tmp_path / "db1.db")
        engine = sa.create_engine(f"sqlite:///{db1}")
        with engine.connect() as conn:
            conn.execute(sa.text("CREATE TABLE t1 (id INTEGER PRIMARY KEY, val TEXT)"))
            conn.execute(sa.text("INSERT INTO t1 (val) VALUES ('from_sqlite')"))
            conn.commit()
        engine.dispose()
        await gw._action_connect({"driver": "sqlite", "database": db1, "connection_id": "c_sqlite"})

        # Custom SQL connection (same dialect, different driver name)
        db2 = str(tmp_path / "db2.db")
        engine = sa.create_engine(f"sqlite:///{db2}")
        with engine.connect() as conn:
            conn.execute(sa.text("CREATE TABLE t2 (id INTEGER PRIMARY KEY, val TEXT)"))
            conn.execute(sa.text("INSERT INTO t2 (val) VALUES ('from_custom')"))
            conn.commit()
        engine.dispose()
        await gw._action_connect({"driver": "custom_sql", "database": db2, "connection_id": "c_custom"})

        # MongoDB connection
        await gw._action_connect({"driver": "mongodb", "database": "testdb", "connection_id": "c_mongo"})
        await gw._action_create({"connection_id": "c_mongo", "entity": "docs", "data": {"val": "from_mongo"}})

        # Verify each routes to correct adapter
        r1 = await gw._action_find({"connection_id": "c_sqlite", "entity": "t1"})
        assert r1["rows"][0]["val"] == "from_sqlite"

        r2 = await gw._action_find({"connection_id": "c_custom", "entity": "t2"})
        assert r2["rows"][0]["val"] == "from_custom"

        r3 = await gw._action_find({"connection_id": "c_mongo", "entity": "docs"})
        assert r3["rows"][0]["val"] == "from_mongo"

    async def test_disconnect_one_doesnt_affect_others(
        self, gw: DatabaseGatewayModule, tmp_path
    ) -> None:
        register_adapter("mongodb", FakeMongoAdapter)

        db1 = str(tmp_path / "db1.db")
        engine = sa.create_engine(f"sqlite:///{db1}")
        with engine.connect() as conn:
            conn.execute(sa.text("CREATE TABLE t1 (id INTEGER PRIMARY KEY, val TEXT)"))
            conn.commit()
        engine.dispose()

        await gw._action_connect({"driver": "sqlite", "database": db1, "connection_id": "sql"})
        await gw._action_connect({"driver": "mongodb", "database": "test", "connection_id": "mongo"})

        await gw._action_disconnect({"connection_id": "mongo"})

        # SQLite should still work
        r = await gw._action_find({"connection_id": "sql", "entity": "t1"})
        assert r["entity"] == "t1"

    async def test_adapter_instance_reuse_per_driver(
        self, gw: DatabaseGatewayModule, tmp_path
    ) -> None:
        """Two connections with same driver should share one adapter instance."""
        db1 = str(tmp_path / "db1.db")
        db2 = str(tmp_path / "db2.db")
        for p in [db1, db2]:
            engine = sa.create_engine(f"sqlite:///{p}")
            with engine.connect() as conn:
                conn.execute(sa.text("CREATE TABLE t (id INTEGER PRIMARY KEY)"))
                conn.commit()
            engine.dispose()

        await gw._action_connect({"driver": "sqlite", "database": db1, "connection_id": "c1"})
        await gw._action_connect({"driver": "sqlite", "database": db2, "connection_id": "c2"})

        assert gw._connection_adapters["c1"] is gw._connection_adapters["c2"]

    async def test_context_snippet_aggregates_all(
        self, gw: DatabaseGatewayModule, tmp_path
    ) -> None:
        """Context snippet includes all SQL connections."""
        db1 = str(tmp_path / "db1.db")
        engine = sa.create_engine(f"sqlite:///{db1}")
        with engine.connect() as conn:
            conn.execute(sa.text("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)"))
            conn.commit()
        engine.dispose()

        await gw._action_connect({"driver": "sqlite", "database": db1, "connection_id": "main"})

        snippet = gw.get_context_snippet()
        assert snippet is not None
        assert "main" in snippet


# ===========================================================================
# E. Error Handling & Edge Cases
# ===========================================================================


@pytest.mark.unit
class TestAdapterErrorHandling:
    async def test_unknown_driver_raises_with_available_list(self, gw: DatabaseGatewayModule) -> None:
        from llmos_bridge.exceptions import ActionExecutionError

        with pytest.raises((ValueError, ActionExecutionError)):
            await gw._action_connect({
                "driver": "nonexistent_db",
                "database": "test",
                "connection_id": "bad",
            })

    async def test_operation_on_disconnected_connection_raises(self, gw: DatabaseGatewayModule) -> None:
        from llmos_bridge.exceptions import ActionExecutionError

        with pytest.raises(ActionExecutionError, match="No active connection"):
            await gw._action_find({
                "connection_id": "nonexistent",
                "entity": "users",
            })

    async def test_disconnect_nonexistent_returns_not_connected(self, gw: DatabaseGatewayModule) -> None:
        result = await gw._action_disconnect({
            "connection_id": "never_connected",
        })
        assert result["status"] == "not_connected"

    async def test_delete_without_confirm_blocked_by_module(
        self, connected_mongo: DatabaseGatewayModule
    ) -> None:
        await connected_mongo._action_create({
            "connection_id": "mongo1",
            "entity": "users",
            "data": {"name": "Alice"},
        })
        result = await connected_mongo._action_delete({
            "connection_id": "mongo1",
            "entity": "users",
            "filter": {"name": "Alice"},
            "confirm": False,
        })
        assert result["deleted"] is False
        assert "confirm" in result["reason"]

    async def test_double_connect_same_id_replaces(
        self, mongo_gw: DatabaseGatewayModule
    ) -> None:
        """Second connect with same connection_id should work."""
        await mongo_gw._action_connect({
            "driver": "mongodb",
            "database": "db1",
            "connection_id": "shared",
        })
        await mongo_gw._action_connect({
            "driver": "mongodb",
            "database": "db2",
            "connection_id": "shared",
        })
        # Should be connected (no error)
        result = await mongo_gw._action_find({
            "connection_id": "shared",
            "entity": "anything",
        })
        assert result["row_count"] == 0

    async def test_create_on_nonexistent_entity_works_for_nosql(
        self, connected_mongo: DatabaseGatewayModule
    ) -> None:
        """NoSQL adapters auto-create collections."""
        result = await connected_mongo._action_create({
            "connection_id": "mongo1",
            "entity": "new_collection",
            "data": {"key": "value"},
        })
        assert result["created"] is True


# ===========================================================================
# F. Entry-Point Discovery Tests
# ===========================================================================


@pytest.mark.unit
class TestEntryPointDiscovery:
    def test_discover_loads_valid_plugin(self) -> None:
        mock_ep = MagicMock()
        mock_ep.name = "oracle_plugin"
        register_fn = MagicMock()
        mock_ep.load.return_value = register_fn

        with patch("importlib.metadata.entry_points", return_value=[mock_ep]):
            loaded = discover_adapters()

        register_fn.assert_called_once()
        assert loaded == ["oracle_plugin"]

    def test_discover_skips_failing_plugin_with_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        mock_ep = MagicMock()
        mock_ep.name = "broken_plugin"
        mock_ep.load.return_value = MagicMock(side_effect=ImportError("missing dep"))

        with patch("importlib.metadata.entry_points", return_value=[mock_ep]):
            with caplog.at_level(logging.WARNING, logger="llmos_bridge.db_gateway"):
                loaded = discover_adapters()

        assert loaded == []
        assert "Failed to load" in caplog.text

    def test_discover_returns_loaded_names(self) -> None:
        eps = []
        for name in ["plugin_a", "plugin_b"]:
            ep = MagicMock()
            ep.name = name
            ep.load.return_value = MagicMock()
            eps.append(ep)

        with patch("importlib.metadata.entry_points", return_value=eps):
            loaded = discover_adapters()

        assert loaded == ["plugin_a", "plugin_b"]

    def test_discover_empty_when_no_plugins(self) -> None:
        with patch("importlib.metadata.entry_points", return_value=[]):
            loaded = discover_adapters()
        assert loaded == []

    def test_discover_called_on_module_init(self) -> None:
        with patch(
            "llmos_bridge.modules.database_gateway.module.discover_adapters"
        ) as mock_discover:
            DatabaseGatewayModule(max_connections=2)
        mock_discover.assert_called_once()


# ===========================================================================
# G. Async Adapter Tests
# ===========================================================================


@pytest.mark.unit
class TestAsyncAdapter:
    async def test_async_adapter_is_recognized(self, gw: DatabaseGatewayModule) -> None:
        register_adapter("async_redis", FakeAsyncRedisAdapter)
        adapter = gw._get_or_create_adapter("async_redis")
        assert isinstance(adapter, BaseAsyncDbAdapter)

    async def test_async_adapter_connect(self, gw: DatabaseGatewayModule) -> None:
        register_adapter("async_redis", FakeAsyncRedisAdapter)
        result = await gw._action_connect({
            "driver": "async_redis",
            "database": "0",
            "connection_id": "redis1",
        })
        assert result["status"] == "connected"
        assert result["driver"] == "redis"

    async def test_async_adapter_create(self, gw: DatabaseGatewayModule) -> None:
        register_adapter("async_redis", FakeAsyncRedisAdapter)
        await gw._action_connect({
            "driver": "async_redis",
            "database": "0",
            "connection_id": "redis1",
        })
        result = await gw._action_create({
            "connection_id": "redis1",
            "entity": "cache",
            "data": {"key": "user:1", "value": "Alice"},
        })
        assert result["created"] is True

    async def test_async_adapter_find(self, gw: DatabaseGatewayModule) -> None:
        register_adapter("async_redis", FakeAsyncRedisAdapter)
        await gw._action_connect({
            "driver": "async_redis",
            "database": "0",
            "connection_id": "redis1",
        })
        await gw._action_create({
            "connection_id": "redis1",
            "entity": "cache",
            "data": {"key": "user:1"},
        })
        result = await gw._action_find({
            "connection_id": "redis1",
            "entity": "cache",
        })
        assert result["row_count"] == 1

    async def test_async_adapter_full_lifecycle(self, gw: DatabaseGatewayModule) -> None:
        register_adapter("async_redis", FakeAsyncRedisAdapter)
        await gw._action_connect({"driver": "async_redis", "connection_id": "r1"})
        await gw._action_create({"connection_id": "r1", "entity": "k", "data": {"v": 1}})
        find_result = await gw._action_find({"connection_id": "r1", "entity": "k"})
        assert find_result["row_count"] == 1
        count_result = await gw._action_count({"connection_id": "r1", "entity": "k"})
        assert count_result["count"] == 1
        disc = await gw._action_disconnect({"connection_id": "r1"})
        assert disc["status"] == "disconnected"

    async def test_mixed_sync_and_async_adapters(
        self, gw: DatabaseGatewayModule, tmp_path
    ) -> None:
        """Use sync SQLite and async Redis on different connections."""
        register_adapter("async_redis", FakeAsyncRedisAdapter)

        db_path = str(tmp_path / "test.db")
        engine = sa.create_engine(f"sqlite:///{db_path}")
        with engine.connect() as conn:
            conn.execute(sa.text("CREATE TABLE t1 (id INTEGER PRIMARY KEY, val TEXT)"))
            conn.execute(sa.text("INSERT INTO t1 (val) VALUES ('sql_data')"))
            conn.commit()
        engine.dispose()

        await gw._action_connect({"driver": "sqlite", "database": db_path, "connection_id": "sql"})
        await gw._action_connect({"driver": "async_redis", "connection_id": "redis"})
        await gw._action_create({"connection_id": "redis", "entity": "k", "data": {"val": "redis_data"}})

        sql_result = await gw._action_find({"connection_id": "sql", "entity": "t1"})
        redis_result = await gw._action_find({"connection_id": "redis", "entity": "k"})

        assert sql_result["rows"][0]["val"] == "sql_data"
        assert redis_result["rows"][0]["val"] == "redis_data"

    async def test_async_capability_flags(self) -> None:
        adapter = FakeAsyncRedisAdapter()
        assert adapter.supports_transactions is False
        assert adapter.supports_foreign_keys is False
        assert adapter.supports_native_aggregation is False


# ===========================================================================
# H. Return Contract Validation
# ===========================================================================


@pytest.mark.unit
class TestReturnContracts:
    """Validate that adapter return dicts have all required keys."""

    CONNECT_KEYS = {"connection_id", "status"}
    FIND_KEYS = {"entity", "rows", "row_count", "truncated", "elapsed_ms", "connection_id"}
    FIND_ONE_KEYS = {"entity", "found", "record", "connection_id"}
    CREATE_KEYS = {"entity", "created", "inserted_id", "connection_id"}
    CREATE_MANY_KEYS = {"entity", "created", "inserted_count", "connection_id"}
    UPDATE_KEYS = {"entity", "rows_affected", "connection_id"}
    DELETE_KEYS = {"entity", "deleted", "rows_deleted", "connection_id"}
    COUNT_KEYS = {"entity", "count", "connection_id"}
    AGGREGATE_KEYS = {"entity", "rows", "row_count", "elapsed_ms", "connection_id"}

    async def test_mongo_connect_contract(self, mongo_gw: DatabaseGatewayModule) -> None:
        result = await mongo_gw._action_connect({
            "driver": "mongodb", "database": "test", "connection_id": "c1",
        })
        assert self.CONNECT_KEYS.issubset(result.keys())

    async def test_mongo_find_contract(self, connected_mongo: DatabaseGatewayModule) -> None:
        await connected_mongo._action_create({
            "connection_id": "mongo1", "entity": "t", "data": {"x": 1},
        })
        result = await connected_mongo._action_find({
            "connection_id": "mongo1", "entity": "t",
        })
        assert self.FIND_KEYS.issubset(result.keys())

    async def test_mongo_find_one_contract(self, connected_mongo: DatabaseGatewayModule) -> None:
        result = await connected_mongo._action_find_one({
            "connection_id": "mongo1", "entity": "t",
        })
        assert self.FIND_ONE_KEYS.issubset(result.keys())

    async def test_mongo_create_contract(self, connected_mongo: DatabaseGatewayModule) -> None:
        result = await connected_mongo._action_create({
            "connection_id": "mongo1", "entity": "t", "data": {"x": 1},
        })
        assert self.CREATE_KEYS.issubset(result.keys())

    async def test_mongo_create_many_contract(self, connected_mongo: DatabaseGatewayModule) -> None:
        result = await connected_mongo._action_create_many({
            "connection_id": "mongo1", "entity": "t",
            "records": [{"x": 1}, {"x": 2}],
        })
        assert self.CREATE_MANY_KEYS.issubset(result.keys())

    async def test_mongo_update_contract(self, connected_mongo: DatabaseGatewayModule) -> None:
        await connected_mongo._action_create({
            "connection_id": "mongo1", "entity": "t", "data": {"x": 1},
        })
        result = await connected_mongo._action_update({
            "connection_id": "mongo1", "entity": "t",
            "filter": {"x": 1}, "values": {"x": 2},
        })
        assert self.UPDATE_KEYS.issubset(result.keys())

    async def test_mongo_delete_contract(self, connected_mongo: DatabaseGatewayModule) -> None:
        await connected_mongo._action_create({
            "connection_id": "mongo1", "entity": "t", "data": {"x": 1},
        })
        result = await connected_mongo._action_delete({
            "connection_id": "mongo1", "entity": "t",
            "filter": {"x": 1}, "confirm": True,
        })
        assert self.DELETE_KEYS.issubset(result.keys())

    async def test_mongo_count_contract(self, connected_mongo: DatabaseGatewayModule) -> None:
        result = await connected_mongo._action_count({
            "connection_id": "mongo1", "entity": "t",
        })
        assert self.COUNT_KEYS.issubset(result.keys())

    async def test_mongo_aggregate_contract(self, connected_mongo: DatabaseGatewayModule) -> None:
        await connected_mongo._action_create({
            "connection_id": "mongo1", "entity": "t", "data": {"g": "a", "v": 10},
        })
        result = await connected_mongo._action_aggregate({
            "connection_id": "mongo1", "entity": "t",
            "group_by": ["g"], "aggregations": {"v": "sum"},
        })
        assert self.AGGREGATE_KEYS.issubset(result.keys())

    async def test_sql_find_contract(
        self, connected_sqlite: tuple[DatabaseGatewayModule, str]
    ) -> None:
        gw, _ = connected_sqlite
        result = await gw._action_find({
            "connection_id": "sql1", "entity": "users",
        })
        assert self.FIND_KEYS.issubset(result.keys())

    async def test_sql_create_contract(
        self, connected_sqlite: tuple[DatabaseGatewayModule, str]
    ) -> None:
        gw, _ = connected_sqlite
        result = await gw._action_create({
            "connection_id": "sql1", "entity": "users",
            "data": {"name": "Diana", "age": 28, "dept": "eng"},
        })
        assert self.CREATE_KEYS.issubset(result.keys())
