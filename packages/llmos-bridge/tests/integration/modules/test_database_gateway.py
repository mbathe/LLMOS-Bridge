"""Integration tests — Database Gateway module against real SQLite.

Full lifecycle tests: connect → introspect → CRUD → aggregate → search → disconnect.
Uses real filesystem SQLite databases.
"""

from __future__ import annotations

import pytest

from llmos_bridge.modules.database_gateway.module import DatabaseGatewayModule


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def gw() -> DatabaseGatewayModule:
    return DatabaseGatewayModule(max_connections=5, schema_cache_ttl=300)


# ---------------------------------------------------------------------------
# Tests — Full CRUD lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestFullLifecycle:
    @pytest.mark.asyncio
    async def test_complete_crud_lifecycle(self, gw, tmp_path) -> None:
        """End-to-end: connect → create_table → create → find → update → delete → count."""
        import sqlalchemy as sa

        db_path = str(tmp_path / "lifecycle.db")

        # Step 1: Connect
        connect_result = await gw._action_connect({
            "driver": "sqlite",
            "database": db_path,
            "connection_id": "lifecycle",
        })
        assert connect_result["status"] == "connected"

        # Step 2: Create table via raw SQLAlchemy (simulating pre-existing schema)
        engine = sa.create_engine(f"sqlite:///{db_path}")
        with engine.connect() as conn:
            conn.execute(sa.text(
                "CREATE TABLE products ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  name TEXT NOT NULL,"
                "  price REAL NOT NULL,"
                "  category TEXT,"
                "  in_stock INTEGER DEFAULT 1"
                ")"
            ))
            conn.commit()
        engine.dispose()

        # Refresh metadata to pick up new table
        await gw._action_introspect({
            "connection_id": "lifecycle",
            "refresh": True,
        })

        # Step 3: Create records
        await gw._action_create({
            "entity": "products",
            "data": {"name": "Widget", "price": 9.99, "category": "tools", "in_stock": 1},
            "connection_id": "lifecycle",
        })
        await gw._action_create({
            "entity": "products",
            "data": {"name": "Gadget", "price": 19.99, "category": "electronics", "in_stock": 1},
            "connection_id": "lifecycle",
        })
        await gw._action_create({
            "entity": "products",
            "data": {"name": "Doohickey", "price": 4.99, "category": "tools", "in_stock": 0},
            "connection_id": "lifecycle",
        })

        # Step 4: Count all
        count = await gw._action_count({
            "entity": "products",
            "connection_id": "lifecycle",
        })
        assert count["count"] == 3

        # Step 5: Find with filter
        tools = await gw._action_find({
            "entity": "products",
            "filter": {"category": "tools"},
            "order_by": ["price"],
            "connection_id": "lifecycle",
        })
        assert tools["row_count"] == 2
        assert tools["rows"][0]["name"] == "Doohickey"  # Cheaper first

        # Step 6: Find one
        widget = await gw._action_find_one({
            "entity": "products",
            "filter": {"name": "Widget"},
            "connection_id": "lifecycle",
        })
        assert widget["found"] is True
        assert widget["record"]["price"] == 9.99

        # Step 7: Update
        update_result = await gw._action_update({
            "entity": "products",
            "filter": {"name": "Doohickey"},
            "values": {"price": 5.99, "in_stock": 1},
            "connection_id": "lifecycle",
        })
        assert update_result["rows_affected"] == 1

        # Verify update
        updated = await gw._action_find_one({
            "entity": "products",
            "filter": {"name": "Doohickey"},
            "connection_id": "lifecycle",
        })
        assert updated["record"]["price"] == 5.99
        assert updated["record"]["in_stock"] == 1

        # Step 8: Delete
        delete_result = await gw._action_delete({
            "entity": "products",
            "filter": {"name": "Gadget"},
            "confirm": True,
            "connection_id": "lifecycle",
        })
        assert delete_result["deleted"] is True
        assert delete_result["rows_deleted"] == 1

        # Step 9: Final count
        final_count = await gw._action_count({
            "entity": "products",
            "connection_id": "lifecycle",
        })
        assert final_count["count"] == 2

        # Step 10: Disconnect
        disconnect_result = await gw._action_disconnect({
            "connection_id": "lifecycle",
        })
        assert disconnect_result["status"] == "disconnected"


