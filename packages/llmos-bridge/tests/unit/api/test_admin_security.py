"""Unit tests — Admin security REST API endpoints.

Tests all six endpoints in llmos_bridge.api.routes.admin_security:
  - GET    /admin/security/permissions          (list permissions)
  - GET    /admin/security/permissions/check    (check single permission)
  - POST   /admin/security/permissions/grant    (grant permission)
  - DELETE /admin/security/permissions/revoke   (revoke permission)
  - GET    /admin/security/status               (security overview)
  - GET    /admin/security/audit                (audit ring buffer)
"""

from __future__ import annotations

from collections import deque
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from llmos_bridge.api.routes.admin_security import router


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_security_module():
    """Create an AsyncMock that mimics the SecurityModule action methods."""
    sec = AsyncMock()
    sec._action_list_permissions = AsyncMock(
        return_value={"grants": [], "count": 0}
    )
    sec._action_check_permission = AsyncMock(
        return_value={"granted": True, "risk_level": "low"}
    )
    sec._action_request_permission = AsyncMock(
        return_value={"granted": True, "permission": "fs.read"}
    )
    sec._action_revoke_permission = AsyncMock(
        return_value={"revoked": True}
    )
    sec._action_get_security_status = AsyncMock(
        return_value={"total_grants": 0, "active_permissions": []}
    )
    return sec


@pytest.fixture
def mock_registry(mock_security_module):
    """Create a mock ModuleRegistry that returns the security module."""
    registry = MagicMock()
    registry.is_available.return_value = True
    registry.get.return_value = mock_security_module
    return registry


@pytest.fixture
def mock_audit_logger():
    """Create a mock audit_logger with a bus ring buffer."""
    bus_mock = MagicMock()
    bus_mock._recent_events = deque([
        {"_topic": "llmos.security", "event": "perm_granted", "_timestamp": 1.0},
        {"_topic": "llmos.plans", "event": "plan_started", "_timestamp": 2.0},
        {"_topic": "llmos.security", "event": "perm_revoked", "_timestamp": 3.0},
    ])
    audit_mock = MagicMock()
    audit_mock._bus = bus_mock
    return audit_mock


@pytest.fixture
def admin_app(mock_registry, mock_audit_logger) -> FastAPI:
    """Build a minimal FastAPI app with the admin-security router."""
    app = FastAPI()
    app.include_router(router)

    # Settings mock — no API token means auth always passes.
    settings_mock = MagicMock()
    settings_mock.security.api_token = None
    app.state.settings = settings_mock
    app.state.module_registry = mock_registry
    app.state.audit_logger = mock_audit_logger
    return app


@pytest.fixture
def client(admin_app) -> TestClient:
    return TestClient(admin_app)


def _make_unavailable_registry() -> MagicMock:
    """Registry where the security module is NOT available."""
    registry = MagicMock()
    registry.is_available.return_value = False
    return registry


def _app_with_registry(registry, audit_logger=None) -> FastAPI:
    """Build app with a custom registry (and optional audit_logger)."""
    app = FastAPI()
    app.include_router(router)
    settings_mock = MagicMock()
    settings_mock.security.api_token = None
    app.state.settings = settings_mock
    app.state.module_registry = registry
    if audit_logger is not None:
        app.state.audit_logger = audit_logger
    return app


