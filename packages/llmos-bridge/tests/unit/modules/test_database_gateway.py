"""Unit tests — Database Gateway module (db_gateway).

All tests use in-memory SQLite via SQLAlchemy. No external databases required.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from llmos_bridge.modules.database_gateway.module import DatabaseGatewayModule


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def gw() -> DatabaseGatewayModule:
    return DatabaseGatewayModule(max_connections=5, schema_cache_ttl=300)


@pytest.fixture()
async def connected_gw(gw, tmp_path):
    """Gateway with a connected SQLite database containing test data."""
    db_path = str(tmp_path / "test.db")

    # Create schema + seed data via raw SQLAlchemy
    engine = sa.create_engine(f"sqlite:///{db_path}")
    with engine.connect() as conn:
        conn.execute(sa.text(
            "CREATE TABLE users ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  name TEXT NOT NULL,"
            "  email TEXT,"
            "  age INTEGER,"
            "  status TEXT DEFAULT 'active',"
            "  department TEXT"
            ")"
        ))
        conn.execute(sa.text(
            "CREATE TABLE orders ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  user_id INTEGER REFERENCES users(id),"
            "  amount REAL,"
            "  product TEXT"
            ")"
        ))
        # Seed users
        conn.execute(sa.text(
            "INSERT INTO users (name, email, age, status, department) VALUES"
            " ('Alice', 'alice@example.com', 30, 'active', 'engineering'),"
            " ('Bob', 'bob@example.com', 25, 'active', 'marketing'),"
            " ('Charlie', 'charlie@example.com', 40, 'inactive', 'engineering'),"
            " ('Diana', 'diana@example.com', 35, 'active', 'engineering'),"
            " ('Eve', 'eve@example.com', 22, 'banned', 'marketing')"
        ))
        # Seed orders
        conn.execute(sa.text(
            "INSERT INTO orders (user_id, amount, product) VALUES"
            " (1, 100.0, 'Widget'),"
            " (1, 200.0, 'Gadget'),"
            " (2, 50.0, 'Widget'),"
            " (4, 150.0, 'Doohickey')"
        ))
        conn.commit()
    engine.dispose()

    # Connect via gateway
    await gw._action_connect({
        "driver": "sqlite",
        "database": db_path,
        "connection_id": "test",
    })
    return gw


# ---------------------------------------------------------------------------
# Tests — Module basics
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestModuleBasics:
    def test_module_id(self, gw) -> None:
        assert gw.MODULE_ID == "db_gateway"

    def test_version(self, gw) -> None:
        assert gw.VERSION == "1.1.0"

    def test_manifest_action_count(self, gw) -> None:
        manifest = gw.get_manifest()
        assert len(manifest.actions) == 12

    def test_manifest_action_names(self, gw) -> None:
        manifest = gw.get_manifest()
        names = {a.name for a in manifest.actions}
        expected = {
            "connect", "disconnect", "introspect",
            "find", "find_one", "create", "create_many",
            "update", "delete", "count", "aggregate", "search",
        }
        assert names == expected

    def test_manifest_has_tags(self, gw) -> None:
        manifest = gw.get_manifest()
        assert "gateway" in manifest.tags
        assert "database" in manifest.tags

    def test_context_snippet_none_when_no_connections(self, gw) -> None:
        assert gw.get_context_snippet() is None


# ---------------------------------------------------------------------------
# Tests — Connect / Disconnect
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestConnect:
    @pytest.mark.asyncio
    async def test_connect_sqlite_memory(self, gw) -> None:
        result = await gw._action_connect({
            "url": "sqlite:///:memory:",
            "connection_id": "mem",
        })
        assert result["status"] == "connected"
        assert result["connection_id"] == "mem"
        assert result["driver"] == "sqlite"

    @pytest.mark.asyncio
    async def test_connect_sqlite_file(self, gw, tmp_path) -> None:
        db_path = str(tmp_path / "test.db")
        result = await gw._action_connect({
            "driver": "sqlite",
            "database": db_path,
            "connection_id": "file",
        })
        assert result["status"] == "connected"

    @pytest.mark.asyncio
    async def test_connect_populates_schema_cache(self, connected_gw) -> None:
        snippet = connected_gw.get_context_snippet()
        assert snippet is not None
        assert "Database Context" in snippet
        assert "users" in snippet

    @pytest.mark.asyncio
    async def test_disconnect(self, connected_gw) -> None:
        result = await connected_gw._action_disconnect({"connection_id": "test"})
        assert result["status"] == "disconnected"

    @pytest.mark.asyncio
    async def test_disconnect_nonexistent(self, gw) -> None:
        result = await gw._action_disconnect({"connection_id": "nope"})
        assert result["status"] == "not_connected"


# ---------------------------------------------------------------------------
# Tests — Introspect
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIntrospect:
    @pytest.mark.asyncio
    async def test_introspect_returns_tables(self, connected_gw) -> None:
        result = await connected_gw._action_introspect({"connection_id": "test"})
        assert result["table_count"] >= 2
        table_names = [t["name"] for t in result["tables"]]
        assert "users" in table_names
        assert "orders" in table_names

    @pytest.mark.asyncio
    async def test_introspect_cached(self, connected_gw) -> None:
        result = await connected_gw._action_introspect({"connection_id": "test"})
        assert result["cached"] is True

    @pytest.mark.asyncio
    async def test_introspect_refresh(self, connected_gw) -> None:
        result = await connected_gw._action_introspect({
            "connection_id": "test",
            "refresh": True,
        })
        assert result["cached"] is False

    @pytest.mark.asyncio
    async def test_introspect_columns(self, connected_gw) -> None:
        result = await connected_gw._action_introspect({
            "connection_id": "test",
            "refresh": True,
        })
        users_table = next(t for t in result["tables"] if t["name"] == "users")
        col_names = [c["name"] for c in users_table["columns"]]
        assert "id" in col_names
        assert "name" in col_names
        assert "email" in col_names


# ---------------------------------------------------------------------------
# Tests — Find
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFind:
    @pytest.mark.asyncio
    async def test_find_all(self, connected_gw) -> None:
        result = await connected_gw._action_find({
            "entity": "users",
            "connection_id": "test",
        })
        assert result["row_count"] == 5
        assert result["entity"] == "users"

    @pytest.mark.asyncio
    async def test_find_with_eq_filter(self, connected_gw) -> None:
        result = await connected_gw._action_find({
            "entity": "users",
            "filter": {"status": "active"},
            "connection_id": "test",
        })
        assert result["row_count"] == 3
        for row in result["rows"]:
            assert row["status"] == "active"

    @pytest.mark.asyncio
    async def test_find_with_gte_filter(self, connected_gw) -> None:
        result = await connected_gw._action_find({
            "entity": "users",
            "filter": {"age": {"$gte": 30}},
            "connection_id": "test",
        })
        assert result["row_count"] == 3  # Alice(30), Charlie(40), Diana(35)

    @pytest.mark.asyncio
    async def test_find_with_projection(self, connected_gw) -> None:
        result = await connected_gw._action_find({
            "entity": "users",
            "select": ["name", "age"],
            "connection_id": "test",
        })
        assert set(result["rows"][0].keys()) == {"name", "age"}

    @pytest.mark.asyncio
    async def test_find_with_order_by_asc(self, connected_gw) -> None:
        result = await connected_gw._action_find({
            "entity": "users",
            "order_by": ["age"],
            "connection_id": "test",
        })
        ages = [r["age"] for r in result["rows"]]
        assert ages == sorted(ages)

    @pytest.mark.asyncio
    async def test_find_with_order_by_desc(self, connected_gw) -> None:
        result = await connected_gw._action_find({
            "entity": "users",
            "order_by": ["-age"],
            "connection_id": "test",
        })
        ages = [r["age"] for r in result["rows"]]
        assert ages == sorted(ages, reverse=True)

    @pytest.mark.asyncio
    async def test_find_with_limit(self, connected_gw) -> None:
        result = await connected_gw._action_find({
            "entity": "users",
            "limit": 2,
            "connection_id": "test",
        })
        assert result["row_count"] == 2
        assert result["truncated"] is True

    @pytest.mark.asyncio
    async def test_find_with_offset(self, connected_gw) -> None:
        all_result = await connected_gw._action_find({
            "entity": "users",
            "order_by": ["id"],
            "connection_id": "test",
        })
        offset_result = await connected_gw._action_find({
            "entity": "users",
            "order_by": ["id"],
            "offset": 2,
            "connection_id": "test",
        })
        assert offset_result["rows"][0] == all_result["rows"][2]

    @pytest.mark.asyncio
    async def test_find_complex_filter(self, connected_gw) -> None:
        result = await connected_gw._action_find({
            "entity": "users",
            "filter": {
                "status": "active",
                "age": {"$gte": 25, "$lte": 35},
            },
            "connection_id": "test",
        })
        for row in result["rows"]:
            assert row["status"] == "active"
            assert 25 <= row["age"] <= 35

    @pytest.mark.asyncio
    async def test_find_or_filter(self, connected_gw) -> None:
        result = await connected_gw._action_find({
            "entity": "users",
            "filter": {
                "$or": [{"name": "Alice"}, {"name": "Bob"}],
            },
            "connection_id": "test",
        })
        assert result["row_count"] == 2

    @pytest.mark.asyncio
    async def test_find_in_filter(self, connected_gw) -> None:
        result = await connected_gw._action_find({
            "entity": "users",
            "filter": {"status": {"$in": ["active", "banned"]}},
            "connection_id": "test",
        })
        assert result["row_count"] == 4  # 3 active + 1 banned


# ---------------------------------------------------------------------------
# Tests — Find One
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFindOne:
    @pytest.mark.asyncio
    async def test_find_one_found(self, connected_gw) -> None:
        result = await connected_gw._action_find_one({
            "entity": "users",
            "filter": {"name": "Alice"},
            "connection_id": "test",
        })
        assert result["found"] is True
        assert result["record"]["name"] == "Alice"

    @pytest.mark.asyncio
    async def test_find_one_not_found(self, connected_gw) -> None:
        result = await connected_gw._action_find_one({
            "entity": "users",
            "filter": {"name": "Nonexistent"},
            "connection_id": "test",
        })
        assert result["found"] is False
        assert result["record"] is None

    @pytest.mark.asyncio
    async def test_find_one_with_projection(self, connected_gw) -> None:
        result = await connected_gw._action_find_one({
            "entity": "users",
            "filter": {"name": "Bob"},
            "select": ["name", "email"],
            "connection_id": "test",
        })
        assert result["found"] is True
        assert set(result["record"].keys()) == {"name", "email"}


# ---------------------------------------------------------------------------
# Tests — Create
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreate:
    @pytest.mark.asyncio
    async def test_create_record(self, connected_gw) -> None:
        result = await connected_gw._action_create({
            "entity": "users",
            "data": {"name": "Frank", "email": "frank@example.com", "age": 28, "status": "active"},
            "connection_id": "test",
        })
        assert result["created"] is True
        assert result["inserted_id"] is not None

        # Verify via find
        found = await connected_gw._action_find_one({
            "entity": "users",
            "filter": {"name": "Frank"},
            "connection_id": "test",
        })
        assert found["found"] is True
        assert found["record"]["age"] == 28


@pytest.mark.unit
class TestCreateMany:
    @pytest.mark.asyncio
    async def test_create_many(self, connected_gw) -> None:
        result = await connected_gw._action_create_many({
            "entity": "users",
            "records": [
                {"name": "Gina", "email": "gina@example.com", "age": 31},
                {"name": "Hank", "email": "hank@example.com", "age": 45},
            ],
            "connection_id": "test",
        })
        assert result["created"] is True
        assert result["inserted_count"] == 2

        # Verify
        count = await connected_gw._action_count({
            "entity": "users",
            "connection_id": "test",
        })
        assert count["count"] == 7  # 5 original + 2 new


# ---------------------------------------------------------------------------
# Tests — Update
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUpdate:
    @pytest.mark.asyncio
    async def test_update_single(self, connected_gw) -> None:
        result = await connected_gw._action_update({
            "entity": "users",
            "filter": {"name": "Bob"},
            "values": {"status": "suspended"},
            "connection_id": "test",
        })
        assert result["rows_affected"] == 1

        # Verify
        found = await connected_gw._action_find_one({
            "entity": "users",
            "filter": {"name": "Bob"},
            "connection_id": "test",
        })
        assert found["record"]["status"] == "suspended"

    @pytest.mark.asyncio
    async def test_update_multiple(self, connected_gw) -> None:
        result = await connected_gw._action_update({
            "entity": "users",
            "filter": {"department": "engineering"},
            "values": {"status": "on_leave"},
            "connection_id": "test",
        })
        assert result["rows_affected"] == 3  # Alice, Charlie, Diana


# ---------------------------------------------------------------------------
# Tests — Delete
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDelete:
    @pytest.mark.asyncio
    async def test_delete_without_confirm(self, connected_gw) -> None:
        result = await connected_gw._action_delete({
            "entity": "users",
            "filter": {"name": "Eve"},
            "connection_id": "test",
        })
        assert result["deleted"] is False
        assert "confirm" in result["reason"]

    @pytest.mark.asyncio
    async def test_delete_with_confirm(self, connected_gw) -> None:
        result = await connected_gw._action_delete({
            "entity": "users",
            "filter": {"name": "Eve"},
            "confirm": True,
            "connection_id": "test",
        })
        assert result["deleted"] is True
        assert result["rows_deleted"] == 1

        # Verify
        count = await connected_gw._action_count({
            "entity": "users",
            "connection_id": "test",
        })
        assert count["count"] == 4


# ---------------------------------------------------------------------------
# Tests — Count
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCount:
    @pytest.mark.asyncio
    async def test_count_all(self, connected_gw) -> None:
        result = await connected_gw._action_count({
            "entity": "users",
            "connection_id": "test",
        })
        assert result["count"] == 5

    @pytest.mark.asyncio
    async def test_count_with_filter(self, connected_gw) -> None:
        result = await connected_gw._action_count({
            "entity": "users",
            "filter": {"status": "active"},
            "connection_id": "test",
        })
        assert result["count"] == 3


# ---------------------------------------------------------------------------
# Tests — Aggregate
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAggregate:
    @pytest.mark.asyncio
    async def test_aggregate_count_by_department(self, connected_gw) -> None:
        result = await connected_gw._action_aggregate({
            "entity": "users",
            "group_by": ["department"],
            "aggregations": {"id": "count"},
            "connection_id": "test",
        })
        rows = {r["department"]: r["count_id"] for r in result["rows"]}
        assert rows["engineering"] == 3
        assert rows["marketing"] == 2

    @pytest.mark.asyncio
    async def test_aggregate_avg_age(self, connected_gw) -> None:
        result = await connected_gw._action_aggregate({
            "entity": "users",
            "group_by": ["status"],
            "aggregations": {"age": "avg"},
            "connection_id": "test",
        })
        assert result["row_count"] >= 2

    @pytest.mark.asyncio
    async def test_aggregate_with_having(self, connected_gw) -> None:
        result = await connected_gw._action_aggregate({
            "entity": "users",
            "group_by": ["department"],
            "aggregations": {"id": "count"},
            "having": {"count_id": {"$gte": 3}},
            "connection_id": "test",
        })
        # Only engineering (3) passes, marketing (2) does not
        assert result["row_count"] == 1
        assert result["rows"][0]["department"] == "engineering"

    @pytest.mark.asyncio
    async def test_aggregate_sum(self, connected_gw) -> None:
        result = await connected_gw._action_aggregate({
            "entity": "orders",
            "group_by": ["product"],
            "aggregations": {"amount": "sum", "id": "count"},
            "order_by": ["-sum_amount"],
            "connection_id": "test",
        })
        assert result["row_count"] >= 2

    @pytest.mark.asyncio
    async def test_aggregate_with_filter(self, connected_gw) -> None:
        result = await connected_gw._action_aggregate({
            "entity": "users",
            "group_by": ["department"],
            "aggregations": {"id": "count"},
            "filter": {"status": "active"},
            "connection_id": "test",
        })
        rows = {r["department"]: r["count_id"] for r in result["rows"]}
        assert rows["engineering"] == 2  # Alice, Diana (Charlie is inactive)
        assert rows["marketing"] == 1   # Bob (Eve is banned)


# ---------------------------------------------------------------------------
# Tests — Search
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSearch:
    @pytest.mark.asyncio
    async def test_search_by_name(self, connected_gw) -> None:
        result = await connected_gw._action_search({
            "entity": "users",
            "query": "ali",
            "columns": ["name"],
            "connection_id": "test",
        })
        assert result["row_count"] == 1
        assert result["rows"][0]["name"] == "Alice"

    @pytest.mark.asyncio
    async def test_search_across_columns(self, connected_gw) -> None:
        result = await connected_gw._action_search({
            "entity": "users",
            "query": "example.com",
            "columns": ["name", "email"],
            "connection_id": "test",
        })
        assert result["row_count"] == 5  # All users have @example.com emails

    @pytest.mark.asyncio
    async def test_search_no_results(self, connected_gw) -> None:
        result = await connected_gw._action_search({
            "entity": "users",
            "query": "nonexistent_string",
            "columns": ["name", "email"],
            "connection_id": "test",
        })
        assert result["row_count"] == 0


# ---------------------------------------------------------------------------
# Tests — Context snippet
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestContextSnippet:
    @pytest.mark.asyncio
    async def test_snippet_includes_table_info(self, connected_gw) -> None:
        snippet = connected_gw.get_context_snippet()
        assert snippet is not None
        assert "users" in snippet
        assert "orders" in snippet

    @pytest.mark.asyncio
    async def test_snippet_includes_columns(self, connected_gw) -> None:
        snippet = connected_gw.get_context_snippet()
        assert "name" in snippet
        assert "email" in snippet

    @pytest.mark.asyncio
    async def test_snippet_after_disconnect_is_none(self, connected_gw) -> None:
        await connected_gw._action_disconnect({"connection_id": "test"})
        assert connected_gw.get_context_snippet() is None


# ---------------------------------------------------------------------------
# Tests — Error handling
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestErrors:
    @pytest.mark.asyncio
    async def test_find_unknown_entity(self, connected_gw) -> None:
        from llmos_bridge.exceptions import ActionExecutionError

        with pytest.raises(ActionExecutionError, match="Unknown entity"):
            await connected_gw._action_find({
                "entity": "nonexistent_table",
                "connection_id": "test",
            })

    @pytest.mark.asyncio
    async def test_find_bad_filter_column(self, connected_gw) -> None:
        from llmos_bridge.exceptions import ActionExecutionError

        with pytest.raises(ActionExecutionError):
            await connected_gw._action_find({
                "entity": "users",
                "filter": {"nonexistent_column": "value"},
                "connection_id": "test",
            })

    @pytest.mark.asyncio
    async def test_connect_no_connection(self, gw) -> None:
        from llmos_bridge.exceptions import ActionExecutionError

        with pytest.raises(ActionExecutionError, match="No active connection"):
            await gw._action_find({
                "entity": "users",
                "connection_id": "nonexistent",
            })

    @pytest.mark.asyncio
    async def test_aggregate_unknown_function(self, connected_gw) -> None:
        from llmos_bridge.exceptions import ActionExecutionError

        with pytest.raises(ActionExecutionError, match="Unknown aggregate function"):
            await connected_gw._action_aggregate({
                "entity": "users",
                "group_by": ["department"],
                "aggregations": {"id": "median"},
                "connection_id": "test",
            })

    @pytest.mark.asyncio
    async def test_search_unknown_column(self, connected_gw) -> None:
        from llmos_bridge.exceptions import ActionExecutionError

        with pytest.raises(ActionExecutionError, match="Unknown column"):
            await connected_gw._action_search({
                "entity": "users",
                "query": "test",
                "columns": ["nonexistent"],
                "connection_id": "test",
            })
