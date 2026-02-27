"""Security IML module — Expose permission management to the LLM.

This module lets the LLM:
  - List all granted permissions
  - Check if a specific permission is granted
  - Request a new permission (auto-granted in Phase 1; Phase 2: approval gate)
  - Revoke a granted permission
  - Get a security status overview
  - List recent audit events (stub for Phase 3)

MODULE_ID: ``security``
"""

from __future__ import annotations

from typing import Any

from llmos_bridge.modules.base import BaseModule, Platform
from llmos_bridge.modules.manifest import ActionSpec, ModuleManifest, ParamSpec
from llmos_bridge.security.decorators import audit_trail, sensitive_action
from llmos_bridge.security.models import PermissionGrant, PermissionScope, RiskLevel


class SecurityModule(BaseModule):
    MODULE_ID = "security"
    VERSION = "1.0.0"
    SUPPORTED_PLATFORMS = [Platform.ALL]

    def __init__(self) -> None:
        super().__init__()
        self._security_manager: Any | None = None

    def set_security_manager(self, security_manager: Any) -> None:
        """Inject the SecurityManager (also stored as self._security for decorators)."""
        self._security_manager = security_manager
        self._security = security_manager

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    async def _action_list_permissions(self, params: dict[str, Any]) -> dict[str, Any]:
        """List all granted permissions, optionally filtered by module."""
        if self._security_manager is None:
            return {"grants": [], "error": "SecurityManager not configured"}

        module_id = params.get("module_id")
        grants = await self._security_manager.permission_manager.list_grants(module_id)
        return {
            "grants": [g.to_dict() for g in grants],
            "count": len(grants),
        }

    async def _action_check_permission(self, params: dict[str, Any]) -> dict[str, Any]:
        """Check if a specific permission is granted for a module."""
        if self._security_manager is None:
            return {"granted": False, "error": "SecurityManager not configured"}

        permission = params["permission"]
        module_id = params["module_id"]
        pm = self._security_manager.permission_manager

        granted = await pm.check(permission, module_id)
        risk_level = pm.get_risk_level(permission)
        grant = await pm.get_grant(permission, module_id) if granted else None

        result: dict[str, Any] = {
            "permission": permission,
            "module_id": module_id,
            "granted": granted,
            "risk_level": risk_level.value,
        }
        if grant:
            result["grant"] = grant.to_dict()
        return result

    @audit_trail("detailed")
    async def _action_request_permission(self, params: dict[str, Any]) -> dict[str, Any]:
        """Request a permission grant for a module.

        In Phase 1, permissions are granted directly.
        In Phase 2, HIGH/CRITICAL permissions will go through the approval gate.
        """
        if self._security_manager is None:
            return {"granted": False, "error": "SecurityManager not configured"}

        permission = params["permission"]
        module_id = params["module_id"]
        reason = params.get("reason", "")
        scope_str = params.get("scope", "session")
        scope = PermissionScope(scope_str)

        pm = self._security_manager.permission_manager
        grant = await pm.grant(
            permission, module_id, scope, reason=reason, granted_by="llm"
        )
        return {
            "permission": permission,
            "module_id": module_id,
            "granted": True,
            "grant": grant.to_dict(),
        }

    @audit_trail("detailed")
    @sensitive_action(RiskLevel.HIGH)
    async def _action_revoke_permission(self, params: dict[str, Any]) -> dict[str, Any]:
        """Revoke a granted permission."""
        if self._security_manager is None:
            return {"revoked": False, "error": "SecurityManager not configured"}

        permission = params["permission"]
        module_id = params["module_id"]
        pm = self._security_manager.permission_manager

        revoked = await pm.revoke(permission, module_id)
        return {
            "permission": permission,
            "module_id": module_id,
            "revoked": revoked,
        }

    async def _action_get_security_status(self, params: dict[str, Any]) -> dict[str, Any]:
        """Get a security overview: profile, total grants, grants by risk level."""
        if self._security_manager is None:
            return {"error": "SecurityManager not configured"}

        pm = self._security_manager.permission_manager
        all_grants = await pm.list_grants()

        # Group by module
        by_module: dict[str, int] = {}
        by_risk: dict[str, int] = {"low": 0, "medium": 0, "high": 0, "critical": 0}
        for g in all_grants:
            by_module[g.module_id] = by_module.get(g.module_id, 0) + 1
            risk = pm.get_risk_level(g.permission).value
            by_risk[risk] = by_risk.get(risk, 0) + 1

        return {
            "total_grants": len(all_grants),
            "grants_by_module": by_module,
            "grants_by_risk_level": by_risk,
        }

    async def _action_list_audit_events(self, params: dict[str, Any]) -> dict[str, Any]:
        """List recent audit events (stub — Phase 3 will add full query support)."""
        return {
            "events": [],
            "message": "Full audit event query support coming in Phase 3.",
        }

    # ------------------------------------------------------------------
    # Manifest
    # ------------------------------------------------------------------

    def get_manifest(self) -> ModuleManifest:
        return ModuleManifest(
            module_id=self.MODULE_ID,
            version=self.VERSION,
            description=(
                "Query and manage OS-level permissions. Check which permissions are "
                "granted, request new ones, or revoke existing grants."
            ),
            platforms=["all"],
            tags=["security", "permissions", "audit"],
            actions=[
                ActionSpec(
                    name="list_permissions",
                    description="List all currently granted OS-level permissions.",
                    params=[
                        ParamSpec(
                            "module_id", "string",
                            "Filter by module ID (optional).",
                            required=False,
                        ),
                    ],
                    returns="object",
                    returns_description='{"grants": [...], "count": int}',
                    permission_required="readonly",
                ),
                ActionSpec(
                    name="check_permission",
                    description="Check if a specific OS-level permission is granted for a module.",
                    params=[
                        ParamSpec("permission", "string", "Permission identifier (e.g. 'filesystem.write')."),
                        ParamSpec("module_id", "string", "Module to check permission for."),
                    ],
                    returns="object",
                    returns_description='{"granted": bool, "risk_level": str, "grant": {...}|null}',
                    permission_required="readonly",
                ),
                ActionSpec(
                    name="request_permission",
                    description=(
                        "Request an OS-level permission for a module. "
                        "LOW-risk permissions are auto-granted. "
                        "MEDIUM/HIGH/CRITICAL permissions require explicit approval."
                    ),
                    params=[
                        ParamSpec("permission", "string", "Permission identifier to request."),
                        ParamSpec("module_id", "string", "Module requesting the permission."),
                        ParamSpec("reason", "string", "Why this permission is needed.", required=False),
                        ParamSpec(
                            "scope", "string",
                            "Grant scope: 'session' (default) or 'permanent'.",
                            required=False, default="session",
                            enum=["session", "permanent"],
                        ),
                    ],
                    permission_required="local_worker",
                ),
                ActionSpec(
                    name="revoke_permission",
                    description="Revoke a previously granted OS-level permission.",
                    params=[
                        ParamSpec("permission", "string", "Permission identifier to revoke."),
                        ParamSpec("module_id", "string", "Module to revoke permission from."),
                    ],
                    permission_required="power_user",
                ),
                ActionSpec(
                    name="get_security_status",
                    description="Get a security overview: total grants, grouped by module and risk level.",
                    params=[],
                    returns="object",
                    returns_description='{"total_grants": int, "grants_by_module": {...}, "grants_by_risk_level": {...}}',
                    permission_required="readonly",
                ),
                ActionSpec(
                    name="list_audit_events",
                    description="List recent security audit events (Phase 3: full query support).",
                    params=[
                        ParamSpec("limit", "integer", "Maximum events to return.", required=False, default=50),
                    ],
                    returns="object",
                    permission_required="readonly",
                ),
            ],
        )
