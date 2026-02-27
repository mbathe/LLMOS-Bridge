"""Typed parameter models for the ``db_gateway`` module."""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, Field


class GatewayConnectParams(BaseModel):
    """Connect via URL or individual params."""

    url: str | None = Field(
        default=None,
        description=(
            "Full connection URL, e.g. 'sqlite:///mydb.db' or "
            "'postgresql://user:pass@host:5432/dbname'. "
            "If provided, driver/host/port/database/user/password are ignored."
        ),
    )
    driver: str = Field(
        default="sqlite",
        description=(
            "Database driver name. Built-in: sqlite, postgresql, mysql. "
            "Community drivers registered via register_sql_driver()."
        ),
    )
    database: str = Field(
        default="",
        description="Database name or file path (for SQLite).",
    )
    host: str = "localhost"
    port: int | None = None
    user: str | None = None
    password: str | None = None
    connection_id: str = Field(
        default="default",
        description="Logical name for this connection.",
    )
    pool_size: Annotated[int, Field(ge=1, le=20)] = 5


class GatewayDisconnectParams(BaseModel):
    connection_id: str = "default"


class IntrospectParams(BaseModel):
    connection_id: str = "default"
    schema_name: str | None = Field(
        default=None,
        description="Schema name (PostgreSQL). None = default schema.",
    )
    refresh: bool = Field(
        default=False,
        description="Force re-introspection, bypassing the cache.",
    )


class FindParams(BaseModel):
    entity: str = Field(description="Table/entity name to query.")
    filter: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "MongoDB-like filter. Examples: "
            '{"age": {"$gte": 18}}, '
            '{"status": "active", "role": {"$in": ["admin","editor"]}}'
        ),
    )
    select: list[str] | None = Field(
        default=None,
        description="Columns to return (projection). None = all columns.",
    )
    order_by: list[str] | None = Field(
        default=None,
        description=(
            "Sort order. Prefix with '-' for descending. "
            'Examples: ["name"], ["-created_at", "name"]'
        ),
    )
    limit: Annotated[int, Field(ge=1, le=10_000)] = 100
    offset: int = Field(default=0, ge=0)
    connection_id: str = "default"


class FindOneParams(BaseModel):
    entity: str = Field(description="Table/entity name.")
    filter: dict[str, Any] = Field(
        default_factory=dict,
        description="MongoDB-like filter to find a single record.",
    )
    select: list[str] | None = None
    connection_id: str = "default"


class CreateParams(BaseModel):
    entity: str = Field(description="Table/entity name.")
    data: dict[str, Any] = Field(description="Column-value mapping for the new record.")
    connection_id: str = "default"


class CreateManyParams(BaseModel):
    entity: str = Field(description="Table/entity name.")
    records: list[dict[str, Any]] = Field(
        description="List of column-value mappings to insert.",
        min_length=1,
    )
    connection_id: str = "default"


class UpdateParams(BaseModel):
    entity: str = Field(description="Table/entity name.")
    filter: dict[str, Any] = Field(
        description="MongoDB-like filter to select records to update.",
    )
    values: dict[str, Any] = Field(
        description="Column-value mapping with new values.",
    )
    connection_id: str = "default"


class DeleteParams(BaseModel):
    entity: str = Field(description="Table/entity name.")
    filter: dict[str, Any] = Field(
        description="MongoDB-like filter to select records to delete.",
    )
    confirm: bool = Field(
        default=False,
        description="Safety flag: must be True to execute DELETE.",
    )
    connection_id: str = "default"


class CountParams(BaseModel):
    entity: str = Field(description="Table/entity name.")
    filter: dict[str, Any] = Field(
        default_factory=dict,
        description="MongoDB-like filter. Empty = count all rows.",
    )
    connection_id: str = "default"


class AggregateParams(BaseModel):
    entity: str = Field(description="Table/entity name.")
    group_by: list[str] = Field(
        description="Columns to group by.",
    )
    aggregations: dict[str, str] = Field(
        description=(
            "Column → aggregate function mapping. "
            'Examples: {"salary": "avg", "id": "count", "price": "sum"}. '
            "Supported: sum, avg, min, max, count."
        ),
    )
    filter: dict[str, Any] = Field(
        default_factory=dict,
        description="Pre-aggregation filter (WHERE clause).",
    )
    having: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Post-aggregation filter (HAVING clause). "
            'Use aggregated column aliases, e.g. {"count_id": {"$gte": 5}}'
        ),
    )
    order_by: list[str] | None = None
    limit: Annotated[int, Field(ge=1, le=10_000)] = 1000
    connection_id: str = "default"


class SearchParams(BaseModel):
    entity: str = Field(description="Table/entity name.")
    query: str = Field(description="Text to search for.")
    columns: list[str] = Field(
        description="Columns to search across.",
    )
    case_sensitive: bool = False
    limit: Annotated[int, Field(ge=1, le=10_000)] = 100
    connection_id: str = "default"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

PARAMS_MAP: dict[str, type[BaseModel]] = {
    "connect": GatewayConnectParams,
    "disconnect": GatewayDisconnectParams,
    "introspect": IntrospectParams,
    "find": FindParams,
    "find_one": FindOneParams,
    "create": CreateParams,
    "create_many": CreateManyParams,
    "update": UpdateParams,
    "delete": DeleteParams,
    "count": CountParams,
    "aggregate": AggregateParams,
    "search": SearchParams,
}
