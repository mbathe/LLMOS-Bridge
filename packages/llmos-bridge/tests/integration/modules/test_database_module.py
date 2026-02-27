"""Integration tests — DatabaseModule against real SQLite files."""

from __future__ import annotations

from pathlib import Path

import pytest

from llmos_bridge.modules.database import DatabaseModule


@pytest.fixture
def module() -> DatabaseModule:
    return DatabaseModule()


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    return str(tmp_path / "integration_test.db")


@pytest.mark.integration
class TestFullWorkflow:
    """End-to-end workflow: connect → create → insert → query → update → delete → disconnect."""

    @pytest.mark.asyncio
    async def test_complete_crud_lifecycle(self, module: DatabaseModule, db_path: str) -> None:
        # 1. Connect
        conn = await module._action_connect({
            "driver": "sqlite", "database": db_path, "connection_id": "test",
        })
        assert conn["status"] == "connected"

        # 2. Create table
        await module._action_create_table({
            "table": "products",
            "columns": [
                {"name": "id", "type": "INTEGER PRIMARY KEY AUTOINCREMENT"},
                {"name": "name", "type": "TEXT NOT NULL"},
                {"name": "price", "type": "REAL"},
                {"name": "stock", "type": "INTEGER DEFAULT 0"},
            ],
            "connection_id": "test",
        })

        # 3. Insert records
        for name, price, stock in [("Widget", 9.99, 100), ("Gadget", 19.99, 50), ("Doohickey", 4.99, 200)]:
            await module._action_insert_record({
                "table": "products",
                "record": {"name": name, "price": price, "stock": stock},
                "connection_id": "test",
            })

        # 4. Fetch all
        result = await module._action_fetch_results({
            "sql": "SELECT * FROM products ORDER BY name",
            "connection_id": "test",
        })
        assert result["row_count"] == 3
        assert result["rows"][0]["name"] == "Doohickey"

        # 5. Update a record
        update = await module._action_update_record({
            "table": "products",
            "values": {"price": 24.99, "stock": 75},
            "where": {"name": "Gadget"},
            "connection_id": "test",
        })
        assert update["rows_affected"] == 1

        # 6. Verify update
        check = await module._action_fetch_results({
            "sql": "SELECT price, stock FROM products WHERE name = ?",
            "params": ["Gadget"],
            "connection_id": "test",
        })
        assert check["rows"][0]["price"] == 24.99
        assert check["rows"][0]["stock"] == 75

        # 7. Delete a record
        delete = await module._action_delete_record({
            "table": "products",
            "where": {"name": "Doohickey"},
            "confirm": True,
            "connection_id": "test",
        })
        assert delete["rows_deleted"] == 1

        # 8. List tables
        tables = await module._action_list_tables({"connection_id": "test"})
        assert "products" in tables["tables"]

        # 9. Get schema
        schema = await module._action_get_table_schema({
            "table": "products", "connection_id": "test",
        })
        assert schema["column_count"] == 4
        col_names = [c["name"] for c in schema["columns"]]
        assert "id" in col_names
        assert "stock" in col_names

        # 10. Disconnect
        disc = await module._action_disconnect({"connection_id": "test"})
        assert disc["status"] == "disconnected"


