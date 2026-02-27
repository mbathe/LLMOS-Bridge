"""Unit tests — DatabaseModule (SQLite only, in-memory)."""

from __future__ import annotations

import pytest

from llmos_bridge.modules.database import DatabaseModule


@pytest.fixture
def module() -> DatabaseModule:
    return DatabaseModule()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _connect(module: DatabaseModule, connection_id: str = "default", db: str = ":memory:") -> dict:
    return await module._action_connect({
        "driver": "sqlite",
        "database": db,
        "connection_id": connection_id,
    })


async def _setup_table(module: DatabaseModule, connection_id: str = "default") -> None:
    """Create a test users table and insert two rows."""
    await module._action_execute_query({
        "sql": "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, email TEXT)",
        "connection_id": connection_id,
    })
    await module._action_execute_query({
        "sql": "INSERT INTO users (name, email) VALUES (?, ?)",
        "params": ["Alice", "alice@test.com"],
        "connection_id": connection_id,
    })
    await module._action_execute_query({
        "sql": "INSERT INTO users (name, email) VALUES (?, ?)",
        "params": ["Bob", "bob@test.com"],
        "connection_id": connection_id,
    })


# ---------------------------------------------------------------------------
# Tests — Connection management
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestConnect:
    @pytest.mark.asyncio
    async def test_connect_sqlite_memory(self, module: DatabaseModule) -> None:
        result = await _connect(module)
        assert result["status"] == "connected"
        assert result["driver"] == "sqlite"
        assert result["connection_id"] == "default"

    @pytest.mark.asyncio
    async def test_connect_sqlite_file(self, module: DatabaseModule, tmp_path) -> None:
        db_path = str(tmp_path / "test.db")
        result = await _connect(module, db=db_path)
        assert result["status"] == "connected"

    @pytest.mark.asyncio
    async def test_connect_replaces_existing(self, module: DatabaseModule) -> None:
        await _connect(module, connection_id="c1")
        result = await _connect(module, connection_id="c1")
        assert result["status"] == "connected"

    @pytest.mark.asyncio
    async def test_connect_multiple_ids(self, module: DatabaseModule) -> None:
        r1 = await _connect(module, connection_id="db1")
        r2 = await _connect(module, connection_id="db2")
        assert r1["connection_id"] == "db1"
        assert r2["connection_id"] == "db2"


@pytest.mark.unit
class TestDisconnect:
    @pytest.mark.asyncio
    async def test_disconnect_connected(self, module: DatabaseModule) -> None:
        await _connect(module)
        result = await module._action_disconnect({"connection_id": "default"})
        assert result["status"] == "disconnected"

    @pytest.mark.asyncio
    async def test_disconnect_not_connected(self, module: DatabaseModule) -> None:
        result = await module._action_disconnect({"connection_id": "nonexistent"})
        assert result["status"] == "not_connected"


# ---------------------------------------------------------------------------
# Tests — Execute query
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExecuteQuery:
    @pytest.mark.asyncio
    async def test_create_table(self, module: DatabaseModule) -> None:
        await _connect(module)
        result = await module._action_execute_query({
            "sql": "CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)",
        })
        assert "rows_affected" in result
        assert result["elapsed_ms"] >= 0

    @pytest.mark.asyncio
    async def test_insert_with_params(self, module: DatabaseModule) -> None:
        await _connect(module)
        await module._action_execute_query({
            "sql": "CREATE TABLE t (x TEXT)",
        })
        result = await module._action_execute_query({
            "sql": "INSERT INTO t (x) VALUES (?)",
            "params": ["hello"],
        })
        assert result["rows_affected"] == 1

    @pytest.mark.asyncio
    async def test_no_connection_raises(self, module: DatabaseModule) -> None:
        from llmos_bridge.exceptions import ActionExecutionError
        with pytest.raises(ActionExecutionError):
            await module._action_execute_query({
                "sql": "SELECT 1",
                "connection_id": "missing",
            })


# ---------------------------------------------------------------------------
# Tests — Fetch results
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFetchResults:
    @pytest.mark.asyncio
    async def test_fetch_all(self, module: DatabaseModule) -> None:
        await _connect(module)
        await _setup_table(module)
        result = await module._action_fetch_results({
            "sql": "SELECT * FROM users ORDER BY id",
        })
        assert result["row_count"] == 2
        assert result["columns"] == ["id", "name", "email"]
        assert result["rows"][0]["name"] == "Alice"
        assert result["rows"][1]["name"] == "Bob"
        assert result["truncated"] is False

    @pytest.mark.asyncio
    async def test_fetch_with_where(self, module: DatabaseModule) -> None:
        await _connect(module)
        await _setup_table(module)
        result = await module._action_fetch_results({
            "sql": "SELECT name FROM users WHERE email = ?",
            "params": ["alice@test.com"],
        })
        assert result["row_count"] == 1
        assert result["rows"][0]["name"] == "Alice"

    @pytest.mark.asyncio
    async def test_fetch_max_rows(self, module: DatabaseModule) -> None:
        await _connect(module)
        await _setup_table(module)
        result = await module._action_fetch_results({
            "sql": "SELECT * FROM users",
            "max_rows": 1,
        })
        assert result["row_count"] == 1
        assert result["truncated"] is True

    @pytest.mark.asyncio
    async def test_fetch_empty(self, module: DatabaseModule) -> None:
        await _connect(module)
        await module._action_execute_query({
            "sql": "CREATE TABLE empty_t (id INTEGER)",
        })
        result = await module._action_fetch_results({
            "sql": "SELECT * FROM empty_t",
        })
        assert result["row_count"] == 0
        assert result["rows"] == []


