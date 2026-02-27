"""Unit tests — SecurityModule (6 IML actions).

Tests the 6 actions exposed by SecurityModule:
  - list_permissions, check_permission, request_permission,
    revoke_permission, get_security_status, list_audit_events

Uses a real SecurityManager stack (PermissionStore + PermissionManager +
ActionRateLimiter + AuditLogger) backed by a temporary SQLite database.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from llmos_bridge.modules.security.module import SecurityModule
from llmos_bridge.security.audit import AuditLogger
from llmos_bridge.security.manager import SecurityManager
from llmos_bridge.security.models import PermissionScope
from llmos_bridge.security.permission_store import PermissionStore
from llmos_bridge.security.permissions import PermissionManager
from llmos_bridge.security.rate_limiter import ActionRateLimiter


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def permission_store(tmp_path: Path) -> PermissionStore:
    store = PermissionStore(tmp_path / "permissions.db")
    await store.init()
    yield store  # type: ignore[misc]
    await store.close()


@pytest_asyncio.fixture
async def security_manager(permission_store: PermissionStore) -> SecurityManager:
    audit = AuditLogger()  # NullEventBus — no file I/O
    pm = PermissionManager(store=permission_store, audit=audit)
    limiter = ActionRateLimiter()
    return SecurityManager(
        permission_manager=pm,
        rate_limiter=limiter,
        audit=audit,
    )


@pytest_asyncio.fixture
async def security_module(security_manager: SecurityManager) -> SecurityModule:
    """SecurityModule with a fully wired SecurityManager."""
    mod = SecurityModule()
    mod.set_security_manager(security_manager)
    return mod


@pytest_asyncio.fixture
async def bare_module() -> SecurityModule:
    """SecurityModule without a SecurityManager (not configured)."""
    return SecurityModule()


# ---------------------------------------------------------------------------
# Actions when SecurityManager is NOT configured
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSecurityModuleNoManager:
    """Every action returns a descriptive error dict when the manager is missing."""

    async def test_list_permissions_returns_error(
        self, bare_module: SecurityModule
    ) -> None:
        result = await bare_module._action_list_permissions({})
        assert result["error"] == "SecurityManager not configured"
        assert result["grants"] == []

    async def test_check_permission_returns_error(
        self, bare_module: SecurityModule
    ) -> None:
        result = await bare_module._action_check_permission(
            {"permission": "filesystem.read", "module_id": "filesystem"}
        )
        assert result["granted"] is False
        assert result["error"] == "SecurityManager not configured"

    async def test_request_permission_returns_error(
        self, bare_module: SecurityModule
    ) -> None:
        result = await bare_module._action_request_permission(
            {"permission": "filesystem.read", "module_id": "filesystem"}
        )
        assert result["granted"] is False
        assert result["error"] == "SecurityManager not configured"

    async def test_revoke_permission_returns_error(
        self, bare_module: SecurityModule
    ) -> None:
        result = await bare_module._action_revoke_permission(
            {"permission": "filesystem.read", "module_id": "filesystem"}
        )
        assert result["revoked"] is False
        assert result["error"] == "SecurityManager not configured"

    async def test_get_security_status_returns_error(
        self, bare_module: SecurityModule
    ) -> None:
        result = await bare_module._action_get_security_status({})
        assert result["error"] == "SecurityManager not configured"

    async def test_list_audit_events_works_without_manager(
        self, bare_module: SecurityModule
    ) -> None:
        """list_audit_events is a stub that never touches SecurityManager."""
        result = await bare_module._action_list_audit_events({})
        assert result["events"] == []
        assert "Phase 3" in result["message"]


# ---------------------------------------------------------------------------
# list_permissions
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListPermissions:
    async def test_empty_store(self, security_module: SecurityModule) -> None:
        result = await security_module._action_list_permissions({})
        assert result["grants"] == []
        assert result["count"] == 0

    async def test_returns_granted_permissions(
        self,
        security_module: SecurityModule,
        security_manager: SecurityManager,
    ) -> None:
        pm = security_manager.permission_manager
        await pm.grant("filesystem.read", "filesystem", PermissionScope.SESSION)
        await pm.grant("filesystem.write", "filesystem", PermissionScope.SESSION)

        result = await security_module._action_list_permissions({})
        assert result["count"] == 2
        assert len(result["grants"]) == 2
        permissions = {g["permission"] for g in result["grants"]}
        assert permissions == {"filesystem.read", "filesystem.write"}

    async def test_filter_by_module_id(
        self,
        security_module: SecurityModule,
        security_manager: SecurityManager,
    ) -> None:
        pm = security_manager.permission_manager
        await pm.grant("filesystem.read", "filesystem", PermissionScope.SESSION)
        await pm.grant("os.process.execute", "os_exec", PermissionScope.SESSION)

        result = await security_module._action_list_permissions(
            {"module_id": "filesystem"}
        )
        assert result["count"] == 1
        assert result["grants"][0]["module_id"] == "filesystem"


# ---------------------------------------------------------------------------
# check_permission — granted vs not granted
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCheckPermission:
    async def test_not_granted(self, security_module: SecurityModule) -> None:
        result = await security_module._action_check_permission(
            {"permission": "filesystem.write", "module_id": "filesystem"}
        )
        assert result["granted"] is False
        assert result["permission"] == "filesystem.write"
        assert result["module_id"] == "filesystem"
        assert result["risk_level"] == "medium"
        assert "grant" not in result

    async def test_granted_includes_grant_record(
        self,
        security_module: SecurityModule,
        security_manager: SecurityManager,
    ) -> None:
        pm = security_manager.permission_manager
        await pm.grant("filesystem.read", "filesystem", PermissionScope.SESSION)

        result = await security_module._action_check_permission(
            {"permission": "filesystem.read", "module_id": "filesystem"}
        )
        assert result["granted"] is True
        assert result["risk_level"] == "low"
        assert result["grant"]["permission"] == "filesystem.read"
        assert result["grant"]["module_id"] == "filesystem"
        assert result["grant"]["scope"] == "session"


# ---------------------------------------------------------------------------
# request_permission — different scopes
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRequestPermission:
    async def test_default_scope_is_session(
        self, security_module: SecurityModule
    ) -> None:
        result = await security_module._action_request_permission(
            {"permission": "filesystem.read", "module_id": "filesystem"}
        )
        assert result["granted"] is True
        assert result["grant"]["scope"] == "session"
        assert result["grant"]["granted_by"] == "llm"

    async def test_permanent_scope(self, security_module: SecurityModule) -> None:
        result = await security_module._action_request_permission(
            {
                "permission": "filesystem.write",
                "module_id": "filesystem",
                "scope": "permanent",
                "reason": "Need write access for reports",
            }
        )
        assert result["granted"] is True
        assert result["grant"]["scope"] == "permanent"
        assert result["grant"]["reason"] == "Need write access for reports"

    async def test_session_scope_explicit(
        self, security_module: SecurityModule
    ) -> None:
        result = await security_module._action_request_permission(
            {
                "permission": "network.read",
                "module_id": "api_http",
                "scope": "session",
                "reason": "Fetch remote config",
            }
        )
        assert result["granted"] is True
        assert result["permission"] == "network.read"
        assert result["module_id"] == "api_http"
        assert result["grant"]["scope"] == "session"

    async def test_grant_then_check_confirms_granted(
        self, security_module: SecurityModule
    ) -> None:
        """After requesting, check_permission should confirm the grant."""
        await security_module._action_request_permission(
            {"permission": "filesystem.delete", "module_id": "filesystem"}
        )
        result = await security_module._action_check_permission(
            {"permission": "filesystem.delete", "module_id": "filesystem"}
        )
        assert result["granted"] is True
        assert result["risk_level"] == "high"


# ---------------------------------------------------------------------------
# revoke_permission
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRevokePermission:
    async def test_revoke_existing_permission(
        self,
        security_module: SecurityModule,
        security_manager: SecurityManager,
    ) -> None:
        pm = security_manager.permission_manager
        await pm.grant("filesystem.read", "filesystem", PermissionScope.SESSION)

        result = await security_module._action_revoke_permission(
            {"permission": "filesystem.read", "module_id": "filesystem"}
        )
        assert result["revoked"] is True
        assert result["permission"] == "filesystem.read"
        assert result["module_id"] == "filesystem"

    async def test_revoke_nonexistent_returns_false(
        self, security_module: SecurityModule
    ) -> None:
        result = await security_module._action_revoke_permission(
            {"permission": "filesystem.read", "module_id": "filesystem"}
        )
        assert result["revoked"] is False

    async def test_revoke_then_check_shows_not_granted(
        self,
        security_module: SecurityModule,
        security_manager: SecurityManager,
    ) -> None:
        """After revoking, check_permission should report not granted."""
        pm = security_manager.permission_manager
        await pm.grant("filesystem.write", "filesystem", PermissionScope.SESSION)

        await security_module._action_revoke_permission(
            {"permission": "filesystem.write", "module_id": "filesystem"}
        )
        result = await security_module._action_check_permission(
            {"permission": "filesystem.write", "module_id": "filesystem"}
        )
        assert result["granted"] is False


# ---------------------------------------------------------------------------
# get_security_status — correct breakdowns
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetSecurityStatus:
    async def test_empty_status(self, security_module: SecurityModule) -> None:
        result = await security_module._action_get_security_status({})
        assert result["total_grants"] == 0
        assert result["grants_by_module"] == {}
        assert result["grants_by_risk_level"] == {
            "low": 0,
            "medium": 0,
            "high": 0,
            "critical": 0,
        }

    async def test_status_with_mixed_grants(
        self,
        security_module: SecurityModule,
        security_manager: SecurityManager,
    ) -> None:
        pm = security_manager.permission_manager
        # LOW risk
        await pm.grant("filesystem.read", "filesystem", PermissionScope.SESSION)
        # MEDIUM risk
        await pm.grant("filesystem.write", "filesystem", PermissionScope.SESSION)
        # HIGH risk
        await pm.grant("filesystem.delete", "filesystem", PermissionScope.SESSION)
        # MEDIUM risk (different module)
        await pm.grant("os.process.execute", "os_exec", PermissionScope.SESSION)

        result = await security_module._action_get_security_status({})
        assert result["total_grants"] == 4
        assert result["grants_by_module"] == {"filesystem": 3, "os_exec": 1}
        assert result["grants_by_risk_level"]["low"] == 1
        assert result["grants_by_risk_level"]["medium"] == 2
        assert result["grants_by_risk_level"]["high"] == 1
        assert result["grants_by_risk_level"]["critical"] == 0


# ---------------------------------------------------------------------------
# list_audit_events (stub)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListAuditEvents:
    async def test_returns_stub_response(
        self, security_module: SecurityModule
    ) -> None:
        result = await security_module._action_list_audit_events({})
        assert result["events"] == []
        assert "Phase 3" in result["message"]

    async def test_ignores_limit_param(
        self, security_module: SecurityModule
    ) -> None:
        """The stub ignores the limit parameter (Phase 3 will honour it)."""
        result = await security_module._action_list_audit_events({"limit": 10})
        assert result["events"] == []


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSecurityModuleManifest:
    def test_module_id_and_version(self) -> None:
        mod = SecurityModule()
        assert mod.MODULE_ID == "security"
        assert mod.VERSION == "1.0.0"

    def test_manifest_declares_six_actions(self) -> None:
        mod = SecurityModule()
        manifest = mod.get_manifest()
        action_names = [a.name for a in manifest.actions]
        assert len(action_names) == 6
        assert set(action_names) == {
            "list_permissions",
            "check_permission",
            "request_permission",
            "revoke_permission",
            "get_security_status",
            "list_audit_events",
        }