# ---------------------------------------------------------------------------
# Tests — Batch operations
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestBatchOperations:
    @pytest.mark.asyncio
    async def test_create_many_and_aggregate(self, gw, tmp_path) -> None:
        import sqlalchemy as sa

        db_path = str(tmp_path / "batch.db")

        # Setup
        engine = sa.create_engine(f"sqlite:///{db_path}")
        with engine.connect() as conn:
            conn.execute(sa.text(
                "CREATE TABLE employees ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  name TEXT NOT NULL,"
                "  department TEXT NOT NULL,"
                "  salary REAL NOT NULL"
                ")"
            ))
            conn.commit()
        engine.dispose()

        await gw._action_connect({
            "driver": "sqlite",
            "database": db_path,
            "connection_id": "batch",
        })

        # Batch insert
        result = await gw._action_create_many({
            "entity": "employees",
            "records": [
                {"name": "Alice", "department": "eng", "salary": 120000},
                {"name": "Bob", "department": "eng", "salary": 110000},
                {"name": "Charlie", "department": "sales", "salary": 90000},
                {"name": "Diana", "department": "eng", "salary": 130000},
                {"name": "Eve", "department": "sales", "salary": 95000},
            ],
            "connection_id": "batch",
        })
        assert result["inserted_count"] == 5

        # Aggregate: avg salary by department
        agg = await gw._action_aggregate({
            "entity": "employees",
            "group_by": ["department"],
            "aggregations": {"salary": "avg", "id": "count"},
            "order_by": ["-avg_salary"],
            "connection_id": "batch",
        })
        assert agg["row_count"] == 2
        eng_row = next(r for r in agg["rows"] if r["department"] == "eng")
        assert eng_row["count_id"] == 3
        assert eng_row["avg_salary"] == 120000.0

        # Aggregate with HAVING
        agg_having = await gw._action_aggregate({
            "entity": "employees",
            "group_by": ["department"],
            "aggregations": {"id": "count"},
            "having": {"count_id": {"$gte": 3}},
            "connection_id": "batch",
        })
        assert agg_having["row_count"] == 1
        assert agg_having["rows"][0]["department"] == "eng"

        await gw._action_disconnect({"connection_id": "batch"})


# ---------------------------------------------------------------------------
# Tests — Search
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestSearchIntegration:
    @pytest.mark.asyncio
    async def test_search_across_columns(self, gw, tmp_path) -> None:
        import sqlalchemy as sa

        db_path = str(tmp_path / "search.db")
        engine = sa.create_engine(f"sqlite:///{db_path}")
        with engine.connect() as conn:
            conn.execute(sa.text(
                "CREATE TABLE articles ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  title TEXT NOT NULL,"
                "  body TEXT,"
                "  author TEXT"
                ")"
            ))
            conn.execute(sa.text(
                "INSERT INTO articles (title, body, author) VALUES"
                " ('Python Guide', 'Learn Python programming', 'Alice'),"
                " ('SQL Tutorial', 'Database queries for beginners', 'Bob'),"
                " ('Python Advanced', 'Advanced Python patterns', 'Charlie')"
            ))
            conn.commit()
        engine.dispose()

        await gw._action_connect({
            "driver": "sqlite",
            "database": db_path,
            "connection_id": "search",
        })

        # Search for "python" in title and body
        result = await gw._action_search({
            "entity": "articles",
            "query": "python",
            "columns": ["title", "body"],
            "connection_id": "search",
        })
        assert result["row_count"] == 2  # Both Python articles

        # Search for "Alice" in author only
        result2 = await gw._action_search({
            "entity": "articles",
            "query": "Alice",
            "columns": ["author"],
            "connection_id": "search",
        })
        assert result2["row_count"] == 1

        await gw._action_disconnect({"connection_id": "search"})


# ---------------------------------------------------------------------------
# Tests — Complex filters
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestComplexFilters:
    @pytest.mark.asyncio
    async def test_between_filter(self, gw, tmp_path) -> None:
        import sqlalchemy as sa

        db_path = str(tmp_path / "filters.db")
        engine = sa.create_engine(f"sqlite:///{db_path}")
        with engine.connect() as conn:
            conn.execute(sa.text(
                "CREATE TABLE scores ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  student TEXT NOT NULL,"
                "  score INTEGER NOT NULL"
                ")"
            ))
            conn.execute(sa.text(
                "INSERT INTO scores (student, score) VALUES"
                " ('Alice', 95), ('Bob', 72), ('Charlie', 85),"
                " ('Diana', 60), ('Eve', 88)"
            ))
            conn.commit()
        engine.dispose()

        await gw._action_connect({
            "driver": "sqlite",
            "database": db_path,
            "connection_id": "filters",
        })

        # Between filter
        result = await gw._action_find({
            "entity": "scores",
            "filter": {"score": {"$between": [70, 90]}},
            "connection_id": "filters",
        })
        assert result["row_count"] == 3  # Bob(72), Charlie(85), Eve(88)

        # OR with nested conditions
        result2 = await gw._action_find({
            "entity": "scores",
            "filter": {
                "$or": [
                    {"score": {"$gte": 90}},
                    {"score": {"$lte": 65}},
                ],
            },
            "connection_id": "filters",
        })
        assert result2["row_count"] == 2  # Alice(95), Diana(60)

        # IN filter
        result3 = await gw._action_find({
            "entity": "scores",
            "filter": {"student": {"$in": ["Alice", "Eve"]}},
            "connection_id": "filters",
        })
        assert result3["row_count"] == 2

        await gw._action_disconnect({"connection_id": "filters"})