@pytest.mark.integration
class TestTransactionsWithFile:
    @pytest.mark.asyncio
    async def test_transaction_commit_persists(self, module: DatabaseModule, db_path: str) -> None:
        await module._action_connect({"driver": "sqlite", "database": db_path})
        await module._action_execute_query({
            "sql": "CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)",
        })

        # Start transaction, insert, commit
        await module._action_begin_transaction({})
        await module._action_execute_query({
            "sql": "INSERT INTO t (v) VALUES (?)", "params": ["committed"],
        })
        await module._action_commit_transaction({})
        await module._action_disconnect({})

        # Reconnect and verify
        await module._action_connect({"driver": "sqlite", "database": db_path})
        result = await module._action_fetch_results({"sql": "SELECT v FROM t"})
        assert result["row_count"] == 1
        assert result["rows"][0]["v"] == "committed"
        await module._action_disconnect({})

    @pytest.mark.asyncio
    async def test_transaction_rollback_does_not_persist(self, module: DatabaseModule, db_path: str) -> None:
        await module._action_connect({"driver": "sqlite", "database": db_path})
        await module._action_execute_query({
            "sql": "CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)",
        })
        await module._action_execute_query({
            "sql": "INSERT INTO t (v) VALUES (?)", "params": ["existing"],
        })

        # Start transaction, insert, rollback
        await module._action_begin_transaction({})
        await module._action_execute_query({
            "sql": "INSERT INTO t (v) VALUES (?)", "params": ["rolled_back"],
        })
        await module._action_rollback_transaction({})
        await module._action_disconnect({})

        # Reconnect and verify only "existing" is there
        await module._action_connect({"driver": "sqlite", "database": db_path})
        result = await module._action_fetch_results({"sql": "SELECT v FROM t ORDER BY v"})
        assert result["row_count"] == 1
        assert result["rows"][0]["v"] == "existing"
        await module._action_disconnect({})


@pytest.mark.integration
class TestMultipleConnections:
    @pytest.mark.asyncio
    async def test_two_connections_to_different_files(self, module: DatabaseModule, tmp_path: Path) -> None:
        db1 = str(tmp_path / "db1.db")
        db2 = str(tmp_path / "db2.db")

        await module._action_connect({"driver": "sqlite", "database": db1, "connection_id": "c1"})
        await module._action_connect({"driver": "sqlite", "database": db2, "connection_id": "c2"})

        await module._action_execute_query({
            "sql": "CREATE TABLE t1 (x TEXT)", "connection_id": "c1",
        })
        await module._action_execute_query({
            "sql": "CREATE TABLE t2 (y TEXT)", "connection_id": "c2",
        })

        t1 = await module._action_list_tables({"connection_id": "c1"})
        t2 = await module._action_list_tables({"connection_id": "c2"})

        assert "t1" in t1["tables"]
        assert "t2" not in t1["tables"]
        assert "t2" in t2["tables"]
        assert "t1" not in t2["tables"]

        await module._action_disconnect({"connection_id": "c1"})
        await module._action_disconnect({"connection_id": "c2"})


@pytest.mark.integration
class TestFileCreation:
    @pytest.mark.asyncio
    async def test_connect_creates_parent_dirs(self, module: DatabaseModule, tmp_path: Path) -> None:
        db_path = str(tmp_path / "nested" / "dirs" / "test.db")
        result = await module._action_connect({"driver": "sqlite", "database": db_path})
        assert result["status"] == "connected"
        assert Path(db_path).parent.exists()
        await module._action_disconnect({})

    @pytest.mark.asyncio
    async def test_wal_mode_enabled(self, module: DatabaseModule, db_path: str) -> None:
        await module._action_connect({"driver": "sqlite", "database": db_path})
        result = await module._action_fetch_results({
            "sql": "PRAGMA journal_mode",
        })
        assert result["rows"][0]["journal_mode"] == "wal"
        await module._action_disconnect({})


@pytest.mark.integration
class TestModuleExecuteInterface:
    """Test via BaseModule.execute() dispatch (not direct _action_ calls)."""

    @pytest.mark.asyncio
    async def test_execute_connect_and_query(self, module: DatabaseModule, db_path: str) -> None:
        await module.execute("connect", {"driver": "sqlite", "database": db_path})
        await module.execute("execute_query", {
            "sql": "CREATE TABLE test (id INTEGER, name TEXT)",
        })
        await module.execute("insert_record", {
            "table": "test", "record": {"id": 1, "name": "Test"},
        })
        result = await module.execute("fetch_results", {
            "sql": "SELECT * FROM test",
        })
        assert result["row_count"] == 1
        await module.execute("disconnect", {})
