"""Typed parameter models for the ``database`` module."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field


class ConnectParams(BaseModel):
    driver: Literal["sqlite", "postgresql", "mysql"] = "sqlite"
    database: str = Field(
        description=(
            "Database name or path. For SQLite, use a file path. "
            "For PostgreSQL/MySQL, use the database name."
        )
    )
    host: str = "localhost"
    port: int | None = None
    user: str | None = None
    password: str | None = None
    connection_id: str = Field(
        default="default",
        description="Logical name for this connection (reuse across actions in the plan).",
    )
    timeout: Annotated[int, Field(ge=1, le=60)] = 10


class DisconnectParams(BaseModel):
    connection_id: str = "default"


class ExecuteQueryParams(BaseModel):
    sql: str = Field(description="SQL statement to execute (INSERT, UPDATE, DELETE, DDL).")
    params: list[Any] | dict[str, Any] = Field(
        default_factory=list,
        description="Positional or named parameters for the query.",
    )
    connection_id: str = "default"
    timeout: Annotated[int, Field(ge=1, le=300)] = 30


class FetchResultsParams(BaseModel):
    sql: str = Field(description="SELECT query to execute.")
    params: list[Any] | dict[str, Any] = Field(default_factory=list)
    connection_id: str = "default"
    max_rows: Annotated[int, Field(ge=1, le=10_000)] = 1_000
    timeout: Annotated[int, Field(ge=1, le=300)] = 30


class InsertRecordParams(BaseModel):
    table: str
    record: dict[str, Any] = Field(description="Column-to-value mapping.")
    connection_id: str = "default"
    on_conflict: Literal["error", "ignore", "replace"] = "error"


class UpdateRecordParams(BaseModel):
    table: str
    values: dict[str, Any] = Field(description="Columns to update.")
    where: dict[str, Any] = Field(description="WHERE clause as column-to-value mapping.")
    connection_id: str = "default"


class DeleteRecordParams(BaseModel):
    table: str
    where: dict[str, Any] = Field(description="WHERE clause as column-to-value mapping.")
    connection_id: str = "default"
    confirm: bool = Field(
        default=False,
        description="Must be True to execute. Guards against accidental deletes.",
    )


class CreateTableParams(BaseModel):
    table: str
    columns: list[dict[str, str]] = Field(
        description=(
            "List of column definitions: [{'name': 'id', 'type': 'INTEGER PRIMARY KEY'}, ...]"
        )
    )
    if_not_exists: bool = True
    connection_id: str = "default"


class ListTablesParams(BaseModel):
    connection_id: str = "default"
    schema: str | None = None


class GetTableSchemaParams(BaseModel):
    table: str
    connection_id: str = "default"


class BeginTransactionParams(BaseModel):
    connection_id: str = "default"
    isolation_level: Literal["deferred", "immediate", "exclusive", "read_committed", "serializable"] = "deferred"


class CommitTransactionParams(BaseModel):
    connection_id: str = "default"


class RollbackTransactionParams(BaseModel):
    connection_id: str = "default"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

PARAMS_MAP: dict[str, type[BaseModel]] = {
    "connect": ConnectParams,
    "disconnect": DisconnectParams,
    "execute_query": ExecuteQueryParams,
    "fetch_results": FetchResultsParams,
    "insert_record": InsertRecordParams,
    "update_record": UpdateRecordParams,
    "delete_record": DeleteRecordParams,
    "create_table": CreateTableParams,
    "list_tables": ListTablesParams,
    "get_table_schema": GetTableSchemaParams,
    "begin_transaction": BeginTransactionParams,
    "commit_transaction": CommitTransactionParams,
    "rollback_transaction": RollbackTransactionParams,
}