# ---------------------------------------------------------------------------
# Tests — CRUD convenience
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestInsertRecord:
    @pytest.mark.asyncio
    async def test_insert(self, module: DatabaseModule) -> None:
        await _connect(module)
        await module._action_execute_query({
            "sql": "CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT, price REAL)",
        })
        result = await module._action_insert_record({
            "table": "products",
            "record": {"name": "Widget", "price": 9.99},
        })
        assert result["inserted"] is True
        assert result["last_row_id"] is not None

    @pytest.mark.asyncio
    async def test_insert_or_ignore(self, module: DatabaseModule) -> None:
        await _connect(module)
        await module._action_execute_query({
            "sql": "CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)",
        })
        await module._action_insert_record({
            "table": "t", "record": {"id": 1, "v": "a"},
        })
        result = await module._action_insert_record({
            "table": "t", "record": {"id": 1, "v": "b"}, "on_conflict": "ignore",
        })
        assert result["inserted"] is True

    @pytest.mark.asyncio
    async def test_insert_or_replace(self, module: DatabaseModule) -> None:
        await _connect(module)
        await module._action_execute_query({
            "sql": "CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)",
        })
        await module._action_insert_record({
            "table": "t", "record": {"id": 1, "v": "a"},
        })
        await module._action_insert_record({
            "table": "t", "record": {"id": 1, "v": "b"}, "on_conflict": "replace",
        })
        fetch = await module._action_fetch_results({"sql": "SELECT v FROM t WHERE id=1"})
        assert fetch["rows"][0]["v"] == "b"


@pytest.mark.unit
class TestUpdateRecord:
    @pytest.mark.asyncio
    async def test_update(self, module: DatabaseModule) -> None:
        await _connect(module)
        await _setup_table(module)
        result = await module._action_update_record({
            "table": "users",
            "values": {"email": "newalice@test.com"},
            "where": {"name": "Alice"},
        })
        assert result["rows_affected"] == 1
        # Verify update
        fetch = await module._action_fetch_results({
            "sql": "SELECT email FROM users WHERE name='Alice'",
        })
        assert fetch["rows"][0]["email"] == "newalice@test.com"

    @pytest.mark.asyncio
    async def test_update_no_match(self, module: DatabaseModule) -> None:
        await _connect(module)
        await _setup_table(module)
        result = await module._action_update_record({
            "table": "users",
            "values": {"email": "x"},
            "where": {"name": "NoOne"},
        })
        assert result["rows_affected"] == 0


@pytest.mark.unit
class TestDeleteRecord:
    @pytest.mark.asyncio
    async def test_delete_with_confirm(self, module: DatabaseModule) -> None:
        await _connect(module)
        await _setup_table(module)
        result = await module._action_delete_record({
            "table": "users",
            "where": {"name": "Bob"},
            "confirm": True,
        })
        assert result["deleted"] is True
        assert result["rows_deleted"] == 1

    @pytest.mark.asyncio
    async def test_delete_without_confirm(self, module: DatabaseModule) -> None:
        await _connect(module)
        await _setup_table(module)
        result = await module._action_delete_record({
            "table": "users",
            "where": {"name": "Bob"},
            "confirm": False,
        })
        assert result["deleted"] is False
        assert "confirm" in result["reason"].lower()


# ---------------------------------------------------------------------------
# Tests — Schema introspection
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateTable:
    @pytest.mark.asyncio
    async def test_create_table(self, module: DatabaseModule) -> None:
        await _connect(module)
        result = await module._action_create_table({
            "table": "orders",
            "columns": [
                {"name": "id", "type": "INTEGER PRIMARY KEY"},
                {"name": "product", "type": "TEXT NOT NULL"},
                {"name": "quantity", "type": "INTEGER DEFAULT 1"},
            ],
        })
        assert result["created"] is True

    @pytest.mark.asyncio
    async def test_create_table_if_not_exists(self, module: DatabaseModule) -> None:
        await _connect(module)
        await module._action_create_table({
            "table": "t",
            "columns": [{"name": "id", "type": "INTEGER"}],
        })
        # Should not raise
        result = await module._action_create_table({
            "table": "t",
            "columns": [{"name": "id", "type": "INTEGER"}],
            "if_not_exists": True,
        })
        assert result["created"] is True


