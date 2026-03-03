"""API routes — System administration for dashboard."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from llmos_bridge.api.dependencies import AuthDep, ConfigDep, RegistryDep

router = APIRouter(prefix="/admin", tags=["admin-system"])


@router.get("/system/status")
async def get_system_status(
    registry: RegistryDep,
    _auth: AuthDep,
) -> dict[str, Any]:
    """Aggregated system health status."""
    if not registry.is_available("module_manager"):
        return {"error": "Module Manager not available"}
    mm = registry.get("module_manager")
    return await mm._action_get_system_status({"include_health": True})


@router.get("/system/services")
async def list_services(
    registry: RegistryDep,
    _auth: AuthDep,
) -> dict[str, Any]:
    """List all ServiceBus registrations."""
    if not registry.is_available("module_manager"):
        return {"services": [], "error": "Module Manager not available"}
    mm = registry.get("module_manager")
    return await mm._action_list_services({})


@router.get("/system/config")
async def get_config(
    config: ConfigDep,
    _auth: AuthDep,
) -> dict[str, Any]:
    """Get current configuration (secrets redacted)."""
    raw = config.model_dump()
    # Redact sensitive values.
    if "security" in raw and "api_token" in raw["security"]:
        token = raw["security"]["api_token"]
        if token:
            raw["security"]["api_token"] = f"{token[:4]}***" if len(token) > 4 else "***"
    return raw


@router.get("/system/events")
async def get_events(
    _auth: AuthDep,
    request: Request,
    limit: int = 50,
    topic: str | None = None,
) -> dict[str, Any]:
    """Query recent events from the event bus ring buffer."""
    # Try to get the event bus from state.
    lifecycle = getattr(request.app.state, "lifecycle_manager", None)
    if lifecycle is None:
        return {"events": [], "count": 0}

    bus = getattr(lifecycle, "_event_bus", None)
    if bus is None:
        return {"events": [], "count": 0}

    recent = list(getattr(bus, "_recent_events", []))
    if topic:
        recent = [e for e in recent if e.get("_topic") == topic]
    recent = recent[-limit:]
    return {"events": recent, "count": len(recent)}


@router.get("/system/policies")
async def get_policies(
    _auth: AuthDep,
    request: Request,
) -> dict[str, Any]:
    """List all module policies and enforcement status."""
    # Get policy enforcer from executor.
    executor = getattr(request.app.state, "plan_executor", None)
    if executor is None:
        return {"policies": {}, "error": "Executor not available"}

    policy_enforcer = getattr(executor, "_policy_enforcer", None)
    if policy_enforcer is None:
        return {"policies": {}, "note": "Policy enforcement not configured"}

    return {"policies": policy_enforcer.status()}
