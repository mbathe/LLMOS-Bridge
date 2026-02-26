"""API routes for TriggerDaemon management.

REST endpoints::

    GET    /triggers              — list all triggers
    POST   /triggers              — register a new trigger
    GET    /triggers/{trigger_id} — get trigger details + health
    PUT    /triggers/{trigger_id}/activate   — arm a trigger
    PUT    /triggers/{trigger_id}/deactivate — pause a trigger
    DELETE /triggers/{trigger_id}            — delete a trigger

All endpoints require TriggerDaemon to be enabled in config.
If not enabled, they return 503 Service Unavailable.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from llmos_bridge.triggers.models import (
    TriggerCondition,
    TriggerDefinition,
    TriggerPriority,
    TriggerType,
)

router = APIRouter(prefix="/triggers", tags=["triggers"])


def _get_daemon(request: Request) -> Any:
    """FastAPI dependency — retrieve TriggerDaemon from app state."""
    daemon = getattr(request.app.state, "trigger_daemon", None)
    if daemon is None:
        raise HTTPException(
            status_code=503,
            detail="TriggerDaemon is not enabled. Set triggers.enabled=true in config.",
        )
    return daemon


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class TriggerConditionRequest(BaseModel):
    type: str
    params: dict[str, Any] = Field(default_factory=dict)


class RegisterTriggerRequest(BaseModel):
    name: str
    description: str = ""
    condition: TriggerConditionRequest
    plan_template: dict[str, Any]
    plan_id_prefix: str = "trigger"
    priority: str = "normal"
    min_interval_seconds: float = 0.0
    max_fires_per_hour: int = 0
    conflict_policy: str = "queue"
    resource_lock: str | None = None
    enabled: bool = True
    tags: list[str] = Field(default_factory=list)
    expires_at: float | None = None
    max_chain_depth: int = 5


class TriggerSummary(BaseModel):
    trigger_id: str
    name: str
    type: str
    state: str
    priority: str
    enabled: bool
    tags: list[str]
    created_by: str
    created_at: float
    fire_count: int
    fail_count: int
    last_fired_at: float | None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=list[TriggerSummary])
async def list_triggers(
    daemon: Any = Depends(_get_daemon),
    state: str | None = None,
) -> list[dict[str, Any]]:
    """List all registered triggers."""
    triggers = await daemon.list_all()
    results = []
    for t in triggers:
        if state and t.state.value != state:
            continue
        results.append({
            "trigger_id": t.trigger_id,
            "name": t.name,
            "type": t.condition.type.value,
            "state": t.state.value,
            "priority": t.priority.name.lower(),
            "enabled": t.enabled,
            "tags": t.tags,
            "created_by": t.created_by,
            "created_at": t.created_at,
            "fire_count": t.health.fire_count,
            "fail_count": t.health.fail_count,
            "last_fired_at": t.health.last_fired_at,
        })
    return results


@router.post("", status_code=201)
async def register_trigger(
    body: RegisterTriggerRequest,
    daemon: Any = Depends(_get_daemon),
) -> dict[str, Any]:
    """Register and optionally activate a new trigger."""
    _PRIORITY_MAP = {
        "background": TriggerPriority.BACKGROUND,
        "low": TriggerPriority.LOW,
        "normal": TriggerPriority.NORMAL,
        "high": TriggerPriority.HIGH,
        "critical": TriggerPriority.CRITICAL,
    }
    condition = TriggerCondition(
        type=TriggerType(body.condition.type),
        params=body.condition.params,
    )
    trigger = TriggerDefinition(
        name=body.name,
        description=body.description,
        condition=condition,
        plan_template=body.plan_template,
        plan_id_prefix=body.plan_id_prefix,
        priority=_PRIORITY_MAP.get(body.priority, TriggerPriority.NORMAL),
        min_interval_seconds=body.min_interval_seconds,
        max_fires_per_hour=body.max_fires_per_hour,
        conflict_policy=body.conflict_policy,  # type: ignore[arg-type]
        resource_lock=body.resource_lock,
        enabled=body.enabled,
        tags=body.tags,
        expires_at=body.expires_at,
        max_chain_depth=body.max_chain_depth,
        created_by="user",
    )
    registered = await daemon.register(trigger)
    return {
        "trigger_id": registered.trigger_id,
        "name": registered.name,
        "state": registered.state.value,
        "message": "Trigger registered successfully",
    }


@router.get("/{trigger_id}")
async def get_trigger(
    trigger_id: str,
    daemon: Any = Depends(_get_daemon),
) -> dict[str, Any]:
    """Retrieve a trigger by ID including full health metrics."""
    trigger = await daemon.get(trigger_id)
    if trigger is None:
        raise HTTPException(status_code=404, detail=f"Trigger not found: {trigger_id}")
    return {
        "trigger_id": trigger.trigger_id,
        "name": trigger.name,
        "description": trigger.description,
        "type": trigger.condition.type.value,
        "condition_params": trigger.condition.params,
        "state": trigger.state.value,
        "priority": trigger.priority.name.lower(),
        "enabled": trigger.enabled,
        "min_interval_seconds": trigger.min_interval_seconds,
        "max_fires_per_hour": trigger.max_fires_per_hour,
        "conflict_policy": trigger.conflict_policy,
        "resource_lock": trigger.resource_lock,
        "tags": trigger.tags,
        "created_by": trigger.created_by,
        "created_at": trigger.created_at,
        "expires_at": trigger.expires_at,
        "health": {
            "fire_count": trigger.health.fire_count,
            "fail_count": trigger.health.fail_count,
            "throttle_count": trigger.health.throttle_count,
            "last_fired_at": trigger.health.last_fired_at,
            "last_error": trigger.health.last_error,
            "avg_latency_ms": round(trigger.health.avg_latency_ms, 2),
        },
    }


@router.put("/{trigger_id}/activate")
async def activate_trigger(
    trigger_id: str,
    daemon: Any = Depends(_get_daemon),
) -> dict[str, str]:
    """Enable and arm a trigger."""
    try:
        await daemon.activate(trigger_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Trigger not found: {trigger_id}")
    return {"trigger_id": trigger_id, "state": "active", "message": "Trigger activated"}


@router.put("/{trigger_id}/deactivate")
async def deactivate_trigger(
    trigger_id: str,
    daemon: Any = Depends(_get_daemon),
) -> dict[str, str]:
    """Pause a trigger without deleting it."""
    try:
        await daemon.deactivate(trigger_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Trigger not found: {trigger_id}")
    return {"trigger_id": trigger_id, "state": "inactive", "message": "Trigger deactivated"}


@router.delete("/{trigger_id}", status_code=204, response_model=None)
async def delete_trigger(
    trigger_id: str,
    daemon: Any = Depends(_get_daemon),
) -> None:
    """Permanently remove a trigger."""
    deleted = await daemon.delete(trigger_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Trigger not found: {trigger_id}")
