"""TriggerModule — IML module that wraps TriggerDaemon for LLM access.

MODULE_ID: "triggers"

This module is special: it does not perform OS operations itself, but
instead provides the LLM with a programmatic interface to TriggerDaemon.
This enables *trigger chaining*: a plan running in response to trigger A
can create trigger B that will fire future plans automatically.

Dependency injection
--------------------
TriggerModule stores a reference to TriggerDaemon injected via ``set_daemon()``.
server.py calls this after both registry and daemon are initialised.

Actions
-------
register_trigger    → TriggerDaemon.register()
activate_trigger    → TriggerDaemon.activate()
deactivate_trigger  → TriggerDaemon.deactivate()
delete_trigger      → TriggerDaemon.delete()
list_triggers       → TriggerDaemon.list_all()
get_trigger         → TriggerDaemon.get()

Security
--------
All write actions require "power_user" permission.
"""

from __future__ import annotations

from typing import Any

from llmos_bridge.modules.base import ActionResult, BaseModule
from llmos_bridge.modules.manifest import ActionSpec, ModuleManifest
from llmos_bridge.security.decorators import (
    audit_trail,
    requires_permission,
    sensitive_action,
)
from llmos_bridge.security.models import Permission, RiskLevel
from llmos_bridge.triggers.models import (
    TriggerCondition,
    TriggerDefinition,
    TriggerPriority,
    TriggerType,
)

_PRIORITY_MAP: dict[str, TriggerPriority] = {
    "background": TriggerPriority.BACKGROUND,
    "low": TriggerPriority.LOW,
    "normal": TriggerPriority.NORMAL,
    "high": TriggerPriority.HIGH,
    "critical": TriggerPriority.CRITICAL,
}


