"""Database module — Implementation.

Covers:
  - Connection management (connect/disconnect) with named connection IDs
  - Direct SQL execution (DDL, DML) with parameterised queries
  - SELECT queries with row-limit and column metadata
  - Convenience CRUD: insert_record, update_record, delete_record
  - Schema introspection: list_tables, get_table_schema, create_table
  - Transactions: begin, commit, rollback

Supported drivers:
  - ``sqlite``       — stdlib ``sqlite3``; always available
  - ``postgresql``   — requires ``psycopg2`` (optional extra)
  - ``mysql``        — requires ``mysql-connector-python`` (optional extra)

All blocking I/O runs in ``asyncio.to_thread`` to avoid starving the event loop.
Connections are protected by a ``threading.Lock`` per connection_id since multiple
plan actions may share the same connection concurrently.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from llmos_bridge.exceptions import ActionExecutionError
from llmos_bridge.modules.base import BaseModule, Platform
from llmos_bridge.security.decorators import audit_trail, requires_permission, sensitive_action
from llmos_bridge.security.models import Permission, RiskLevel
from llmos_bridge.modules.manifest import ActionSpec, ModuleManifest, ParamSpec
from llmos_bridge.protocol.params.database import (
    BeginTransactionParams,
    CommitTransactionParams,
    ConnectParams,
    CreateTableParams,
    DeleteRecordParams,
    DisconnectParams,
    ExecuteQueryParams,
    FetchResultsParams,
    GetTableSchemaParams,
    InsertRecordParams,
    ListTablesParams,
    RollbackTransactionParams,
    UpdateRecordParams,
)


class DatabaseModule(BaseModule):
    MODULE_ID = "database"
    VERSION = "1.0.0"
    SUPPORTED_PLATFORMS = [Platform.ALL]

    def __init__(self) -> None:
        # connection_id -> (driver, connection_object)
        self._connections: dict[str, tuple[str, Any]] = {}
        # Per-connection locks (threading because we use to_thread).
        self._conn_locks: dict[str, threading.Lock] = {}
        self._meta_lock = threading.Lock()
        super().__init__()

    # ------------------------------------------------------------------
    # Lock helpers
    # ------------------------------------------------------------------

    def _get_conn_lock(self, connection_id: str) -> threading.Lock:
        with self._meta_lock:
            if connection_id not in self._conn_locks:
                self._conn_locks[connection_id] = threading.Lock()
            return self._conn_locks[connection_id]

    def _get_connection(self, connection_id: str) -> tuple[str, Any]:
        conn = self._connections.get(connection_id)
        if conn is None:
            raise ActionExecutionError(
                module_id=self.MODULE_ID,
                action="",
                cause=RuntimeError(
                    f"No active connection with id '{connection_id}'. "
                    "Use the 'connect' action first."
                ),
            )
        return conn

    # ------------------------------------------------------------------
    # Actions — Connection management
    # ------------------------------------------------------------------

    @requires_permission(Permission.DATABASE_READ, reason="Opens database connection")
    async def _action_connect(self, params: dict[str, Any]) -> dict[str, Any]:
        p = ConnectParams.model_validate(params)

        def _inner() -> dict[str, Any]:
            with self._get_conn_lock(p.connection_id):
                # Close existing connection with same id if any.
                if p.connection_id in self._connections:
                    old_driver, old_conn = self._connections.pop(p.connection_id)
                    try:
                        old_conn.close()
                    except Exception:
                        pass

                if p.driver == "sqlite":
                    conn = self._connect_sqlite(p)
                elif p.driver == "postgresql":
                    conn = self._connect_postgresql(p)
                elif p.driver == "mysql":
                    conn = self._connect_mysql(p)
                else:
                    raise ValueError(f"Unsupported driver: {p.driver}")

                self._connections[p.connection_id] = (p.driver, conn)
                return {
                    "connection_id": p.connection_id,
                    "driver": p.driver,
                    "database": p.database,
                    "status": "connected",
                }

        return await asyncio.to_thread(_inner)

    def _connect_sqlite(self, p: ConnectParams) -> sqlite3.Connection:
        db_path = p.database
        if db_path != ":memory:":
            path = Path(db_path).resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            db_path = str(path)
        # isolation_level=None → autocommit mode; we manage transactions
        # explicitly via BEGIN/COMMIT/ROLLBACK.
        conn = sqlite3.connect(db_path, timeout=p.timeout, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _connect_postgresql(self, p: ConnectParams) -> Any:
        try:
            import psycopg2  # noqa: PLC0415
            import psycopg2.extras  # noqa: PLC0415
        except ImportError as exc:
            raise ActionExecutionError(
                module_id=self.MODULE_ID,
                action="connect",
                cause=RuntimeError(
                    "psycopg2 is required for PostgreSQL: "
                    "pip install 'psycopg2-binary'"
                ),
            ) from exc

        port = p.port or 5432
        conn = psycopg2.connect(
            host=p.host,
            port=port,
            dbname=p.database,
            user=p.user,
            password=p.password,
            connect_timeout=p.timeout,
        )
        conn.autocommit = True
        return conn

    def _connect_mysql(self, p: ConnectParams) -> Any:
        try:
            import mysql.connector  # noqa: PLC0415
        except ImportError as exc:
            raise ActionExecutionError(
                module_id=self.MODULE_ID,
                action="connect",
                cause=RuntimeError(
                    "mysql-connector-python is required for MySQL: "
                    "pip install 'mysql-connector-python'"
                ),
            ) from exc

        port = p.port or 3306
        conn = mysql.connector.connect(
            host=p.host,
            port=port,
            database=p.database,
            user=p.user,
            password=p.password,
            connection_timeout=p.timeout,
        )
        conn.autocommit = True
        return conn

    async def _action_disconnect(self, params: dict[str, Any]) -> dict[str, Any]:
        p = DisconnectParams.model_validate(params)

        def _inner() -> dict[str, Any]:
            with self._get_conn_lock(p.connection_id):
                entry = self._connections.pop(p.connection_id, None)
                if entry is None:
                    return {
                        "connection_id": p.connection_id,
                        "status": "not_connected",
                    }
                _driver, conn = entry
                try:
                    conn.close()
                except Exception:
                    pass
                return {
                    "connection_id": p.connection_id,
                    "status": "disconnected",
                }

        return await asyncio.to_thread(_inner)

    # ------------------------------------------------------------------
    # Actions — Query execution
    # ------------------------------------------------------------------

    @audit_trail("detailed")
    @sensitive_action(RiskLevel.HIGH)
    @requires_permission(Permission.DATABASE_WRITE, reason="Executes SQL query")
    async def _action_execute_query(self, params: dict[str, Any]) -> dict[str, Any]:
        p = ExecuteQueryParams.model_validate(params)

        def _inner() -> dict[str, Any]:
            with self._get_conn_lock(p.connection_id):
                driver, conn = self._get_connection(p.connection_id)
                cursor = conn.cursor()
                try:
                    start = time.monotonic()
                    cursor.execute(p.sql, p.params or [])
                    elapsed = time.monotonic() - start

                    rowcount = cursor.rowcount if cursor.rowcount >= 0 else 0
                    # For SQLite, commit unless in an explicit transaction.
                    if driver == "sqlite" and not self._in_transaction(conn, driver):
                        conn.commit()

                    return {
                        "rows_affected": rowcount,
                        "elapsed_ms": round(elapsed * 1000, 2),
                        "connection_id": p.connection_id,
                    }
                finally:
                    cursor.close()

        return await asyncio.to_thread(_inner)

    @requires_permission(Permission.DATABASE_READ, reason="Fetches query results")
    async def _action_fetch_results(self, params: dict[str, Any]) -> dict[str, Any]:
        p = FetchResultsParams.model_validate(params)

        def _inner() -> dict[str, Any]:
            with self._get_conn_lock(p.connection_id):
                driver, conn = self._get_connection(p.connection_id)
                cursor = conn.cursor()
                try:
                    start = time.monotonic()
                    cursor.execute(p.sql, p.params or [])
                    elapsed = time.monotonic() - start

                    columns = self._get_column_names(cursor, driver)
                    rows_raw = cursor.fetchmany(p.max_rows)
                    rows = [
                        dict(zip(columns, row)) for row in rows_raw
                    ]
                    total = len(rows)

                    return {
                        "columns": columns,
                        "rows": rows,
                        "row_count": total,
                        "truncated": total >= p.max_rows,
                        "elapsed_ms": round(elapsed * 1000, 2),
                        "connection_id": p.connection_id,
                    }
                finally:
                    cursor.close()

        return await asyncio.to_thread(_inner)

    # ------------------------------------------------------------------
    # Actions — CRUD convenience
    # ------------------------------------------------------------------

    @audit_trail("standard")
    @requires_permission(Permission.DATABASE_WRITE, reason="Inserts database record")
    async def _action_insert_record(self, params: dict[str, Any]) -> dict[str, Any]:
        p = InsertRecordParams.model_validate(params)

        def _inner() -> dict[str, Any]:
            with self._get_conn_lock(p.connection_id):
                driver, conn = self._get_connection(p.connection_id)

                columns = list(p.record.keys())
                values = list(p.record.values())
                placeholders = self._placeholders(driver, len(columns))

                conflict_clause = ""
                if p.on_conflict == "ignore":
                    conflict_clause = " OR IGNORE" if driver == "sqlite" else ""
                elif p.on_conflict == "replace":
                    conflict_clause = " OR REPLACE" if driver == "sqlite" else ""

                col_str = ", ".join(columns)
                sql = f"INSERT{conflict_clause} INTO {p.table} ({col_str}) VALUES ({placeholders})"

                cursor = conn.cursor()
                try:
                    cursor.execute(sql, values)
                    lastrowid = cursor.lastrowid
                    if driver == "sqlite" and not self._in_transaction(conn, driver):
                        conn.commit()
                    return {
                        "table": p.table,
                        "inserted": True,
                        "last_row_id": lastrowid,
                        "connection_id": p.connection_id,
                    }
                finally:
                    cursor.close()

        return await asyncio.to_thread(_inner)

    @audit_trail("standard")
    @requires_permission(Permission.DATABASE_WRITE, reason="Updates database record")
    async def _action_update_record(self, params: dict[str, Any]) -> dict[str, Any]:
        p = UpdateRecordParams.model_validate(params)

        def _inner() -> dict[str, Any]:
            with self._get_conn_lock(p.connection_id):
                driver, conn = self._get_connection(p.connection_id)

                set_cols = list(p.values.keys())
                set_vals = list(p.values.values())
                where_cols = list(p.where.keys())
                where_vals = list(p.where.values())

                set_clause = ", ".join(f"{c} = ?" for c in set_cols)
                where_clause = " AND ".join(f"{c} = ?" for c in where_cols)
                sql = f"UPDATE {p.table} SET {set_clause} WHERE {where_clause}"

                # Adapt placeholders for PostgreSQL/MySQL.
                sql = self._adapt_placeholders(sql, driver)
                all_values = set_vals + where_vals

                cursor = conn.cursor()
                try:
                    cursor.execute(sql, all_values)
                    rowcount = cursor.rowcount if cursor.rowcount >= 0 else 0
                    if driver == "sqlite" and not self._in_transaction(conn, driver):
                        conn.commit()
                    return {
                        "table": p.table,
                        "rows_affected": rowcount,
                        "connection_id": p.connection_id,
                    }
                finally:
                    cursor.close()

        return await asyncio.to_thread(_inner)

    @audit_trail("detailed")
    @sensitive_action(RiskLevel.HIGH, irreversible=True)
    @requires_permission(Permission.DATABASE_DELETE, reason="Deletes database record")
    async def _action_delete_record(self, params: dict[str, Any]) -> dict[str, Any]:
        p = DeleteRecordParams.model_validate(params)

        if not p.confirm:
            return {
                "table": p.table,
                "deleted": False,
                "reason": "confirm must be True to execute a DELETE.",
                "connection_id": p.connection_id,
            }

        def _inner() -> dict[str, Any]:
            with self._get_conn_lock(p.connection_id):
                driver, conn = self._get_connection(p.connection_id)

                where_cols = list(p.where.keys())
                where_vals = list(p.where.values())
                where_clause = " AND ".join(f"{c} = ?" for c in where_cols)
                sql = f"DELETE FROM {p.table} WHERE {where_clause}"
                sql = self._adapt_placeholders(sql, driver)

                cursor = conn.cursor()
                try:
                    cursor.execute(sql, where_vals)
                    rowcount = cursor.rowcount if cursor.rowcount >= 0 else 0
                    if driver == "sqlite" and not self._in_transaction(conn, driver):
                        conn.commit()
                    return {
                        "table": p.table,
                        "rows_deleted": rowcount,
                        "deleted": True,
                        "connection_id": p.connection_id,
                    }
                finally:
                    cursor.close()

        return await asyncio.to_thread(_inner)

    # ------------------------------------------------------------------
    # Actions — Schema introspection
    # ------------------------------------------------------------------

    @audit_trail("standard")
    @requires_permission(Permission.DATABASE_WRITE, reason="Creates database table")
    async def _action_create_table(self, params: dict[str, Any]) -> dict[str, Any]:
        p = CreateTableParams.model_validate(params)

        def _inner() -> dict[str, Any]:
            with self._get_conn_lock(p.connection_id):
                driver, conn = self._get_connection(p.connection_id)

                col_defs = ", ".join(
                    f"{col['name']} {col['type']}" for col in p.columns
                )
                exists = " IF NOT EXISTS" if p.if_not_exists else ""
                sql = f"CREATE TABLE{exists} {p.table} ({col_defs})"

                cursor = conn.cursor()
                try:
                    cursor.execute(sql)
                    if driver == "sqlite" and not self._in_transaction(conn, driver):
                        conn.commit()
                    return {
                        "table": p.table,
                        "created": True,
                        "connection_id": p.connection_id,
                    }
                finally:
                    cursor.close()

        return await asyncio.to_thread(_inner)

    @requires_permission(Permission.DATABASE_READ, reason="Lists database tables")
    async def _action_list_tables(self, params: dict[str, Any]) -> dict[str, Any]:
        p = ListTablesParams.model_validate(params)

        def _inner() -> dict[str, Any]:
            with self._get_conn_lock(p.connection_id):
                driver, conn = self._get_connection(p.connection_id)

                if driver == "sqlite":
                    sql = "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                elif driver == "postgresql":
                    schema = p.schema or "public"
                    sql = (
                        "SELECT table_name FROM information_schema.tables "
                        f"WHERE table_schema = '{schema}' ORDER BY table_name"
                    )
                elif driver == "mysql":
                    sql = "SHOW TABLES"
                else:
                    sql = "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"

                cursor = conn.cursor()
                try:
                    cursor.execute(sql)
                    rows = cursor.fetchall()
                    tables = [row[0] if isinstance(row, (tuple, list)) else list(row)[0] for row in rows]
                    return {
                        "tables": tables,
                        "count": len(tables),
                        "connection_id": p.connection_id,
                    }
                finally:
                    cursor.close()

        return await asyncio.to_thread(_inner)

    @requires_permission(Permission.DATABASE_READ, reason="Reads table schema")
    async def _action_get_table_schema(self, params: dict[str, Any]) -> dict[str, Any]:
        p = GetTableSchemaParams.model_validate(params)

        def _inner() -> dict[str, Any]:
            with self._get_conn_lock(p.connection_id):
                driver, conn = self._get_connection(p.connection_id)

                if driver == "sqlite":
                    return self._sqlite_table_schema(conn, p.table, p.connection_id)
                elif driver == "postgresql":
                    return self._pg_table_schema(conn, p.table, p.connection_id)
                elif driver == "mysql":
                    return self._mysql_table_schema(conn, p.table, p.connection_id)
                else:
                    return self._sqlite_table_schema(conn, p.table, p.connection_id)

        return await asyncio.to_thread(_inner)

    # ------------------------------------------------------------------
    # Actions — Transactions
    # ------------------------------------------------------------------

    @requires_permission(Permission.DATABASE_WRITE, reason="Begins transaction")
    async def _action_begin_transaction(self, params: dict[str, Any]) -> dict[str, Any]:
        p = BeginTransactionParams.model_validate(params)

        def _inner() -> dict[str, Any]:
            with self._get_conn_lock(p.connection_id):
                driver, conn = self._get_connection(p.connection_id)

                if driver == "sqlite":
                    level = p.isolation_level.upper()
                    if level not in ("DEFERRED", "IMMEDIATE", "EXCLUSIVE"):
                        level = "DEFERRED"
                    conn.execute(f"BEGIN {level}")
                elif driver == "postgresql":
                    conn.autocommit = False
                elif driver == "mysql":
                    conn.autocommit = False
                    conn.start_transaction()

                return {
                    "transaction": "started",
                    "isolation_level": p.isolation_level,
                    "connection_id": p.connection_id,
                }

        return await asyncio.to_thread(_inner)

    @audit_trail("standard")
    async def _action_commit_transaction(self, params: dict[str, Any]) -> dict[str, Any]:
        p = CommitTransactionParams.model_validate(params)

        def _inner() -> dict[str, Any]:
            with self._get_conn_lock(p.connection_id):
                driver, conn = self._get_connection(p.connection_id)
                conn.commit()
                # Restore autocommit for PostgreSQL/MySQL.
                if driver in ("postgresql", "mysql"):
                    conn.autocommit = True
                return {
                    "transaction": "committed",
                    "connection_id": p.connection_id,
                }

        return await asyncio.to_thread(_inner)

    @audit_trail("standard")
    async def _action_rollback_transaction(self, params: dict[str, Any]) -> dict[str, Any]:
        p = RollbackTransactionParams.model_validate(params)

        def _inner() -> dict[str, Any]:
            with self._get_conn_lock(p.connection_id):
                driver, conn = self._get_connection(p.connection_id)
                conn.rollback()
                if driver in ("postgresql", "mysql"):
                    conn.autocommit = True
                return {
                    "transaction": "rolled_back",
                    "connection_id": p.connection_id,
                }

        return await asyncio.to_thread(_inner)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _in_transaction(conn: Any, driver: str) -> bool:
        if driver == "sqlite":
            return conn.in_transaction
        elif driver == "postgresql":
            return not getattr(conn, "autocommit", True)
        elif driver == "mysql":
            return not getattr(conn, "autocommit", True)
        return False

    @staticmethod
    def _placeholders(driver: str, count: int) -> str:
        if driver == "postgresql":
            return ", ".join(f"%s" for _ in range(count))
        elif driver == "mysql":
            return ", ".join(f"%s" for _ in range(count))
        return ", ".join("?" for _ in range(count))

    @staticmethod
    def _adapt_placeholders(sql: str, driver: str) -> str:
        """Replace ``?`` placeholders with ``%s`` for PostgreSQL/MySQL."""
        if driver in ("postgresql", "mysql"):
            return sql.replace("?", "%s")
        return sql

    @staticmethod
    def _get_column_names(cursor: Any, driver: str) -> list[str]:
        if cursor.description is None:
            return []
        return [desc[0] for desc in cursor.description]

    @staticmethod
    def _sqlite_table_schema(conn: Any, table: str, connection_id: str) -> dict[str, Any]:
        cursor = conn.cursor()
        try:
            cursor.execute(f"PRAGMA table_info({table})")
            rows = cursor.fetchall()
            columns = []
            for row in rows:
                columns.append({
                    "name": row[1] if isinstance(row, (tuple, list)) else row["name"],
                    "type": row[2] if isinstance(row, (tuple, list)) else row["type"],
                    "nullable": not (row[3] if isinstance(row, (tuple, list)) else row["notnull"]),
                    "default": row[4] if isinstance(row, (tuple, list)) else row["dflt_value"],
                    "primary_key": bool(row[5] if isinstance(row, (tuple, list)) else row["pk"]),
                })
            return {
                "table": table,
                "columns": columns,
                "column_count": len(columns),
                "connection_id": connection_id,
            }
        finally:
            cursor.close()

    @staticmethod
    def _pg_table_schema(conn: Any, table: str, connection_id: str) -> dict[str, Any]:
        cursor = conn.cursor()
        try:
            cursor.execute(
                "SELECT column_name, data_type, is_nullable, column_default "
                "FROM information_schema.columns "
                "WHERE table_name = %s ORDER BY ordinal_position",
                (table,),
            )
            rows = cursor.fetchall()
            columns = [
                {
                    "name": row[0],
                    "type": row[1],
                    "nullable": row[2] == "YES",
                    "default": row[3],
                    "primary_key": False,
                }
                for row in rows
            ]
            return {
                "table": table,
                "columns": columns,
                "column_count": len(columns),
                "connection_id": connection_id,
            }
        finally:
            cursor.close()

    @staticmethod
    def _mysql_table_schema(conn: Any, table: str, connection_id: str) -> dict[str, Any]:
        cursor = conn.cursor()
        try:
            cursor.execute(f"DESCRIBE {table}")
            rows = cursor.fetchall()
            columns = [
                {
                    "name": row[0],
                    "type": row[1],
                    "nullable": row[2] == "YES",
                    "default": row[4],
                    "primary_key": row[3] == "PRI",
                }
                for row in rows
            ]
            return {
                "table": table,
                "columns": columns,
                "column_count": len(columns),
                "connection_id": connection_id,
            }
        finally:
            cursor.close()

    # ------------------------------------------------------------------
    # Manifest
    # ------------------------------------------------------------------

    def get_manifest(self) -> ModuleManifest:
        return ModuleManifest(
            module_id=self.MODULE_ID,
            version=self.VERSION,
            description=(
                "SQL database operations — connect, query, CRUD, schema introspection, "
                "and transaction management. Supports SQLite (built-in), PostgreSQL, and MySQL."
            ),
            platforms=["all"],
            declared_permissions=["database_access"],
            tags=["database", "sql", "sqlite", "postgresql", "mysql"],
            actions=[
                ActionSpec(
                    name="connect",
                    description="Open a database connection (SQLite, PostgreSQL, or MySQL).",
                    params=[
                        ParamSpec(name="driver", type="string", description="Database driver.", required=False, default="sqlite", enum=["sqlite", "postgresql", "mysql"]),
                        ParamSpec(name="database", type="string", description="Database name or file path."),
                        ParamSpec(name="host", type="string", description="Database host.", required=False, default="localhost"),
                        ParamSpec(name="port", type="integer", description="Database port.", required=False),
                        ParamSpec(name="user", type="string", description="Database user.", required=False),
                        ParamSpec(name="password", type="string", description="Database password.", required=False),
                        ParamSpec(name="connection_id", type="string", description="Logical connection name.", required=False, default="default"),
                        ParamSpec(name="timeout", type="integer", description="Connection timeout (seconds).", required=False, default=10),
                    ],
                    returns="object",
                    returns_description="Connection status with driver info.",
                    permission_required="local_worker",
                    examples=[{
                        "description": "Connect to a SQLite database",
                        "params": {"driver": "sqlite", "database": "/tmp/myapp.db"},
                    }],
                ),
                ActionSpec(
                    name="disconnect",
                    description="Close an active database connection.",
                    params=[
                        ParamSpec(name="connection_id", type="string", description="Connection to close.", required=False, default="default"),
                    ],
                    returns="object",
                    permission_required="local_worker",
                ),
                ActionSpec(
                    name="execute_query",
                    description="Execute a SQL statement (INSERT, UPDATE, DELETE, DDL). Returns rows affected.",
                    params=[
                        ParamSpec(name="sql", type="string", description="SQL statement to execute."),
                        ParamSpec(name="params", type="array", description="Query parameters.", required=False),
                        ParamSpec(name="connection_id", type="string", description="Connection to use.", required=False, default="default"),
                        ParamSpec(name="timeout", type="integer", description="Query timeout (seconds).", required=False, default=30),
                    ],
                    returns="object",
                    returns_description="Rows affected and elapsed time.",
                    permission_required="local_worker",
                    examples=[{
                        "description": "Insert a row",
                        "params": {"sql": "INSERT INTO users (name, email) VALUES (?, ?)", "params": ["Alice", "alice@example.com"]},
                    }],
                ),
                ActionSpec(
                    name="fetch_results",
                    description="Execute a SELECT query and return rows as list of dicts.",
                    params=[
                        ParamSpec(name="sql", type="string", description="SELECT query to execute."),
                        ParamSpec(name="params", type="array", description="Query parameters.", required=False),
                        ParamSpec(name="connection_id", type="string", description="Connection to use.", required=False, default="default"),
                        ParamSpec(name="max_rows", type="integer", description="Maximum rows to return.", required=False, default=1000),
                        ParamSpec(name="timeout", type="integer", description="Query timeout (seconds).", required=False, default=30),
                    ],
                    returns="object",
                    returns_description="Columns, rows as dicts, row count, and truncation flag.",
                    permission_required="readonly",
                    examples=[{
                        "description": "Fetch all users",
                        "params": {"sql": "SELECT * FROM users"},
                    }],
                ),
                ActionSpec(
                    name="insert_record",
                    description="Insert a record into a table using column-value mapping.",
                    params=[
                        ParamSpec(name="table", type="string", description="Target table name."),
                        ParamSpec(name="record", type="object", description="Column-to-value mapping."),
                        ParamSpec(name="connection_id", type="string", description="Connection to use.", required=False, default="default"),
                        ParamSpec(name="on_conflict", type="string", description="Conflict resolution.", required=False, default="error", enum=["error", "ignore", "replace"]),
                    ],
                    returns="object",
                    permission_required="local_worker",
                ),
                ActionSpec(
                    name="update_record",
                    description="Update records matching a WHERE clause.",
                    params=[
                        ParamSpec(name="table", type="string", description="Target table name."),
                        ParamSpec(name="values", type="object", description="Columns to update."),
                        ParamSpec(name="where", type="object", description="WHERE clause as column-value mapping."),
                        ParamSpec(name="connection_id", type="string", description="Connection to use.", required=False, default="default"),
                    ],
                    returns="object",
                    permission_required="local_worker",
                ),
                ActionSpec(
                    name="delete_record",
                    description="Delete records matching a WHERE clause. Requires confirm=true.",
                    params=[
                        ParamSpec(name="table", type="string", description="Target table name."),
                        ParamSpec(name="where", type="object", description="WHERE clause as column-value mapping."),
                        ParamSpec(name="connection_id", type="string", description="Connection to use.", required=False, default="default"),
                        ParamSpec(name="confirm", type="boolean", description="Safety flag — must be true to execute.", required=True),
                    ],
                    returns="object",
                    permission_required="power_user",
                ),
                ActionSpec(
                    name="create_table",
                    description="Create a new table with column definitions.",
                    params=[
                        ParamSpec(name="table", type="string", description="Table name."),
                        ParamSpec(name="columns", type="array", description='Column definitions: [{"name": "id", "type": "INTEGER PRIMARY KEY"}, ...].'),
                        ParamSpec(name="if_not_exists", type="boolean", description="Skip if table exists.", required=False, default=True),
                        ParamSpec(name="connection_id", type="string", description="Connection to use.", required=False, default="default"),
                    ],
                    returns="object",
                    permission_required="local_worker",
                ),
                ActionSpec(
                    name="list_tables",
                    description="List all tables in the connected database.",
                    params=[
                        ParamSpec(name="connection_id", type="string", description="Connection to use.", required=False, default="default"),
                        ParamSpec(name="schema", type="string", description="Schema name (PostgreSQL only).", required=False),
                    ],
                    returns="object",
                    returns_description="List of table names.",
                    permission_required="readonly",
                ),
                ActionSpec(
                    name="get_table_schema",
                    description="Get column definitions for a table.",
                    params=[
                        ParamSpec(name="table", type="string", description="Table name."),
                        ParamSpec(name="connection_id", type="string", description="Connection to use.", required=False, default="default"),
                    ],
                    returns="object",
                    returns_description="Column names, types, nullable, default, primary key.",
                    permission_required="readonly",
                ),
                ActionSpec(
                    name="begin_transaction",
                    description="Start an explicit transaction.",
                    params=[
                        ParamSpec(name="connection_id", type="string", description="Connection to use.", required=False, default="default"),
                        ParamSpec(name="isolation_level", type="string", description="Isolation level.", required=False, default="deferred", enum=["deferred", "immediate", "exclusive", "read_committed", "serializable"]),
                    ],
                    returns="object",
                    permission_required="local_worker",
                ),
                ActionSpec(
                    name="commit_transaction",
                    description="Commit the current transaction.",
                    params=[
                        ParamSpec(name="connection_id", type="string", description="Connection to use.", required=False, default="default"),
                    ],
                    returns="object",
                    permission_required="local_worker",
                ),
                ActionSpec(
                    name="rollback_transaction",
                    description="Roll back the current transaction.",
                    params=[
                        ParamSpec(name="connection_id", type="string", description="Connection to use.", required=False, default="default"),
                    ],
                    returns="object",
                    permission_required="local_worker",
                ),
            ],
        )
