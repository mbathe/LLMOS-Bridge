"""Typed parameter models for the context_manager module."""

from __future__ import annotations

from pydantic import BaseModel, Field


class GetBudgetParams(BaseModel):
    """Parameters for context_manager.get_budget."""
    pass


class CompressHistoryParams(BaseModel):
    """Parameters for context_manager.compress_history."""
    keep_last_n: int = Field(
        default=10,
        ge=1,
        le=200,
        description="Number of recent messages to keep uncompressed",
    )


class FetchContextParams(BaseModel):
    """Parameters for context_manager.fetch_context."""
    query: str = Field(description="What to look for in compressed history")
    segment_index: int | None = Field(
        default=None,
        ge=0,
        description="Specific compression segment to retrieve (0 = most recent)",
    )


class GetToolsSummaryParams(BaseModel):
    """Parameters for context_manager.get_tools_summary."""
    module_filter: str = Field(
        default="",
        description="Only show tools from this module",
    )


class GetStateParams(BaseModel):
    """Parameters for context_manager.get_state."""
    pass


PARAMS_MAP: dict[str, type[BaseModel]] = {
    "get_budget": GetBudgetParams,
    "compress_history": CompressHistoryParams,
    "fetch_context": FetchContextParams,
    "get_tools_summary": GetToolsSummaryParams,
    "get_state": GetStateParams,
}