@pytest.mark.unit
class TestListTables:
    @pytest.mark.asyncio
    async def test_list_tables(self, module: DatabaseModule) -> None:
        await _connect(module)
        await _setup_table(module)
        await module._action_execute_query({
            "sql": "CREATE TABLE orders (id INTEGER)",
        })
        result = await module._action_list_tables({})
        assert "users" in result["tables"]
        assert "orders" in result["tables"]
        assert result["count"] >= 2

    @pytest.mark.asyncio
    async def test_list_tables_empty(self, module: DatabaseModule) -> None:
        await _connect(module)
        result = await module._action_list_tables({})
        assert result["tables"] == []


@pytest.mark.unit
class TestGetTableSchema:
    @pytest.mark.asyncio
    async def test_get_schema(self, module: DatabaseModule) -> None:
        await _connect(module)
        await _setup_table(module)
        result = await module._action_get_table_schema({"table": "users"})
        assert result["table"] == "users"
        assert result["column_count"] == 3
        names = [c["name"] for c in result["columns"]]
        assert "id" in names
        assert "name" in names
        assert "email" in names
        # Check id is primary key
        id_col = next(c for c in result["columns"] if c["name"] == "id")
        assert id_col["primary_key"] is True


# ---------------------------------------------------------------------------
# Tests — Transactions
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTransactions:
    @pytest.mark.asyncio
    async def test_begin_commit(self, module: DatabaseModule) -> None:
        await _connect(module)
        await _setup_table(module)
        await module._action_begin_transaction({})
        await module._action_execute_query({
            "sql": "INSERT INTO users (name, email) VALUES (?, ?)",
            "params": ["Charlie", "charlie@test.com"],
        })
        result = await module._action_commit_transaction({})
        assert result["transaction"] == "committed"
        # Verify committed
        fetch = await module._action_fetch_results({
            "sql": "SELECT * FROM users",
        })
        assert fetch["row_count"] == 3

    @pytest.mark.asyncio
    async def test_begin_rollback(self, module: DatabaseModule) -> None:
        await _connect(module)
        await _setup_table(module)
        await module._action_begin_transaction({})
        await module._action_execute_query({
            "sql": "INSERT INTO users (name, email) VALUES (?, ?)",
            "params": ["Charlie", "charlie@test.com"],
        })
        result = await module._action_rollback_transaction({})
        assert result["transaction"] == "rolled_back"
        # Verify rolled back
        fetch = await module._action_fetch_results({
            "sql": "SELECT * FROM users",
        })
        assert fetch["row_count"] == 2

    @pytest.mark.asyncio
    async def test_begin_immediate(self, module: DatabaseModule) -> None:
        await _connect(module)
        result = await module._action_begin_transaction({
            "isolation_level": "immediate",
        })
        assert result["isolation_level"] == "immediate"
        await module._action_rollback_transaction({})


# ---------------------------------------------------------------------------
# Tests — Manifest
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestManifest:
    def test_manifest_actions(self, module: DatabaseModule) -> None:
        manifest = module.get_manifest()
        assert manifest.module_id == "database"
        assert manifest.version == "1.0.0"
        action_names = manifest.action_names()
        assert len(action_names) == 13
        for expected in [
            "connect", "disconnect", "execute_query", "fetch_results",
            "insert_record", "update_record", "delete_record",
            "create_table", "list_tables", "get_table_schema",
            "begin_transaction", "commit_transaction", "rollback_transaction",
        ]:
            assert expected in action_names, f"Missing action: {expected}"

    def test_manifest_permissions(self, module: DatabaseModule) -> None:
        manifest = module.get_manifest()
        assert "database_access" in manifest.declared_permissions

    def test_action_schemas(self, module: DatabaseModule) -> None:
        manifest = module.get_manifest()
        connect = manifest.get_action("connect")
        assert connect is not None
        schema = connect.to_json_schema()
        assert "database" in schema["properties"]
        assert "driver" in schema["properties"]


# ---------------------------------------------------------------------------
# Tests — Module basics
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestModuleBasics:
    def test_module_id(self, module: DatabaseModule) -> None:
        assert module.MODULE_ID == "database"

    def test_supported_platforms(self, module: DatabaseModule) -> None:
        assert module.is_supported_on_current_platform()

    @pytest.mark.asyncio
    async def test_execute_dispatch(self, module: DatabaseModule) -> None:
        """BaseModule.execute dispatches to _action_connect."""
        await _connect(module)
        result = await module.execute("list_tables", {})
        assert "tables" in result

    @pytest.mark.asyncio
    async def test_execute_unknown_action(self, module: DatabaseModule) -> None:
        from llmos_bridge.exceptions import ActionNotFoundError
        with pytest.raises(ActionNotFoundError):
            await module.execute("nonexistent_action", {})


# ---------------------------------------------------------------------------
# Tests — Connection lock safety
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestConnectionLocks:
    def test_lock_creation(self, module: DatabaseModule) -> None:
        lock1 = module._get_conn_lock("a")
        lock2 = module._get_conn_lock("a")
        assert lock1 is lock2

    def test_different_connection_ids_get_different_locks(self, module: DatabaseModule) -> None:
        lock1 = module._get_conn_lock("a")
        lock2 = module._get_conn_lock("b")
        assert lock1 is not lock2
