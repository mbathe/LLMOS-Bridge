"""IML parameter models for the ``triggers`` module.

These Pydantic models validate the ``params`` dict in each IML action
that targets the TriggerModule (MODULE_ID = "triggers").

Actions
-------
register_trigger    — create a new trigger
activate_trigger    — enable/re-arm a trigger
deactivate_trigger  — pause a trigger without deleting it
delete_trigger      — permanently remove a trigger
list_triggers       — list all triggers, optionally filtered by state
get_trigger         — retrieve one trigger by ID
update_trigger      — modify trigger configuration
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Shared condition model
# ---------------------------------------------------------------------------


class TriggerConditionParams(BaseModel):
    """Describes what the trigger watches."""

    type: Literal[
        "temporal", "filesystem", "process", "resource", "application", "iot", "composite"
    ] = Field(..., description="Trigger type")

    params: dict[str, Any] = Field(
        default_factory=dict,
        description="Type-specific parameters. See TriggerDaemon documentation.",
    )


# ---------------------------------------------------------------------------
# register_trigger
# ---------------------------------------------------------------------------


class RegisterTriggerParams(BaseModel):
    """Params for the ``register_trigger`` action."""

    name: str = Field(..., min_length=1, max_length=128, description="Human-readable trigger name")
    description: str = Field(default="", description="Optional description")
    condition: TriggerConditionParams = Field(..., description="What to watch")
    plan_template: dict[str, Any] = Field(
        ..., description="IML plan JSON template to execute when the trigger fires"
    )
    plan_id_prefix: str = Field(default="trigger", description="Prefix for auto-generated plan IDs")
    priority: Literal["background", "low", "normal", "high", "critical"] = Field(
        default="normal", description="Execution priority"
    )
    min_interval_seconds: float = Field(
        default=0.0, ge=0.0, description="Minimum seconds between fires (0 = no limit)"
    )
    max_fires_per_hour: int = Field(
        default=0, ge=0, description="Max fires per hour (0 = unlimited)"
    )
    conflict_policy: Literal["queue", "preempt", "reject"] = Field(
        default="queue",
        description="What to do when another plan from this trigger is already running",
    )
    resource_lock: str | None = Field(
        default=None,
        description="Optional shared resource name — prevents concurrent execution",
    )
    enabled: bool = Field(default=True, description="Activate immediately after registration")
    tags: list[str] = Field(default_factory=list, description="Optional tags for filtering")
    expires_at: float | None = Field(
        default=None, description="Auto-delete after this Unix timestamp"
    )
    max_chain_depth: int = Field(
        default=5, ge=1, le=20, description="Maximum trigger chain depth (loop protection)"
    )


# ---------------------------------------------------------------------------
# activate_trigger / deactivate_trigger / delete_trigger / get_trigger
# ---------------------------------------------------------------------------


class TriggerIdParams(BaseModel):
    """Params for actions that target a single trigger by ID."""

    trigger_id: str = Field(..., min_length=1, description="Trigger ID to operate on")


# Reuse for activate, deactivate, delete, get
ActivateTriggerParams = TriggerIdParams
DeactivateTriggerParams = TriggerIdParams
DeleteTriggerParams = TriggerIdParams
GetTriggerParams = TriggerIdParams


# ---------------------------------------------------------------------------
# list_triggers
# ---------------------------------------------------------------------------


class ListTriggersParams(BaseModel):
    """Params for listing triggers with optional filters."""

    state: Literal[
        "registered", "inactive", "active", "watching", "fired", "throttled", "failed"
    ] | None = Field(default=None, description="Filter by lifecycle state")

    trigger_type: Literal[
        "temporal", "filesystem", "process", "resource", "application", "iot", "composite"
    ] | None = Field(default=None, description="Filter by trigger type")

    tags: list[str] = Field(
        default_factory=list, description="Filter by tags (all specified tags must match)"
    )

    created_by: Literal["user", "llm", "system"] | None = Field(
        default=None, description="Filter by creator"
    )

    include_health: bool = Field(
        default=True, description="Include health metrics in the response"
    )


# ---------------------------------------------------------------------------
# update_trigger
# ---------------------------------------------------------------------------


class UpdateTriggerParams(BaseModel):
    """Params for partial update of a trigger configuration."""

    trigger_id: str = Field(..., description="ID of the trigger to update")
    name: str | None = Field(default=None)
    description: str | None = Field(default=None)
    plan_template: dict[str, Any] | None = Field(default=None)
    priority: Literal["background", "low", "normal", "high", "critical"] | None = Field(default=None)
    min_interval_seconds: float | None = Field(default=None, ge=0.0)
    max_fires_per_hour: int | None = Field(default=None, ge=0)
    conflict_policy: Literal["queue", "preempt", "reject"] | None = Field(default=None)
    resource_lock: str | None = Field(default=None)
    tags: list[str] | None = Field(default=None)
    expires_at: float | None = Field(default=None)