class TriggerModule(BaseModule):
    """IML module providing LLM access to TriggerDaemon."""

    MODULE_ID = "triggers"
    VERSION = "1.0.0"
    SUPPORTED_PLATFORMS = ["linux", "darwin", "windows"]

    def __init__(self) -> None:
        self._daemon: Any | None = None  # injected via set_daemon()
        super().__init__()

    def set_daemon(self, daemon: Any) -> None:
        """Inject the TriggerDaemon.  Called by server.py after startup."""
        self._daemon = daemon

    def _check_dependencies(self) -> None:
        pass  # No external dependencies at import time

    def get_manifest(self) -> ModuleManifest:
        return ModuleManifest(
            module_id=self.MODULE_ID,
            version=self.VERSION,
            description="Manage reactive triggers that fire IML plans in response to OS events, schedules, and conditions.",
            actions=[
                ActionSpec(
                    name="register_trigger",
                    description="Register a new trigger that fires an IML plan when its condition is met",
                    permission_required="power_user",
                    platforms=["all"],
                ),
                ActionSpec(
                    name="activate_trigger",
                    description="Enable and arm an existing trigger",
                    permission_required="power_user",
                    platforms=["all"],
                ),
                ActionSpec(
                    name="deactivate_trigger",
                    description="Pause a trigger without deleting it",
                    permission_required="power_user",
                    platforms=["all"],
                ),
                ActionSpec(
                    name="delete_trigger",
                    description="Permanently remove a trigger",
                    permission_required="power_user",
                    platforms=["all"],
                ),
                ActionSpec(
                    name="list_triggers",
                    description="List all registered triggers with optional state/type filters",
                    permission_required="local_worker",
                    platforms=["all"],
                ),
                ActionSpec(
                    name="get_trigger",
                    description="Retrieve a single trigger by ID",
                    permission_required="local_worker",
                    platforms=["all"],
                ),
            ],
        )

    # ---------------------------------------------------------------------------
    # Action implementations
    # ---------------------------------------------------------------------------

    @requires_permission(Permission.PROCESS_EXECUTE, reason="Creates automated execution trigger")
    @audit_trail("standard")
    async def _action_register_trigger(self, params: dict[str, Any]) -> Any:
        if self._daemon is None:
            return ActionResult(success=False, error="TriggerDaemon not available — triggers not enabled in config")

        cond_raw = params["condition"]
        condition = TriggerCondition(
            type=TriggerType(cond_raw["type"]),
            params=cond_raw.get("params", {}),
        )
        trigger = TriggerDefinition(
            name=params["name"],
            description=params.get("description", ""),
            condition=condition,
            plan_template=params["plan_template"],
            plan_id_prefix=params.get("plan_id_prefix", "trigger"),
            priority=_PRIORITY_MAP.get(params.get("priority", "normal"), TriggerPriority.NORMAL),
            min_interval_seconds=float(params.get("min_interval_seconds", 0.0)),
            max_fires_per_hour=int(params.get("max_fires_per_hour", 0)),
            conflict_policy=params.get("conflict_policy", "queue"),  # type: ignore[arg-type]
            resource_lock=params.get("resource_lock"),
            enabled=bool(params.get("enabled", True)),
            tags=list(params.get("tags", [])),
            expires_at=params.get("expires_at"),
            max_chain_depth=int(params.get("max_chain_depth", 5)),
            created_by="llm",
            chain_depth=int(params.get("_chain_depth", 0)),
        )

        registered = await self._daemon.register(trigger)
        return {
            "trigger_id": registered.trigger_id,
            "name": registered.name,
            "state": registered.state.value,
            "enabled": registered.enabled,
        }

    @audit_trail("standard")
    async def _action_activate_trigger(self, params: dict[str, Any]) -> Any:
        if self._daemon is None:
            return ActionResult(success=False, error="TriggerDaemon not available")
        await self._daemon.activate(params["trigger_id"])
        return {"trigger_id": params["trigger_id"], "state": "active"}

    @audit_trail("standard")
    async def _action_deactivate_trigger(self, params: dict[str, Any]) -> Any:
        if self._daemon is None:
            return ActionResult(success=False, error="TriggerDaemon not available")
        await self._daemon.deactivate(params["trigger_id"])
        return {"trigger_id": params["trigger_id"], "state": "inactive"}

    @requires_permission(Permission.PROCESS_EXECUTE, reason="Removes automated execution trigger")
    @sensitive_action(RiskLevel.MEDIUM)
    @audit_trail("standard")
    async def _action_delete_trigger(self, params: dict[str, Any]) -> Any:
        if self._daemon is None:
            return ActionResult(success=False, error="TriggerDaemon not available")
        deleted = await self._daemon.delete(params["trigger_id"])
        return {"trigger_id": params["trigger_id"], "deleted": deleted}

    async def _action_list_triggers(self, params: dict[str, Any]) -> Any:
        if self._daemon is None:
            return ActionResult(success=False, error="TriggerDaemon not available")

        triggers = await self._daemon.list_all()

        state_filter = params.get("state")
        type_filter = params.get("trigger_type")
        tags_filter = list(params.get("tags", []))
        created_by_filter = params.get("created_by")
        include_health = bool(params.get("include_health", True))

        results = []
        for t in triggers:
            if state_filter and t.state.value != state_filter:
                continue
            if type_filter and t.condition.type.value != type_filter:
                continue
            if tags_filter and not all(tag in t.tags for tag in tags_filter):
                continue
            if created_by_filter and t.created_by != created_by_filter:
                continue
            entry: dict[str, Any] = {
                "trigger_id": t.trigger_id,
                "name": t.name,
                "type": t.condition.type.value,
                "state": t.state.value,
                "priority": t.priority.name.lower(),
                "enabled": t.enabled,
                "tags": t.tags,
                "created_by": t.created_by,
                "created_at": t.created_at,
            }
            if include_health:
                entry["health"] = {
                    "fire_count": t.health.fire_count,
                    "fail_count": t.health.fail_count,
                    "last_fired_at": t.health.last_fired_at,
                    "avg_latency_ms": t.health.avg_latency_ms,
                }
            results.append(entry)

        return {"triggers": results, "count": len(results)}

    async def _action_get_trigger(self, params: dict[str, Any]) -> Any:
        if self._daemon is None:
            return ActionResult(success=False, error="TriggerDaemon not available")

        trigger = await self._daemon.get(params["trigger_id"])
        if trigger is None:
            return ActionResult(success=False, error=f"Trigger not found: {params['trigger_id']}")

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
            "health": {
                "fire_count": trigger.health.fire_count,
                "fail_count": trigger.health.fail_count,
                "throttle_count": trigger.health.throttle_count,
                "last_fired_at": trigger.health.last_fired_at,
                "last_error": trigger.health.last_error,
                "avg_latency_ms": trigger.health.avg_latency_ms,
            },
        }

