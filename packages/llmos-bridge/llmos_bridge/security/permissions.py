"""Security layer — PermissionManager.

High-level API over :class:`PermissionStore` that adds:
  - Risk-level-aware checking (auto-grant LOW risk when configured)
  - Audit event emission on every state change
  - Convenience helpers (``check_or_raise``, ``check_all``)

Usage::

    pm = PermissionManager(store, audit_logger, auto_grant_low_risk=True)
    await pm.check_or_raise("filesystem.write", "filesystem", action="write_file")
    await pm.grant("filesystem.write", "filesystem", PermissionScope.SESSION)
"""

from __future__ import annotations

from typing import Any

from llmos_bridge.events.bus import TOPIC_PERMISSIONS
from llmos_bridge.exceptions import PermissionNotGrantedError
from llmos_bridge.logging import get_logger
from llmos_bridge.security.audit import AuditLogger
from llmos_bridge.security.models import (
    PERMISSION_RISK,
    PermissionGrant,
    PermissionScope,
    RiskLevel,
)
from llmos_bridge.security.permission_store import PermissionStore

log = get_logger(__name__)


class PermissionManager:
    """Central permission check/grant/revoke service with audit trail.

    Parameters
    ----------
    store:
        The persistence backend for permission grants.
    audit:
        AuditLogger for emitting permission events.
    auto_grant_low_risk:
        When ``True``, LOW-risk permissions are granted automatically on
        first check (logged as auto-grant).  Defaults to ``True``.
    """

    def __init__(
        self,
        store: PermissionStore,
        audit: AuditLogger,
        *,
        auto_grant_low_risk: bool = True,
    ) -> None:
        self._store = store
        self._audit = audit
        self._auto_grant_low_risk = auto_grant_low_risk

    # ------------------------------------------------------------------
    # Check
    # ------------------------------------------------------------------

    async def check(self, permission: str, module_id: str) -> bool:
        """Return ``True`` if *permission* is currently granted for *module_id*."""
        return await self._store.is_granted(permission, module_id)

    async def check_or_raise(
        self,
        permission: str,
        module_id: str,
        action: str = "",
    ) -> None:
        """Check permission, auto-granting LOW risk if configured.

        Raises :class:`PermissionNotGrantedError` when the permission is
        missing and cannot be auto-granted.
        """
        if await self._store.is_granted(permission, module_id):
            return

        risk = self.get_risk_level(permission)

        # Auto-grant LOW risk permissions
        if self._auto_grant_low_risk and risk == RiskLevel.LOW:
            grant = PermissionGrant(
                permission=permission,
                module_id=module_id,
                scope=PermissionScope.SESSION,
                granted_by="auto",
                reason="Auto-granted (low risk)",
            )
            await self._store.grant(grant)
            await self._emit_permission_event(
                "permission_auto_granted",
                permission=permission,
                module_id=module_id,
                risk_level=risk.value,
                action=action,
            )
            log.info(
                "permission_auto_granted",
                permission=permission,
                module_id=module_id,
                action=action,
            )
            return

        # Emit check-failed event
        await self._emit_permission_event(
            "permission_check_failed",
            permission=permission,
            module_id=module_id,
            risk_level=risk.value,
            action=action,
        )

        raise PermissionNotGrantedError(
            permission=permission,
            module_id=module_id,
            action=action,
            risk_level=risk.value,
        )

    async def check_all(
        self, permissions: list[str], module_id: str
    ) -> list[str]:
        """Return list of permissions that are NOT granted for *module_id*."""
        missing: list[str] = []
        for perm in permissions:
            if not await self._store.is_granted(perm, module_id):
                missing.append(perm)
        return missing

    # ------------------------------------------------------------------
    # Grant / Revoke
    # ------------------------------------------------------------------

    async def grant(
        self,
        permission: str,
        module_id: str,
        scope: PermissionScope = PermissionScope.SESSION,
        *,
        reason: str = "",
        granted_by: str = "user",
        expires_at: float | None = None,
    ) -> PermissionGrant:
        """Grant a permission and emit an audit event."""
        grant = PermissionGrant(
            permission=permission,
            module_id=module_id,
            scope=scope,
            granted_by=granted_by,
            reason=reason,
            expires_at=expires_at,
        )
        await self._store.grant(grant)
        await self._emit_permission_event(
            "permission_granted",
            permission=permission,
            module_id=module_id,
            scope=scope.value,
            granted_by=granted_by,
            reason=reason,
            risk_level=self.get_risk_level(permission).value,
        )
        log.info(
            "permission_granted",
            permission=permission,
            module_id=module_id,
            scope=scope.value,
        )
        return grant

    async def revoke(self, permission: str, module_id: str) -> bool:
        """Revoke a permission. Returns True if it existed."""
        removed = await self._store.revoke(permission, module_id)
        if removed:
            await self._emit_permission_event(
                "permission_revoked",
                permission=permission,
                module_id=module_id,
            )
            log.info(
                "permission_revoked",
                permission=permission,
                module_id=module_id,
            )
        return removed

    async def revoke_all_for_module(self, module_id: str) -> int:
        """Revoke all permissions for a module. Returns count removed."""
        count = await self._store.revoke_all_for_module(module_id)
        if count:
            await self._emit_permission_event(
                "permissions_revoked_all",
                module_id=module_id,
                count=count,
            )
        return count

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    async def list_grants(
        self, module_id: str | None = None
    ) -> list[PermissionGrant]:
        """List all grants, optionally filtered by module."""
        if module_id:
            return await self._store.get_for_module(module_id)
        return await self._store.get_all()

    async def get_grant(
        self, permission: str, module_id: str
    ) -> PermissionGrant | None:
        """Retrieve a single grant record."""
        return await self._store.get_grant(permission, module_id)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def get_risk_level(permission: str) -> RiskLevel:
        """Return the risk level for a permission (defaults to MEDIUM)."""
        return PERMISSION_RISK.get(permission, RiskLevel.MEDIUM)

    async def _emit_permission_event(
        self, event_type: str, **data: Any
    ) -> None:
        """Emit a permission event directly to the EventBus."""
        record = {"event": event_type, **data}
        await self._audit.bus.emit(TOPIC_PERMISSIONS, record)