# ---------------------------------------------------------------------------
# Tests — GET /admin/security/permissions
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListPermissions:
    """GET /admin/security/permissions"""

    def test_list_permissions_returns_200(self, client, mock_security_module):
        resp = client.get("/admin/security/permissions")

        assert resp.status_code == 200
        body = resp.json()
        assert body == {"grants": [], "count": 0}
        mock_security_module._action_list_permissions.assert_awaited_once_with({})

    def test_list_permissions_with_module_id_filter(self, client, mock_security_module):
        resp = client.get("/admin/security/permissions?module_id=filesystem")

        assert resp.status_code == 200
        mock_security_module._action_list_permissions.assert_awaited_once_with(
            {"module_id": "filesystem"}
        )

    def test_list_permissions_without_module_id_sends_empty_params(
        self, client, mock_security_module
    ):
        """When module_id is omitted, params dict should be empty."""
        resp = client.get("/admin/security/permissions")

        assert resp.status_code == 200
        mock_security_module._action_list_permissions.assert_awaited_once_with({})

    def test_list_permissions_503_when_security_module_unavailable(self):
        app = _app_with_registry(_make_unavailable_registry())
        c = TestClient(app)

        resp = c.get("/admin/security/permissions")

        assert resp.status_code == 503
        assert "security module" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Tests — GET /admin/security/permissions/check
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCheckPermission:
    """GET /admin/security/permissions/check"""

    def test_check_permission_returns_200(self, client, mock_security_module):
        resp = client.get(
            "/admin/security/permissions/check",
            params={"permission": "fs.read", "module_id": "filesystem"},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["granted"] is True
        assert body["risk_level"] == "low"
        mock_security_module._action_check_permission.assert_awaited_once_with(
            {"permission": "fs.read", "module_id": "filesystem"}
        )

    def test_check_permission_missing_permission_param(self, client):
        """When 'permission' is missing, return 400."""
        resp = client.get(
            "/admin/security/permissions/check",
            params={"module_id": "filesystem"},
        )

        assert resp.status_code == 400
        assert "both" in resp.json()["detail"].lower()

    def test_check_permission_missing_module_id_param(self, client):
        """When 'module_id' is missing, return 400."""
        resp = client.get(
            "/admin/security/permissions/check",
            params={"permission": "fs.read"},
        )

        assert resp.status_code == 400
        assert "both" in resp.json()["detail"].lower()

    def test_check_permission_missing_both_params(self, client):
        """When both params are missing, return 400."""
        resp = client.get("/admin/security/permissions/check")

        assert resp.status_code == 400

    def test_check_permission_503_when_security_module_unavailable(self):
        app = _app_with_registry(_make_unavailable_registry())
        c = TestClient(app)

        resp = c.get(
            "/admin/security/permissions/check",
            params={"permission": "fs.read", "module_id": "filesystem"},
        )

        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Tests — POST /admin/security/permissions/grant
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGrantPermission:
    """POST /admin/security/permissions/grant"""

    def test_grant_permission_returns_200(self, client, mock_security_module):
        resp = client.post(
            "/admin/security/permissions/grant",
            json={
                "permission": "fs.read",
                "module_id": "filesystem",
                "reason": "User requested read access",
                "scope": "session",
            },
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["granted"] is True
        assert body["permission"] == "fs.read"
        mock_security_module._action_request_permission.assert_awaited_once_with({
            "permission": "fs.read",
            "module_id": "filesystem",
            "reason": "User requested read access",
            "scope": "session",
        })

    def test_grant_permission_with_defaults(self, client, mock_security_module):
        """When reason and scope are omitted, defaults are used."""
        resp = client.post(
            "/admin/security/permissions/grant",
            json={
                "permission": "os.execute",
                "module_id": "os_exec",
            },
        )

        assert resp.status_code == 200
        mock_security_module._action_request_permission.assert_awaited_once_with({
            "permission": "os.execute",
            "module_id": "os_exec",
            "reason": "",
            "scope": "session",
        })

    def test_grant_permission_permanent_scope(self, client, mock_security_module):
        """Granting with scope=permanent forwards the scope correctly."""
        resp = client.post(
            "/admin/security/permissions/grant",
            json={
                "permission": "fs.write",
                "module_id": "filesystem",
                "reason": "Permanent access needed",
                "scope": "permanent",
            },
        )

        assert resp.status_code == 200
        call_args = mock_security_module._action_request_permission.call_args[0][0]
        assert call_args["scope"] == "permanent"

    def test_grant_permission_503_when_security_module_unavailable(self):
        app = _app_with_registry(_make_unavailable_registry())
        c = TestClient(app)

        resp = c.post(
            "/admin/security/permissions/grant",
            json={"permission": "fs.read", "module_id": "filesystem"},
        )

        assert resp.status_code == 503

    def test_grant_permission_422_when_body_invalid(self, client):
        """Missing required fields in body returns 422."""
        resp = client.post(
            "/admin/security/permissions/grant",
            json={"permission": "fs.read"},  # missing module_id
        )

        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Tests — DELETE /admin/security/permissions/revoke
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRevokePermission:
    """DELETE /admin/security/permissions/revoke"""

    def test_revoke_permission_returns_200(self, client, mock_security_module):
        resp = client.request(
            "DELETE",
            "/admin/security/permissions/revoke",
            json={"permission": "fs.read", "module_id": "filesystem"},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["revoked"] is True
        mock_security_module._action_revoke_permission.assert_awaited_once_with({
            "permission": "fs.read",
            "module_id": "filesystem",
        })

    def test_revoke_permission_503_when_security_module_unavailable(self):
        app = _app_with_registry(_make_unavailable_registry())
        c = TestClient(app)

        resp = c.request(
            "DELETE",
            "/admin/security/permissions/revoke",
            json={"permission": "fs.read", "module_id": "filesystem"},
        )

        assert resp.status_code == 503

    def test_revoke_permission_422_when_body_invalid(self, client):
        """Missing required fields in body returns 422."""
        resp = client.request(
            "DELETE",
            "/admin/security/permissions/revoke",
            json={"permission": "fs.read"},  # missing module_id
        )

        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Tests — GET /admin/security/status
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSecurityStatus:
    """GET /admin/security/status"""

    def test_status_returns_200(self, client, mock_security_module):
        resp = client.get("/admin/security/status")

        assert resp.status_code == 200
        body = resp.json()
        assert body["total_grants"] == 0
        assert "active_permissions" in body
        mock_security_module._action_get_security_status.assert_awaited_once_with({})

    def test_status_503_when_security_module_unavailable(self):
        app = _app_with_registry(_make_unavailable_registry())
        c = TestClient(app)

        resp = c.get("/admin/security/status")

        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Tests — GET /admin/security/audit
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAuditEvents:
    """GET /admin/security/audit"""

    def test_audit_returns_all_events(self, client):
        resp = client.get("/admin/security/audit")

        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 3
        assert len(body["events"]) == 3

    def test_audit_with_limit(self, client):
        """limit=1 returns only the last event."""
        resp = client.get("/admin/security/audit?limit=1")

        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert len(body["events"]) == 1
        # The last event in the ring buffer (timestamp 3.0)
        assert body["events"][0]["_timestamp"] == 3.0

    def test_audit_with_topic_filter(self, client):
        """topic filter returns only events matching the topic."""
        resp = client.get("/admin/security/audit?topic=llmos.security")

        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 2
        for evt in body["events"]:
            assert evt["_topic"] == "llmos.security"

    def test_audit_with_topic_and_limit(self, client):
        """topic + limit applies topic filter first, then limit."""
        resp = client.get(
            "/admin/security/audit?topic=llmos.security&limit=1"
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["events"][0]["_topic"] == "llmos.security"
        # Should be the last matching event (timestamp 3.0)
        assert body["events"][0]["_timestamp"] == 3.0

    def test_audit_with_nonexistent_topic(self, client):
        """A topic with no matching events returns empty list."""
        resp = client.get("/admin/security/audit?topic=llmos.nonexistent")

        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 0
        assert body["events"] == []

    def test_audit_when_no_audit_logger(self):
        """When audit_logger is not on app.state, return empty."""
        app = FastAPI()
        app.include_router(router)
        settings_mock = MagicMock()
        settings_mock.security.api_token = None
        app.state.settings = settings_mock
        # Deliberately do NOT set app.state.audit_logger
        c = TestClient(app)

        resp = c.get("/admin/security/audit")

        assert resp.status_code == 200
        body = resp.json()
        assert body == {"events": [], "count": 0}

    def test_audit_when_audit_logger_has_no_bus(self):
        """When audit_logger exists but has no _bus attr, return empty."""
        app = FastAPI()
        app.include_router(router)
        settings_mock = MagicMock()
        settings_mock.security.api_token = None
        app.state.settings = settings_mock
        # audit_logger exists but _bus is None
        audit_mock = MagicMock()
        audit_mock._bus = None
        app.state.audit_logger = audit_mock
        c = TestClient(app)

        resp = c.get("/admin/security/audit")

        assert resp.status_code == 200
        body = resp.json()
        assert body == {"events": [], "count": 0}

    def test_audit_when_bus_has_no_recent_events(self):
        """When bus exists but has no _recent_events, return empty."""
        app = FastAPI()
        app.include_router(router)
        settings_mock = MagicMock()
        settings_mock.security.api_token = None
        app.state.settings = settings_mock
        bus_mock = MagicMock(spec=[])  # spec=[] means no attributes at all
        audit_mock = MagicMock()
        audit_mock._bus = bus_mock
        app.state.audit_logger = audit_mock
        c = TestClient(app)

        resp = c.get("/admin/security/audit")

        assert resp.status_code == 200
        body = resp.json()
        assert body == {"events": [], "count": 0}


# ---------------------------------------------------------------------------
# Tests — _get_security_module helper (cross-cutting)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetSecurityModuleHelper:
    """Verify _get_security_module raises 503 consistently across all
    endpoints that depend on the security module."""

    _SECURITY_MODULE_ENDPOINTS = [
        ("GET", "/admin/security/permissions", None),
        (
            "GET",
            "/admin/security/permissions/check",
            {"params": {"permission": "fs.read", "module_id": "filesystem"}},
        ),
        (
            "POST",
            "/admin/security/permissions/grant",
            {"json": {"permission": "fs.read", "module_id": "filesystem"}},
        ),
        (
            "DELETE",
            "/admin/security/permissions/revoke",
            {"json": {"permission": "fs.read", "module_id": "filesystem"}},
        ),
        ("GET", "/admin/security/status", None),
    ]

    @pytest.mark.parametrize(
        "method,path,kwargs",
        _SECURITY_MODULE_ENDPOINTS,
        ids=[
            "list_permissions",
            "check_permission",
            "grant_permission",
            "revoke_permission",
            "security_status",
        ],
    )
    def test_all_module_endpoints_return_503(self, method, path, kwargs):
        """Every endpoint that uses _get_security_module returns 503
        when the security module is not available."""
        app = _app_with_registry(_make_unavailable_registry())
        c = TestClient(app)

        resp = c.request(method, path, **(kwargs or {}))

        assert resp.status_code == 503
        detail = resp.json()["detail"]
        assert "security module" in detail.lower()
        assert "enable_decorators" in detail.lower()
