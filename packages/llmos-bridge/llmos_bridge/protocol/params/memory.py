"""Protocol params for the memory module."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class StoreParams(BaseModel):
    key: str = Field(description="Key to store under")
    value: str = Field(description="Value to store")
    backend: str | None = Field(default=None, description="Backend to use (default: module default)")
    metadata: dict[str, Any] | None = Field(default=None, description="Optional metadata dict")
    ttl_seconds: float | None = Field(default=None, description="Time-to-live in seconds")


class RecallParams(BaseModel):
    key: str = Field(description="Key to recall")
    backend: str | None = Field(default=None, description="Backend to query")


class SearchParams(BaseModel):
    query: str = Field(description="Search query")
    backend: str | None = Field(default=None, description="Backend to search (omit for all)")
    top_k: int = Field(default=5, ge=1, le=100, description="Max results to return")


class DeleteParams(BaseModel):
    key: str = Field(description="Key to delete")
    backend: str | None = Field(default=None, description="Backend to delete from")


class ListKeysParams(BaseModel):
    backend: str | None = Field(default=None, description="Backend to list keys from")
    prefix: str | None = Field(default=None, description="Filter by key prefix")
    limit: int = Field(default=100, ge=1, le=10_000, description="Max keys to return")


class ClearParams(BaseModel):
    backend: str = Field(description="Backend to clear")


class ListBackendsParams(BaseModel):
    pass


class SetObjectiveParams(BaseModel):
    goal: str = Field(description="The primary objective/goal")
    sub_goals: list[str] = Field(default_factory=list, description="List of sub-goals")
    success_criteria: list[str] = Field(default_factory=list, description="Criteria for completion")


class GetContextParams(BaseModel):
    pass


class UpdateProgressParams(BaseModel):
    progress: float = Field(ge=0.0, le=1.0, description="Progress from 0.0 to 1.0")
    completed_sub_goal: str | None = Field(default=None, description="Name of sub-goal just completed")
    complete: bool = Field(default=False, description="Mark objective as fully completed")


class ObserveParams(BaseModel):
    """Get a real-time snapshot of all memory state without needing specific keys."""
    pass


PARAMS_MAP: dict[str, type] = {
    "store": StoreParams,
    "recall": RecallParams,
    "search": SearchParams,
    "delete": DeleteParams,
    "list_keys": ListKeysParams,
    "clear": ClearParams,
    "list_backends": ListBackendsParams,
    "set_objective": SetObjectiveParams,
    "get_context": GetContextParams,
    "update_progress": UpdateProgressParams,
    "observe": ObserveParams,
}
