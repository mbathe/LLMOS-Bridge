"""Unit tests for PermissionManager.

Tests the high-level permission check/grant/revoke API, including
risk-aware auto-granting and audit event emission.
"""

from __future__ import annotations

import pytest

from llmos_bridge.exceptions import PermissionNotGrantedError
from llmos_bridge.security.audit import AuditLogger
from llmos_bridge.security.models import (
    PERMISSION_RISK,
    Permission,
    PermissionGrant,
    PermissionScope,
    RiskLevel,
)
from llmos_bridge.security.permission_store import PermissionStore
from llmos_bridge.security.permissions import PermissionManager


@pytest.fixture()
async def store(tmp_path):
    """Create and initialise a real SQLite-backed PermissionStore."""
    s = PermissionStore(tmp_path / "permissions.db")
    await s.init()
    yield s
    await s.close()


@pytest.fixture()
def audit():
    """AuditLogger with NullEventBus (no-op backend)."""
    return AuditLogger()


@pytest.fixture()
def manager(store, audit):
    """PermissionManager with default auto_grant_low_risk=True."""
    return PermissionManager(store, audit)


@pytest.mark.unit
class TestPermissionManager:
    """Tests for PermissionManager check / grant / revoke lifecycle."""

    # ------------------------------------------------------------------
    # 1. check returns False for a permission that was never granted
    # ------------------------------------------------------------------
    async def test_check_returns_false_for_missing_permission(self, manager):
        result = await manager.check("filesystem.write", "filesystem")
        assert result is False

    # ------------------------------------------------------------------
    # 2. check returns True after an explicit grant
    # ------------------------------------------------------------------
    async def test_check_returns_true_after_grant(self, manager):
        await manager.grant("filesystem.write", "filesystem", PermissionScope.SESSION)
        result = await manager.check("filesystem.write", "filesystem")
        assert result is True

    # ------------------------------------------------------------------
    # 3. check_or_raise auto-grants LOW risk permissions
    # ------------------------------------------------------------------
    async def test_check_or_raise_auto_grants_low_risk(self, manager):
        # filesystem.read is LOW risk
        await manager.check_or_raise("filesystem.read", "filesystem", action="read_file")
        # After auto-grant, a plain check should return True
        assert await manager.check("filesystem.read", "filesystem") is True

    # ------------------------------------------------------------------
    # 4. check_or_raise raises for MEDIUM risk when not granted
    # ------------------------------------------------------------------
    async def test_check_or_raise_raises_for_medium_risk(self, manager):
        with pytest.raises(PermissionNotGrantedError) as exc_info:
            await manager.check_or_raise(
                "filesystem.write", "filesystem", action="write_file"
            )
        assert exc_info.value.permission == "filesystem.write"
        assert exc_info.value.module_id == "filesystem"

    # ------------------------------------------------------------------
    # 5. check_or_raise succeeds after explicit grant for MEDIUM risk
    # ------------------------------------------------------------------
    async def test_check_or_raise_succeeds_after_explicit_grant(self, manager):
        await manager.grant("filesystem.write", "filesystem", PermissionScope.SESSION)
        # Should not raise
        await manager.check_or_raise("filesystem.write", "filesystem", action="write_file")

    # ------------------------------------------------------------------
    # 6. check_or_raise with auto_grant_low_risk=False raises for LOW
    # ------------------------------------------------------------------
    async def test_check_or_raise_no_auto_grant_raises_for_low(self, store, audit):
        pm = PermissionManager(store, audit, auto_grant_low_risk=False)
        with pytest.raises(PermissionNotGrantedError):
            await pm.check_or_raise("filesystem.read", "filesystem", action="read_file")

    # ------------------------------------------------------------------
    # 7. check_all returns list of missing permissions
    # ------------------------------------------------------------------
    async def test_check_all_returns_missing(self, manager):
        await manager.grant("filesystem.read", "filesystem", PermissionScope.SESSION)
        missing = await manager.check_all(
            ["filesystem.read", "filesystem.write", "filesystem.delete"],
            "filesystem",
        )
        assert "filesystem.read" not in missing
        assert "filesystem.write" in missing
        assert "filesystem.delete" in missing

    # ------------------------------------------------------------------
    # 8. check_all returns empty list when all are granted
    # ------------------------------------------------------------------
    async def test_check_all_returns_empty_when_all_granted(self, manager):
        await manager.grant("filesystem.read", "filesystem", PermissionScope.SESSION)
        await manager.grant("filesystem.write", "filesystem", PermissionScope.SESSION)
        missing = await manager.check_all(
            ["filesystem.read", "filesystem.write"], "filesystem"
        )
        assert missing == []

    # ------------------------------------------------------------------
    # 9. grant returns a PermissionGrant object
    # ------------------------------------------------------------------
    async def test_grant_returns_permission_grant(self, manager):
        grant = await manager.grant(
            "filesystem.write",
            "filesystem",
            PermissionScope.PERMANENT,
            reason="user approved",
            granted_by="admin",
        )
        assert isinstance(grant, PermissionGrant)
        assert grant.permission == "filesystem.write"
        assert grant.module_id == "filesystem"
        assert grant.scope == PermissionScope.PERMANENT
        assert grant.reason == "user approved"
        assert grant.granted_by == "admin"

    # ------------------------------------------------------------------
    # 10. revoke returns True for an existing grant
    # ------------------------------------------------------------------
    async def test_revoke_returns_true_for_existing(self, manager):
        await manager.grant("filesystem.write", "filesystem", PermissionScope.SESSION)
        removed = await manager.revoke("filesystem.write", "filesystem")
        assert removed is True
        # Confirm it's actually gone
        assert await manager.check("filesystem.write", "filesystem") is False

    # ------------------------------------------------------------------
    # 11. revoke returns False for a missing grant
    # ------------------------------------------------------------------
    async def test_revoke_returns_false_for_missing(self, manager):
        removed = await manager.revoke("filesystem.write", "filesystem")
        assert removed is False

    # ------------------------------------------------------------------
    # 12. revoke_all_for_module removes all grants and returns count
    # ------------------------------------------------------------------
    async def test_revoke_all_for_module(self, manager):
        await manager.grant("filesystem.read", "filesystem", PermissionScope.SESSION)
        await manager.grant("filesystem.write", "filesystem", PermissionScope.SESSION)
        await manager.grant("filesystem.delete", "filesystem", PermissionScope.SESSION)
        count = await manager.revoke_all_for_module("filesystem")
        assert count == 3
        assert await manager.check("filesystem.read", "filesystem") is False

    # ------------------------------------------------------------------
    # 13. list_grants returns all grants across modules
    # ------------------------------------------------------------------
    async def test_list_grants_returns_all(self, manager):
        await manager.grant("filesystem.read", "filesystem", PermissionScope.SESSION)
        await manager.grant("os.process.execute", "os_exec", PermissionScope.SESSION)
        grants = await manager.list_grants()
        permissions = {g.permission for g in grants}
        assert "filesystem.read" in permissions
        assert "os.process.execute" in permissions
        assert len(grants) == 2

    # ------------------------------------------------------------------
    # 14. list_grants with module_id filter
    # ------------------------------------------------------------------
    async def test_list_grants_filtered_by_module(self, manager):
        await manager.grant("filesystem.read", "filesystem", PermissionScope.SESSION)
        await manager.grant("filesystem.write", "filesystem", PermissionScope.SESSION)
        await manager.grant("os.process.execute", "os_exec", PermissionScope.SESSION)
        grants = await manager.list_grants(module_id="filesystem")
        assert len(grants) == 2
        assert all(g.module_id == "filesystem" for g in grants)

    # ------------------------------------------------------------------
    # 15. get_risk_level returns correct level from PERMISSION_RISK
    # ------------------------------------------------------------------
    async def test_get_risk_level(self, manager):
        assert manager.get_risk_level(Permission.FILESYSTEM_READ) == RiskLevel.LOW
        assert manager.get_risk_level(Permission.FILESYSTEM_WRITE) == RiskLevel.MEDIUM
        assert manager.get_risk_level(Permission.FILESYSTEM_DELETE) == RiskLevel.HIGH
        assert manager.get_risk_level(Permission.CREDENTIALS) == RiskLevel.CRITICAL
        # Unknown permissions default to MEDIUM
        assert manager.get_risk_level("totally.unknown") == RiskLevel.MEDIUM