# ---------------------------------------------------------------------------
# Tests — Introspection accuracy
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestIntrospectionAccuracy:
    @pytest.mark.asyncio
    async def test_introspect_detects_columns_and_types(self, gw, tmp_path) -> None:
        import sqlalchemy as sa

        db_path = str(tmp_path / "introspect.db")
        engine = sa.create_engine(f"sqlite:///{db_path}")
        with engine.connect() as conn:
            conn.execute(sa.text(
                "CREATE TABLE test_table ("
                "  id INTEGER PRIMARY KEY,"
                "  name TEXT NOT NULL,"
                "  value REAL DEFAULT 0.0,"
                "  active BOOLEAN"
                ")"
            ))
            conn.execute(sa.text(
                "CREATE INDEX idx_test_name ON test_table (name)"
            ))
            conn.commit()
        engine.dispose()

        await gw._action_connect({
            "driver": "sqlite",
            "database": db_path,
            "connection_id": "intro",
        })

        result = await gw._action_introspect({
            "connection_id": "intro",
            "refresh": True,
        })

        test_table = next(t for t in result["tables"] if t["name"] == "test_table")
        col_names = [c["name"] for c in test_table["columns"]]
        assert "id" in col_names
        assert "name" in col_names
        assert "value" in col_names

        # Check PK
        id_col = next(c for c in test_table["columns"] if c["name"] == "id")
        assert id_col["primary_key"] is True

        # Check index
        assert len(test_table["indexes"]) >= 1

        await gw._action_disconnect({"connection_id": "intro"})


# ---------------------------------------------------------------------------
# Tests — Multi-connection
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestMultiConnection:
    @pytest.mark.asyncio
    async def test_two_databases_simultaneously(self, gw, tmp_path) -> None:
        import sqlalchemy as sa

        # Create two separate databases
        for name in ["db1", "db2"]:
            db_path = str(tmp_path / f"{name}.db")
            engine = sa.create_engine(f"sqlite:///{db_path}")
            with engine.connect() as conn:
                conn.execute(sa.text(
                    f"CREATE TABLE items (id INTEGER PRIMARY KEY, label TEXT)"
                ))
                conn.execute(sa.text(
                    f"INSERT INTO items (label) VALUES ('{name}_item1'), ('{name}_item2')"
                ))
                conn.commit()
            engine.dispose()

        # Connect both
        await gw._action_connect({
            "driver": "sqlite",
            "database": str(tmp_path / "db1.db"),
            "connection_id": "conn1",
        })
        await gw._action_connect({
            "driver": "sqlite",
            "database": str(tmp_path / "db2.db"),
            "connection_id": "conn2",
        })

        # Query each independently
        r1 = await gw._action_find({
            "entity": "items",
            "connection_id": "conn1",
        })
        r2 = await gw._action_find({
            "entity": "items",
            "connection_id": "conn2",
        })

        assert r1["rows"][0]["label"].startswith("db1_")
        assert r2["rows"][0]["label"].startswith("db2_")

        # Context snippet should mention both
        snippet = gw.get_context_snippet()
        assert "conn1" in snippet
        assert "conn2" in snippet

        await gw._action_disconnect({"connection_id": "conn1"})
        await gw._action_disconnect({"connection_id": "conn2"})


# ---------------------------------------------------------------------------
# Tests — Error handling
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_query_nonexistent_table(self, gw, tmp_path) -> None:
        from llmos_bridge.exceptions import ActionExecutionError

        db_path = str(tmp_path / "error.db")
        await gw._action_connect({
            "driver": "sqlite",
            "database": db_path,
            "connection_id": "error",
        })

        with pytest.raises(ActionExecutionError, match="Unknown entity"):
            await gw._action_find({
                "entity": "nonexistent",
                "connection_id": "error",
            })

        await gw._action_disconnect({"connection_id": "error"})

    @pytest.mark.asyncio
    async def test_bad_filter_column(self, gw, tmp_path) -> None:
        import sqlalchemy as sa
        from llmos_bridge.exceptions import ActionExecutionError

        db_path = str(tmp_path / "badfilter.db")
        engine = sa.create_engine(f"sqlite:///{db_path}")
        with engine.connect() as conn:
            conn.execute(sa.text("CREATE TABLE t (id INTEGER PRIMARY KEY, x TEXT)"))
            conn.commit()
        engine.dispose()

        await gw._action_connect({
            "driver": "sqlite",
            "database": db_path,
            "connection_id": "bf",
        })

        with pytest.raises(ActionExecutionError):
            await gw._action_find({
                "entity": "t",
                "filter": {"nonexistent_column": "val"},
                "connection_id": "bf",
            })

        await gw._action_disconnect({"connection_id": "bf"})
