"""Module Manager — system module for runtime module governance.

22 IML-callable actions for listing, enabling, disabling, pausing, resuming,
introspecting, installing, upgrading, and uninstalling modules.  This is the
central control plane that an LLM or admin uses to manage the module system
at runtime.

MODULE_ID: ``module_manager``
MODULE_TYPE: ``system`` (cannot be disabled or uninstalled)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from llmos_bridge.modules.base import BaseModule, Platform
from llmos_bridge.modules.manifest import (
    ActionSpec,
    ModuleManifest,
    ParamSpec,
    ServiceDescriptor,
)
from llmos_bridge.modules.types import ModuleState, ModuleType, SYSTEM_MODULE_IDS
from llmos_bridge.security.decorators import (
    audit_trail,
    data_classification,
    rate_limited,
    requires_permission,
    sensitive_action,
)
from llmos_bridge.security.models import DataClassification, Permission, RiskLevel


class ModuleManagerModule(BaseModule):
    MODULE_ID = "module_manager"
    VERSION = "2.0.0"
    MODULE_TYPE = "system"
    SUPPORTED_PLATFORMS = [Platform.ALL]

    def __init__(self) -> None:
        super().__init__()
        self._lifecycle: Any | None = None  # ModuleLifecycleManager
        self._service_bus: Any | None = None  # ServiceBus
        self._installer: Any | None = None  # ModuleInstaller
        self._hub_client: Any | None = None  # HubClient

    def set_lifecycle_manager(self, lifecycle: Any) -> None:
        """Inject the ModuleLifecycleManager."""
        self._lifecycle = lifecycle

    def set_service_bus(self, service_bus: Any) -> None:
        """Inject the ServiceBus."""
        self._service_bus = service_bus

    def set_installer(self, installer: Any) -> None:
        """Inject the ModuleInstaller for hub operations."""
        self._installer = installer

    def set_hub_client(self, hub_client: Any) -> None:
        """Inject the HubClient for remote registry operations."""
        self._hub_client = hub_client

    def _check_dependencies(self) -> None:
        pass

    # ------------------------------------------------------------------
    # Actions — Module listing and inspection
    # ------------------------------------------------------------------

    @requires_permission(Permission.MODULE_READ, reason="List registered modules")
    async def _action_list_modules(self, params: dict[str, Any]) -> dict[str, Any]:
        """List all registered modules with their state and type."""
        if self._lifecycle is None:
            return {"modules": [], "error": "LifecycleManager not configured"}

        registry = self._lifecycle._registry
        modules = []
        for module_id in registry.list_available():
            state = self._lifecycle.get_state(module_id)
            mtype = self._lifecycle.get_type(module_id)

            # Apply filters.
            filter_type = params.get("module_type")
            if filter_type and mtype.value != filter_type:
                continue
            filter_state = params.get("state")
            if filter_state and state.value != filter_state:
                continue

            info: dict[str, Any] = {
                "module_id": module_id,
                "state": state.value,
                "type": mtype.value,
            }

            if params.get("include_health") and state == ModuleState.ACTIVE:
                try:
                    mod = registry.get(module_id)
                    health = await mod.health_check()
                    info["health"] = health
                except Exception as exc:
                    info["health"] = {"status": "error", "error": str(exc)}

            modules.append(info)

        return {"modules": modules, "count": len(modules)}

    @requires_permission(Permission.MODULE_READ, reason="Inspect module details")
    async def _action_get_module_info(self, params: dict[str, Any]) -> dict[str, Any]:
        """Get detailed information about a specific module."""
        if self._lifecycle is None:
            return {"error": "LifecycleManager not configured"}

        module_id = params["module_id"]
        registry = self._lifecycle._registry

        if not registry.is_available(module_id):
            return {"error": f"Module '{module_id}' is not available"}

        module = registry.get(module_id)
        manifest = module.get_manifest()
        state = self._lifecycle.get_state(module_id)
        mtype = self._lifecycle.get_type(module_id)

        info: dict[str, Any] = {
            "module_id": module_id,
            "version": manifest.version,
            "description": manifest.description,
            "state": state.value,
            "type": mtype.value,
            "actions": manifest.action_names(),
            "disabled_actions": self._lifecycle.get_disabled_actions(module_id),
        }

        if params.get("include_health") and state == ModuleState.ACTIVE:
            try:
                info["health"] = await module.health_check()
            except Exception as exc:
                info["health"] = {"status": "error", "error": str(exc)}

        if params.get("include_metrics"):
            info["metrics"] = module.metrics()

        return info

    # ------------------------------------------------------------------
    # Actions — Lifecycle management
    # ------------------------------------------------------------------

    @requires_permission(Permission.MODULE_MANAGE, reason="Enable a module")
    @sensitive_action(RiskLevel.MEDIUM)
    @audit_trail("standard")
    async def _action_enable_module(self, params: dict[str, Any]) -> dict[str, Any]:
        """Enable (start) a disabled module."""
        if self._lifecycle is None:
            return {"error": "LifecycleManager not configured"}

        module_id = params["module_id"]
        try:
            await self._lifecycle.start_module(module_id)
            return {
                "module_id": module_id,
                "state": self._lifecycle.get_state(module_id).value,
                "success": True,
            }
        except Exception as exc:
            return {"module_id": module_id, "success": False, "error": str(exc)}

    @requires_permission(Permission.MODULE_MANAGE, reason="Disable a module")
    @sensitive_action(RiskLevel.MEDIUM)
    @audit_trail("standard")
    async def _action_disable_module(self, params: dict[str, Any]) -> dict[str, Any]:
        """Disable (stop) a module.  System modules cannot be disabled."""
        if self._lifecycle is None:
            return {"error": "LifecycleManager not configured"}

        module_id = params["module_id"]
        if self._lifecycle.is_system_module(module_id):
            return {
                "module_id": module_id,
                "success": False,
                "error": f"Cannot disable system module '{module_id}'",
            }

        try:
            await self._lifecycle.stop_module(module_id)
            return {
                "module_id": module_id,
                "state": self._lifecycle.get_state(module_id).value,
                "success": True,
            }
        except Exception as exc:
            return {"module_id": module_id, "success": False, "error": str(exc)}

    @requires_permission(Permission.MODULE_MANAGE, reason="Pause a module")
    @sensitive_action(RiskLevel.MEDIUM)
    @audit_trail("standard")
    async def _action_pause_module(self, params: dict[str, Any]) -> dict[str, Any]:
        """Pause a module (temporarily suspend its actions)."""
        if self._lifecycle is None:
            return {"error": "LifecycleManager not configured"}

        module_id = params["module_id"]
        try:
            await self._lifecycle.pause_module(module_id)
            return {
                "module_id": module_id,
                "state": self._lifecycle.get_state(module_id).value,
                "success": True,
            }
        except Exception as exc:
            return {"module_id": module_id, "success": False, "error": str(exc)}

    @requires_permission(Permission.MODULE_MANAGE, reason="Resume a paused module")
    @sensitive_action(RiskLevel.MEDIUM)
    @audit_trail("standard")
    async def _action_resume_module(self, params: dict[str, Any]) -> dict[str, Any]:
        """Resume a paused module."""
        if self._lifecycle is None:
            return {"error": "LifecycleManager not configured"}

        module_id = params["module_id"]
        try:
            await self._lifecycle.resume_module(module_id)
            return {
                "module_id": module_id,
                "state": self._lifecycle.get_state(module_id).value,
                "success": True,
            }
        except Exception as exc:
            return {"module_id": module_id, "success": False, "error": str(exc)}

    @requires_permission(Permission.MODULE_MANAGE, reason="Restart a module")
    @sensitive_action(RiskLevel.HIGH)
    @rate_limited(calls_per_minute=10)
    @audit_trail("standard")
    async def _action_restart_module(self, params: dict[str, Any]) -> dict[str, Any]:
        """Restart a module (stop then start)."""
        if self._lifecycle is None:
            return {"error": "LifecycleManager not configured"}

        module_id = params["module_id"]
        try:
            await self._lifecycle.restart_module(module_id)
            return {
                "module_id": module_id,
                "state": self._lifecycle.get_state(module_id).value,
                "success": True,
            }
        except Exception as exc:
            return {"module_id": module_id, "success": False, "error": str(exc)}

    # ------------------------------------------------------------------
    # Actions — Action toggles
    # ------------------------------------------------------------------

    @requires_permission(Permission.MODULE_MANAGE, reason="Re-enable an action")
    @sensitive_action(RiskLevel.MEDIUM)
    @audit_trail("standard")
    async def _action_enable_action(self, params: dict[str, Any]) -> dict[str, Any]:
        """Re-enable a previously disabled action."""
        if self._lifecycle is None:
            return {"error": "LifecycleManager not configured"}

        module_id = params["module_id"]
        action = params["action"]
        self._lifecycle.enable_action(module_id, action)
        return {"module_id": module_id, "action": action, "enabled": True}

    @requires_permission(Permission.MODULE_MANAGE, reason="Disable a specific action")
    @sensitive_action(RiskLevel.MEDIUM)
    @audit_trail("standard")
    async def _action_disable_action(self, params: dict[str, Any]) -> dict[str, Any]:
        """Disable a specific action on a module."""
        if self._lifecycle is None:
            return {"error": "LifecycleManager not configured"}

        module_id = params["module_id"]
        action = params["action"]
        reason = params.get("reason", "")
        self._lifecycle.disable_action(module_id, action, reason)
        return {"module_id": module_id, "action": action, "enabled": False, "reason": reason}

    # ------------------------------------------------------------------
    # Actions — Introspection
    # ------------------------------------------------------------------

    @requires_permission(Permission.MODULE_READ, reason="Check module health")
    async def _action_get_module_health(self, params: dict[str, Any]) -> dict[str, Any]:
        """Run health_check() on a specific module."""
        if self._lifecycle is None:
            return {"error": "LifecycleManager not configured"}

        module_id = params["module_id"]
        registry = self._lifecycle._registry
        module = registry.get(module_id)
        try:
            return await module.health_check()
        except Exception as exc:
            return {"module_id": module_id, "status": "error", "error": str(exc)}

    @requires_permission(Permission.MODULE_READ, reason="Read module metrics")
    async def _action_get_module_metrics(self, params: dict[str, Any]) -> dict[str, Any]:
        """Get operational metrics from a module."""
        if self._lifecycle is None:
            return {"error": "LifecycleManager not configured"}

        module_id = params["module_id"]
        registry = self._lifecycle._registry
        module = registry.get(module_id)
        return {"module_id": module_id, "metrics": module.metrics()}

    @requires_permission(Permission.MODULE_READ, reason="Read module state snapshot")
    async def _action_get_module_state(self, params: dict[str, Any]) -> dict[str, Any]:
        """Get a state snapshot from a module."""
        if self._lifecycle is None:
            return {"error": "LifecycleManager not configured"}

        module_id = params["module_id"]
        registry = self._lifecycle._registry
        module = registry.get(module_id)
        return {"module_id": module_id, "state_snapshot": module.state_snapshot()}

    @requires_permission(Permission.MODULE_READ, reason="List registered services")
    async def _action_list_services(self, params: dict[str, Any]) -> dict[str, Any]:
        """List all registered services on the ServiceBus."""
        if self._service_bus is None:
            return {"services": [], "error": "ServiceBus not configured"}

        services = self._service_bus.list_services()
        return {"services": services, "count": len(services)}

    @requires_permission(Permission.MODULE_READ, reason="Read system status")
    async def _action_get_system_status(self, params: dict[str, Any]) -> dict[str, Any]:
        """Get overall system health summary."""
        if self._lifecycle is None:
            return {"error": "LifecycleManager not configured"}

        report = self._lifecycle.get_full_report()
        registry = self._lifecycle._registry

        # Count by state and type.
        by_state: dict[str, int] = {}
        by_type: dict[str, int] = {}
        for info in report.values():
            st = info["state"]
            by_state[st] = by_state.get(st, 0) + 1
            tp = info["type"]
            by_type[tp] = by_type.get(tp, 0) + 1

        result: dict[str, Any] = {
            "total_modules": len(report),
            "by_state": by_state,
            "by_type": by_type,
            "failed": registry.list_failed(),
            "platform_excluded": registry.list_platform_excluded(),
        }

        if params.get("include_health"):
            health_results: dict[str, Any] = {}
            for module_id, info in report.items():
                if info["state"] == "active":
                    try:
                        mod = registry.get(module_id)
                        health_results[module_id] = await mod.health_check()
                    except Exception as exc:
                        health_results[module_id] = {"status": "error", "error": str(exc)}
            result["health"] = health_results

        if self._service_bus is not None:
            result["service_count"] = self._service_bus.service_count

        return result

    @requires_permission(Permission.MODULE_MANAGE, reason="Update module configuration")
    @sensitive_action(RiskLevel.MEDIUM)
    @data_classification(DataClassification.INTERNAL)
    @audit_trail("detailed")
    async def _action_update_module_config(self, params: dict[str, Any]) -> dict[str, Any]:
        """Update a module's runtime configuration."""
        if self._lifecycle is None:
            return {"error": "LifecycleManager not configured"}

        module_id = params["module_id"]
        config = params["config"]
        try:
            await self._lifecycle.update_config(module_id, config)
            return {"module_id": module_id, "success": True}
        except Exception as exc:
            return {"module_id": module_id, "success": False, "error": str(exc)}

    # ------------------------------------------------------------------
    # Actions — Hub / Package Manager (v3)
    # ------------------------------------------------------------------

    @requires_permission(Permission.MODULE_INSTALL, reason="Install module from hub or local path")
    @sensitive_action(RiskLevel.HIGH)
    @rate_limited(calls_per_minute=5)
    @audit_trail("detailed")
    async def _action_install_module(self, params: dict[str, Any]) -> dict[str, Any]:
        """Install a module from the hub or a local path."""
        if self._installer is None:
            return {"success": False, "error": "Module installer not configured"}

        source = params.get("source", "hub")
        module_id = params.get("module_id", "")
        path = params.get("path", "")
        version = params.get("version", "latest")

        if source == "local" and path:
            result = await self._installer.install_from_path(Path(path))
        elif source == "hub" and module_id:
            result = await self._installer.install_from_hub(
                module_id, version, hub_client=self._hub_client
            )
        else:
            return {
                "success": False,
                "error": "Provide 'module_id' for hub install or 'path' for local install.",
            }

        return {
            "success": result.success,
            "module_id": result.module_id,
            "version": result.version,
            "error": result.error,
            "installed_deps": result.installed_deps,
        }

    @requires_permission(Permission.MODULE_INSTALL, reason="Uninstall a community module")
    @sensitive_action(RiskLevel.HIGH)
    @rate_limited(calls_per_minute=5)
    @audit_trail("detailed")
    async def _action_uninstall_module(self, params: dict[str, Any]) -> dict[str, Any]:
        """Uninstall a community module."""
        if self._installer is None:
            return {"success": False, "error": "Module installer not configured"}

        module_id = params["module_id"]
        result = await self._installer.uninstall(module_id)
        return {
            "success": result.success,
            "module_id": result.module_id,
            "version": result.version,
            "error": result.error,
        }

    @requires_permission(Permission.MODULE_INSTALL, reason="Upgrade installed module")
    @sensitive_action(RiskLevel.HIGH)
    @rate_limited(calls_per_minute=5)
    @audit_trail("detailed")
    async def _action_upgrade_module(self, params: dict[str, Any]) -> dict[str, Any]:
        """Upgrade an installed module to a new version."""
        if self._installer is None:
            return {"success": False, "error": "Module installer not configured"}

        module_id = params["module_id"]
        path = params.get("path", "")

        if not path:
            return {
                "success": False,
                "error": "Provide 'path' to the new version package directory.",
            }

        result = await self._installer.upgrade(module_id, Path(path))
        return {
            "success": result.success,
            "module_id": result.module_id,
            "version": result.version,
            "error": result.error,
        }

    @requires_permission(Permission.MODULE_READ, reason="Search module hub")
    async def _action_search_hub(self, params: dict[str, Any]) -> dict[str, Any]:
        """Search the module hub for available modules."""
        if self._hub_client is None:
            return {"results": [], "error": "Hub client not configured"}

        query = params.get("query", "")
        limit = params.get("limit", 20)

        try:
            results = await self._hub_client.search(query, limit=limit)
            return {
                "results": [
                    {
                        "module_id": r.module_id,
                        "version": r.version,
                        "description": r.description,
                        "author": r.author,
                        "downloads": r.downloads,
                        "tags": r.tags,
                    }
                    for r in results
                ],
                "count": len(results),
            }
        except Exception as exc:
            return {"results": [], "error": str(exc)}

    @requires_permission(Permission.MODULE_READ, reason="List installed modules")
    async def _action_list_installed(self, params: dict[str, Any]) -> dict[str, Any]:
        """List all installed community modules."""
        if self._installer is None:
            return {"modules": [], "error": "Module installer not configured"}

        try:
            index = self._installer._index
            if params.get("enabled_only", False):
                modules = await index.list_enabled()
            else:
                modules = await index.list_all()

            return {
                "modules": [
                    {
                        "module_id": m.module_id,
                        "version": m.version,
                        "install_path": m.install_path,
                        "enabled": m.enabled,
                        "sandbox_level": m.sandbox_level,
                    }
                    for m in modules
                ],
                "count": len(modules),
            }
        except Exception as exc:
            return {"modules": [], "error": str(exc)}

    @requires_permission(Permission.MODULE_READ, reason="Verify module integrity")
    async def _action_verify_module(self, params: dict[str, Any]) -> dict[str, Any]:
        """Verify an installed module's integrity (signature + hash)."""
        if self._installer is None:
            return {"verified": False, "error": "Module installer not configured"}

        module_id = params["module_id"]
        return await self._installer.verify_module(module_id)

    @requires_permission(Permission.MODULE_READ, reason="Read module description")
    async def _action_describe_module(self, params: dict[str, Any]) -> dict[str, Any]:
        """Get dynamic self-description from a module (v3 describe())."""
        if self._lifecycle is None:
            return {"error": "LifecycleManager not configured"}

        module_id = params["module_id"]
        registry = self._lifecycle._registry

        if not registry.is_available(module_id):
            return {"error": f"Module '{module_id}' is not available"}

        module = registry.get(module_id)
        return module.describe()

    # ------------------------------------------------------------------
    # Manifest
    # ------------------------------------------------------------------

    def get_manifest(self) -> ModuleManifest:
        return ModuleManifest(
            module_id=self.MODULE_ID,
            version=self.VERSION,
            description=(
                "Central control plane for the module system. List, enable, disable, "
                "pause, resume, and introspect all registered modules and services."
            ),
            platforms=["all"],
            tags=["system", "management", "lifecycle"],
            module_type="system",
            actions=[
                ActionSpec(
                    name="list_modules",
                    description="List all registered modules with their state and type.",
                    params=[
                        ParamSpec("module_type", "string", "Filter: 'system' or 'user'.", required=False),
                        ParamSpec("state", "string", "Filter by lifecycle state.", required=False),
                        ParamSpec("include_health", "boolean", "Include health check results.", required=False, default=False),
                    ],
                    permission_required="readonly",
                ),
                ActionSpec(
                    name="get_module_info",
                    description="Get detailed information about a specific module.",
                    params=[
                        ParamSpec("module_id", "string", "Module ID to inspect."),
                        ParamSpec("include_health", "boolean", "Include health data.", required=False, default=False),
                        ParamSpec("include_metrics", "boolean", "Include metrics.", required=False, default=False),
                    ],
                    permission_required="readonly",
                ),
                ActionSpec(
                    name="enable_module",
                    description="Enable (start) a disabled module.",
                    params=[ParamSpec("module_id", "string", "Module to enable.")],
                    permission_required="power_user",
                ),
                ActionSpec(
                    name="disable_module",
                    description="Disable (stop) a module. System modules cannot be disabled.",
                    params=[
                        ParamSpec("module_id", "string", "Module to disable."),
                        ParamSpec("reason", "string", "Reason for disabling.", required=False),
                    ],
                    permission_required="power_user",
                ),
                ActionSpec(
                    name="pause_module",
                    description="Temporarily suspend a module's actions.",
                    params=[ParamSpec("module_id", "string", "Module to pause.")],
                    permission_required="local_worker",
                ),
                ActionSpec(
                    name="resume_module",
                    description="Resume a paused module.",
                    params=[ParamSpec("module_id", "string", "Module to resume.")],
                    permission_required="local_worker",
                ),
                ActionSpec(
                    name="restart_module",
                    description="Restart a module (stop then start).",
                    params=[
                        ParamSpec("module_id", "string", "Module to restart."),
                        ParamSpec("force", "boolean", "Force restart.", required=False, default=False),
                    ],
                    permission_required="power_user",
                ),
                ActionSpec(
                    name="enable_action",
                    description="Re-enable a previously disabled action.",
                    params=[
                        ParamSpec("module_id", "string", "Module containing the action."),
                        ParamSpec("action", "string", "Action to re-enable."),
                    ],
                    permission_required="local_worker",
                ),
                ActionSpec(
                    name="disable_action",
                    description="Disable a specific action on a module.",
                    params=[
                        ParamSpec("module_id", "string", "Module containing the action."),
                        ParamSpec("action", "string", "Action to disable."),
                        ParamSpec("reason", "string", "Reason for disabling.", required=False),
                    ],
                    permission_required="power_user",
                ),
                ActionSpec(
                    name="get_module_health",
                    description="Run health_check() on a specific module.",
                    params=[ParamSpec("module_id", "string", "Module to health-check.")],
                    permission_required="readonly",
                ),
                ActionSpec(
                    name="get_module_metrics",
                    description="Get operational metrics from a module.",
                    params=[ParamSpec("module_id", "string", "Module ID.")],
                    permission_required="readonly",
                ),
                ActionSpec(
                    name="get_module_state",
                    description="Get a state snapshot from a module.",
                    params=[ParamSpec("module_id", "string", "Module ID.")],
                    permission_required="readonly",
                ),
                ActionSpec(
                    name="list_services",
                    description="List all registered services on the ServiceBus.",
                    params=[],
                    permission_required="readonly",
                ),
                ActionSpec(
                    name="get_system_status",
                    description="Get overall system health summary.",
                    params=[
                        ParamSpec("include_health", "boolean", "Include per-module health.", required=False, default=False),
                    ],
                    permission_required="readonly",
                ),
                ActionSpec(
                    name="update_module_config",
                    description="Update a module's runtime configuration.",
                    params=[
                        ParamSpec("module_id", "string", "Module to configure."),
                        ParamSpec("config", "object", "Configuration dict to apply."),
                    ],
                    permission_required="power_user",
                ),
                # --- v3 Hub / Package Manager actions ---
                ActionSpec(
                    name="install_module",
                    description="Install a module from the hub or local path.",
                    params=[
                        ParamSpec("source", "string", "'hub' or 'local'.", required=False, default="hub"),
                        ParamSpec("module_id", "string", "Module ID (for hub install).", required=False),
                        ParamSpec("path", "string", "Local path (for local install).", required=False),
                        ParamSpec("version", "string", "Version constraint.", required=False, default="latest"),
                    ],
                    permission_required="power_user",
                    risk_level="medium",
                    side_effects=["filesystem_write", "network_request"],
                ),
                ActionSpec(
                    name="uninstall_module",
                    description="Uninstall a community module.",
                    params=[
                        ParamSpec("module_id", "string", "Module to uninstall."),
                    ],
                    permission_required="power_user",
                    risk_level="high",
                    irreversible=True,
                    side_effects=["filesystem_write"],
                ),
                ActionSpec(
                    name="upgrade_module",
                    description="Upgrade an installed module to a new version.",
                    params=[
                        ParamSpec("module_id", "string", "Module to upgrade."),
                        ParamSpec("path", "string", "Path to new version package."),
                    ],
                    permission_required="power_user",
                    risk_level="medium",
                    side_effects=["filesystem_write"],
                ),
                ActionSpec(
                    name="search_hub",
                    description="Search the module hub for available modules.",
                    params=[
                        ParamSpec("query", "string", "Search query."),
                        ParamSpec("limit", "integer", "Max results.", required=False, default=20),
                    ],
                    permission_required="readonly",
                    side_effects=["network_request"],
                ),
                ActionSpec(
                    name="list_installed",
                    description="List all installed community modules.",
                    params=[
                        ParamSpec("enabled_only", "boolean", "Only show enabled.", required=False, default=False),
                    ],
                    permission_required="readonly",
                ),
                ActionSpec(
                    name="verify_module",
                    description="Verify an installed module's integrity (signature + hash).",
                    params=[
                        ParamSpec("module_id", "string", "Module to verify."),
                    ],
                    permission_required="readonly",
                ),
                ActionSpec(
                    name="describe_module",
                    description="Get dynamic self-description from a module.",
                    params=[
                        ParamSpec("module_id", "string", "Module to describe."),
                    ],
                    permission_required="readonly",
                ),
            ],
        )
