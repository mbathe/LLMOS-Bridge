"""Unit tests -- Admin module REST API endpoints.

Tests all endpoints in llmos_bridge.api.routes.admin_modules:
  - GET    /admin/modules
  - GET    /admin/modules/{id}
  - GET    /admin/modules/{id}/health
  - GET    /admin/modules/{id}/metrics
  - GET    /admin/modules/{id}/state
  - GET    /admin/modules/{id}/describe
  - GET    /admin/modules/{id}/manifest
  - GET    /admin/modules/{id}/docs
  - POST   /admin/modules/{id}/enable
  - POST   /admin/modules/{id}/disable
  - POST   /admin/modules/{id}/pause
  - POST   /admin/modules/{id}/resume
  - POST   /admin/modules/{id}/restart
  - PUT    /admin/modules/{id}/config
  - POST   /admin/modules/{id}/actions/{action}/enable
  - POST   /admin/modules/{id}/actions/{action}/disable
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from llmos_bridge.api.routes.admin_modules import router


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_module_manager():
    """Create a mock ModuleManagerModule with all action methods."""
    mm = AsyncMock()
    mm._action_list_modules = AsyncMock(
        return_value={"modules": [{"module_id": "fs"}], "count": 1},
    )
    mm._action_get_module_info = AsyncMock(
        return_value={"module_id": "fs", "status": "ready"},
    )
    mm._action_get_module_health = AsyncMock(
        return_value={"module_id": "fs", "healthy": True, "checks": {}},
    )
    mm._action_get_module_metrics = AsyncMock(
        return_value={"module_id": "fs", "actions_executed": 42, "uptime": 3600.0},
    )
    mm._action_get_module_state = AsyncMock(
        return_value={"module_id": "fs", "state": "running", "config": {}},
    )
    mm._action_describe_module = AsyncMock(
        return_value={"module_id": "fs", "description": "Filesystem module"},
    )
    mm._action_enable_module = AsyncMock(
        return_value={"module_id": "fs", "enabled": True},
    )
    mm._action_disable_module = AsyncMock(
        return_value={"module_id": "fs", "enabled": False},
    )
    mm._action_pause_module = AsyncMock(
        return_value={"module_id": "fs", "paused": True},
    )
    mm._action_resume_module = AsyncMock(
        return_value={"module_id": "fs", "paused": False},
    )
    mm._action_restart_module = AsyncMock(
        return_value={"module_id": "fs", "restarted": True},
    )
    mm._action_update_module_config = AsyncMock(
        return_value={"module_id": "fs", "config_applied": True},
    )
    mm._action_enable_action = AsyncMock(
        return_value={"module_id": "fs", "action": "read_file", "enabled": True},
    )
    mm._action_disable_action = AsyncMock(
        return_value={"module_id": "fs", "action": "read_file", "disabled": True},
    )
    return mm


@pytest.fixture
def mock_registry(mock_module_manager):
    """Registry that provides the module_manager."""
    registry = MagicMock()

    def _is_available(module_id: str) -> bool:
        if module_id == "module_manager":
            return True
        if module_id == "fs":
            return True
        return False

    def _get(module_id: str):
        if module_id == "module_manager":
            return mock_module_manager
        if module_id == "fs":
            # Return a mock module with get_manifest and __class__
            mod = MagicMock()
            manifest = MagicMock()
            manifest.to_dict.return_value = {
                "module_id": "fs",
                "version": "1.0.0",
                "description": "Filesystem operations",
                "actions": ["read_file", "write_file"],
            }
            mod.get_manifest.return_value = manifest
            return mod
        raise KeyError(f"Module '{module_id}' not found")

    registry.is_available.side_effect = _is_available
    registry.get.side_effect = _get
    return registry


@pytest.fixture
def admin_app(mock_registry) -> FastAPI:
    """Minimal FastAPI app with the admin_modules router."""
    app = FastAPI()
    app.include_router(router)
    settings_mock = MagicMock()
    settings_mock.security.api_token = None  # No auth required
    app.state.settings = settings_mock
    app.state.module_registry = mock_registry
    return app


@pytest.fixture
def client(admin_app) -> TestClient:
    """Test client for the admin app."""
    return TestClient(admin_app)


# ---------------------------------------------------------------------------
# Helper: app where module_manager is NOT available
# ---------------------------------------------------------------------------


def _make_app_without_mm() -> FastAPI:
    """Build an app where module_manager is NOT registered (503 path)."""
    app = FastAPI()
    app.include_router(router)

    settings_mock = MagicMock()
    settings_mock.security.api_token = None
    app.state.settings = settings_mock

    registry = MagicMock()
    registry.is_available.return_value = False
    app.state.module_registry = registry
    return app


def _make_app_module_not_found() -> FastAPI:
    """Build an app where specific modules are not found (404 path)."""
    app = FastAPI()
    app.include_router(router)

    settings_mock = MagicMock()
    settings_mock.security.api_token = None
    app.state.settings = settings_mock

    mm = AsyncMock()
    registry = MagicMock()

    def _is_available(module_id: str) -> bool:
        return module_id == "module_manager"

    registry.is_available.side_effect = _is_available
    registry.get.return_value = mm
    app.state.module_registry = registry
    return app


# ---------------------------------------------------------------------------
# Tests -- GET /admin/modules (list)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListModules:
    """GET /admin/modules"""

    def test_list_modules_returns_200(self, client, mock_module_manager):
        resp = client.get("/admin/modules")
        assert resp.status_code == 200
        body = resp.json()
        assert body["modules"] == [{"module_id": "fs"}]
        assert body["count"] == 1

    def test_list_modules_delegates_default_params(self, client, mock_module_manager):
        """Default query params are forwarded as None/False."""
        client.get("/admin/modules")
        mock_module_manager._action_list_modules.assert_awaited_once_with({
            "module_type": None,
            "state": None,
            "include_health": False,
        })

    def test_list_modules_with_query_params(self, client, mock_module_manager):
        """Query params are forwarded to the action method."""
        client.get("/admin/modules?module_type=core&state=running&include_health=true")
        mock_module_manager._action_list_modules.assert_awaited_once_with({
            "module_type": "core",
            "state": "running",
            "include_health": True,
        })

    def test_list_modules_503_when_mm_unavailable(self):
        """Returns 503 when module_manager is not available."""
        app = _make_app_without_mm()
        c = TestClient(app, raise_server_exceptions=False)
        resp = c.get("/admin/modules")
        assert resp.status_code == 503
        assert "not available" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Tests -- GET /admin/modules/{id} (info)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetModuleInfo:
    """GET /admin/modules/{module_id}"""

    def test_get_module_info_returns_200(self, client, mock_module_manager):
        resp = client.get("/admin/modules/fs")
        assert resp.status_code == 200
        body = resp.json()
        assert body["module_id"] == "fs"
        assert body["status"] == "ready"

    def test_get_module_info_delegates_params(self, client, mock_module_manager):
        client.get("/admin/modules/fs?include_health=true&include_metrics=true")
        mock_module_manager._action_get_module_info.assert_awaited_once_with({
            "module_id": "fs",
            "include_health": True,
            "include_metrics": True,
        })

    def test_get_module_info_default_flags(self, client, mock_module_manager):
        client.get("/admin/modules/fs")
        mock_module_manager._action_get_module_info.assert_awaited_once_with({
            "module_id": "fs",
            "include_health": False,
            "include_metrics": False,
        })

    def test_get_module_info_503_when_mm_unavailable(self):
        app = _make_app_without_mm()
        c = TestClient(app, raise_server_exceptions=False)
        resp = c.get("/admin/modules/fs")
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Tests -- GET /admin/modules/{id}/health
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetModuleHealth:
    """GET /admin/modules/{module_id}/health"""

    def test_health_returns_200(self, client, mock_module_manager):
        resp = client.get("/admin/modules/fs/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["healthy"] is True

    def test_health_delegates_module_id(self, client, mock_module_manager):
        client.get("/admin/modules/os_exec/health")
        mock_module_manager._action_get_module_health.assert_awaited_once_with(
            {"module_id": "os_exec"},
        )

    def test_health_503_when_mm_unavailable(self):
        app = _make_app_without_mm()
        c = TestClient(app, raise_server_exceptions=False)
        resp = c.get("/admin/modules/fs/health")
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Tests -- GET /admin/modules/{id}/metrics
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetModuleMetrics:
    """GET /admin/modules/{module_id}/metrics"""

    def test_metrics_returns_200(self, client, mock_module_manager):
        resp = client.get("/admin/modules/fs/metrics")
        assert resp.status_code == 200
        body = resp.json()
        assert body["actions_executed"] == 42

    def test_metrics_delegates_module_id(self, client, mock_module_manager):
        client.get("/admin/modules/gui/metrics")
        mock_module_manager._action_get_module_metrics.assert_awaited_once_with(
            {"module_id": "gui"},
        )

    def test_metrics_503_when_mm_unavailable(self):
        app = _make_app_without_mm()
        c = TestClient(app, raise_server_exceptions=False)
        resp = c.get("/admin/modules/fs/metrics")
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Tests -- GET /admin/modules/{id}/state
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetModuleState:
    """GET /admin/modules/{module_id}/state"""

    def test_state_returns_200(self, client, mock_module_manager):
        resp = client.get("/admin/modules/fs/state")
        assert resp.status_code == 200
        body = resp.json()
        assert body["state"] == "running"

    def test_state_delegates_module_id(self, client, mock_module_manager):
        client.get("/admin/modules/browser/state")
        mock_module_manager._action_get_module_state.assert_awaited_once_with(
            {"module_id": "browser"},
        )

    def test_state_503_when_mm_unavailable(self):
        app = _make_app_without_mm()
        c = TestClient(app, raise_server_exceptions=False)
        resp = c.get("/admin/modules/fs/state")
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Tests -- GET /admin/modules/{id}/describe
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDescribeModule:
    """GET /admin/modules/{module_id}/describe"""

    def test_describe_returns_200(self, client, mock_module_manager):
        resp = client.get("/admin/modules/fs/describe")
        assert resp.status_code == 200
        body = resp.json()
        assert body["description"] == "Filesystem module"

    def test_describe_delegates_module_id(self, client, mock_module_manager):
        client.get("/admin/modules/iot/describe")
        mock_module_manager._action_describe_module.assert_awaited_once_with(
            {"module_id": "iot"},
        )

    def test_describe_503_when_mm_unavailable(self):
        app = _make_app_without_mm()
        c = TestClient(app, raise_server_exceptions=False)
        resp = c.get("/admin/modules/fs/describe")
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Tests -- GET /admin/modules/{id}/manifest
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetModuleManifest:
    """GET /admin/modules/{module_id}/manifest"""

    def test_manifest_returns_200(self, client):
        resp = client.get("/admin/modules/fs/manifest")
        assert resp.status_code == 200
        body = resp.json()
        assert body["module_id"] == "fs"
        assert body["version"] == "1.0.0"
        assert "actions" in body

    def test_manifest_calls_registry_get_directly(self, client, mock_registry):
        """Manifest does NOT go through module_manager -- it calls registry.get() directly."""
        client.get("/admin/modules/fs/manifest")
        # registry.get was called with "fs" (not "module_manager" for this path)
        mock_registry.get.assert_any_call("fs")

    def test_manifest_404_when_module_not_found(self):
        """Returns 404 when the target module is not available in the registry."""
        app = _make_app_module_not_found()
        c = TestClient(app, raise_server_exceptions=False)
        resp = c.get("/admin/modules/nonexistent/manifest")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    def test_manifest_returns_expected_body(self, client):
        """Verifies that the manifest body matches what to_dict() returns."""
        resp = client.get("/admin/modules/fs/manifest")
        body = resp.json()
        assert body["module_id"] == "fs"
        assert body["version"] == "1.0.0"
        assert body["description"] == "Filesystem operations"


# ---------------------------------------------------------------------------
# Tests -- GET /admin/modules/{id}/docs
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetModuleDocs:
    """GET /admin/modules/{module_id}/docs"""

    def test_docs_404_when_module_not_found(self):
        """Returns 404 when the target module is not available in the registry."""
        app = _make_app_module_not_found()
        c = TestClient(app, raise_server_exceptions=False)
        resp = c.get("/admin/modules/nonexistent/docs")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    def test_docs_returns_200_with_none_fields(self, client, tmp_path):
        """Returns 200 with None doc fields when no doc files exist."""
        # Create a module directory with no docs
        module_dir = tmp_path / "empty_module"
        module_dir.mkdir()
        (module_dir / "module.py").write_text("# module")

        with patch("inspect.getfile", return_value=str(module_dir / "module.py")):
            resp = client.get("/admin/modules/fs/docs")

        assert resp.status_code == 200
        body = resp.json()
        assert body["module_id"] == "fs"
        assert body["readme"] is None
        assert body["actions"] is None
        assert body["integration"] is None
        assert body["changelog"] is None

    def test_docs_returns_200_with_existing_files(self, client, tmp_path):
        """Returns 200 with doc contents when files exist."""
        # Create a fake module directory with docs
        module_dir = tmp_path / "my_module"
        module_dir.mkdir()
        (module_dir / "module.py").write_text("# module")
        (module_dir / "README.md").write_text("# My Module README")
        docs_dir = module_dir / "docs"
        docs_dir.mkdir()
        (docs_dir / "actions.md").write_text("# Actions doc")
        (module_dir / "CHANGELOG.md").write_text("# Changelog")

        with patch("inspect.getfile", return_value=str(module_dir / "module.py")):
            resp = client.get("/admin/modules/fs/docs")

        assert resp.status_code == 200
        body = resp.json()
        assert body["module_id"] == "fs"
        assert body["readme"] == "# My Module README"
        assert body["actions"] == "# Actions doc"
        assert body["integration"] is None  # not created
        assert body["changelog"] == "# Changelog"


# ---------------------------------------------------------------------------
# Tests -- POST /admin/modules/{id}/enable
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEnableModule:
    """POST /admin/modules/{module_id}/enable"""

    def test_enable_returns_200(self, client, mock_module_manager):
        resp = client.post("/admin/modules/fs/enable")
        assert resp.status_code == 200
        body = resp.json()
        assert body["enabled"] is True

    def test_enable_delegates_module_id(self, client, mock_module_manager):
        client.post("/admin/modules/os_exec/enable")
        mock_module_manager._action_enable_module.assert_awaited_once_with(
            {"module_id": "os_exec"},
        )

    def test_enable_503_when_mm_unavailable(self):
        app = _make_app_without_mm()
        c = TestClient(app, raise_server_exceptions=False)
        resp = c.post("/admin/modules/fs/enable")
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Tests -- POST /admin/modules/{id}/disable
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDisableModule:
    """POST /admin/modules/{module_id}/disable"""

    def test_disable_returns_200(self, client, mock_module_manager):
        resp = client.post("/admin/modules/fs/disable")
        assert resp.status_code == 200
        body = resp.json()
        assert body["enabled"] is False

    def test_disable_delegates_module_id(self, client, mock_module_manager):
        client.post("/admin/modules/gui/disable")
        mock_module_manager._action_disable_module.assert_awaited_once_with(
            {"module_id": "gui"},
        )

    def test_disable_503_when_mm_unavailable(self):
        app = _make_app_without_mm()
        c = TestClient(app, raise_server_exceptions=False)
        resp = c.post("/admin/modules/fs/disable")
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Tests -- POST /admin/modules/{id}/pause
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPauseModule:
    """POST /admin/modules/{module_id}/pause"""

    def test_pause_returns_200(self, client, mock_module_manager):
        resp = client.post("/admin/modules/fs/pause")
        assert resp.status_code == 200
        body = resp.json()
        assert body["paused"] is True

    def test_pause_delegates_module_id(self, client, mock_module_manager):
        client.post("/admin/modules/browser/pause")
        mock_module_manager._action_pause_module.assert_awaited_once_with(
            {"module_id": "browser"},
        )

    def test_pause_503_when_mm_unavailable(self):
        app = _make_app_without_mm()
        c = TestClient(app, raise_server_exceptions=False)
        resp = c.post("/admin/modules/fs/pause")
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Tests -- POST /admin/modules/{id}/resume
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestResumeModule:
    """POST /admin/modules/{module_id}/resume"""

    def test_resume_returns_200(self, client, mock_module_manager):
        resp = client.post("/admin/modules/fs/resume")
        assert resp.status_code == 200
        body = resp.json()
        assert body["paused"] is False

    def test_resume_delegates_module_id(self, client, mock_module_manager):
        client.post("/admin/modules/iot/resume")
        mock_module_manager._action_resume_module.assert_awaited_once_with(
            {"module_id": "iot"},
        )

    def test_resume_503_when_mm_unavailable(self):
        app = _make_app_without_mm()
        c = TestClient(app, raise_server_exceptions=False)
        resp = c.post("/admin/modules/fs/resume")
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Tests -- POST /admin/modules/{id}/restart
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRestartModule:
    """POST /admin/modules/{module_id}/restart"""

    def test_restart_returns_200(self, client, mock_module_manager):
        resp = client.post("/admin/modules/fs/restart")
        assert resp.status_code == 200
        body = resp.json()
        assert body["restarted"] is True

    def test_restart_delegates_module_id(self, client, mock_module_manager):
        client.post("/admin/modules/vision/restart")
        mock_module_manager._action_restart_module.assert_awaited_once_with(
            {"module_id": "vision"},
        )

    def test_restart_503_when_mm_unavailable(self):
        app = _make_app_without_mm()
        c = TestClient(app, raise_server_exceptions=False)
        resp = c.post("/admin/modules/fs/restart")
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Tests -- PUT /admin/modules/{id}/config
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUpdateModuleConfig:
    """PUT /admin/modules/{module_id}/config"""

    def test_config_update_returns_200(self, client, mock_module_manager):
        resp = client.put(
            "/admin/modules/fs/config",
            json={"config": {"sandbox_paths": ["/tmp"]}},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["config_applied"] is True

    def test_config_update_delegates_params(self, client, mock_module_manager):
        new_config = {"max_retries": 3, "timeout": 30}
        client.put("/admin/modules/fs/config", json={"config": new_config})
        mock_module_manager._action_update_module_config.assert_awaited_once_with({
            "module_id": "fs",
            "config": new_config,
        })

    def test_config_update_503_when_mm_unavailable(self):
        app = _make_app_without_mm()
        c = TestClient(app, raise_server_exceptions=False)
        resp = c.put("/admin/modules/fs/config", json={"config": {"k": "v"}})
        assert resp.status_code == 503

    def test_config_update_422_when_body_missing_config(self, client):
        """Missing required 'config' field in body returns 422."""
        resp = client.put("/admin/modules/fs/config", json={})
        assert resp.status_code == 422

    def test_config_update_with_empty_config(self, client, mock_module_manager):
        """An empty config dict is valid and forwarded."""
        client.put("/admin/modules/fs/config", json={"config": {}})
        mock_module_manager._action_update_module_config.assert_awaited_once_with({
            "module_id": "fs",
            "config": {},
        })


# ---------------------------------------------------------------------------
# Tests -- POST /admin/modules/{id}/actions/{action}/enable
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEnableAction:
    """POST /admin/modules/{module_id}/actions/{action_name}/enable"""

    def test_enable_action_returns_200(self, client, mock_module_manager):
        resp = client.post("/admin/modules/fs/actions/read_file/enable")
        assert resp.status_code == 200
        body = resp.json()
        assert body["enabled"] is True

    def test_enable_action_delegates_params(self, client, mock_module_manager):
        client.post("/admin/modules/os_exec/actions/run_command/enable")
        mock_module_manager._action_enable_action.assert_awaited_once_with({
            "module_id": "os_exec",
            "action": "run_command",
        })

    def test_enable_action_503_when_mm_unavailable(self):
        app = _make_app_without_mm()
        c = TestClient(app, raise_server_exceptions=False)
        resp = c.post("/admin/modules/fs/actions/read_file/enable")
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Tests -- POST /admin/modules/{id}/actions/{action}/disable
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDisableAction:
    """POST /admin/modules/{module_id}/actions/{action_name}/disable"""

    def test_disable_action_returns_200(self, client, mock_module_manager):
        resp = client.post(
            "/admin/modules/fs/actions/write_file/disable",
            json={"reason": "Security audit"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["disabled"] is True

    def test_disable_action_delegates_params_with_reason(self, client, mock_module_manager):
        client.post(
            "/admin/modules/os_exec/actions/run_command/disable",
            json={"reason": "Too dangerous"},
        )
        mock_module_manager._action_disable_action.assert_awaited_once_with({
            "module_id": "os_exec",
            "action": "run_command",
            "reason": "Too dangerous",
        })

    def test_disable_action_default_reason(self, client, mock_module_manager):
        """When reason is omitted from body, it defaults to empty string."""
        client.post(
            "/admin/modules/fs/actions/write_file/disable",
            json={},
        )
        mock_module_manager._action_disable_action.assert_awaited_once_with({
            "module_id": "fs",
            "action": "write_file",
            "reason": "",
        })

    def test_disable_action_503_when_mm_unavailable(self):
        app = _make_app_without_mm()
        c = TestClient(app, raise_server_exceptions=False)
        resp = c.post(
            "/admin/modules/fs/actions/write_file/disable",
            json={"reason": "test"},
        )
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Tests -- _get_module_manager helper
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetModuleManagerHelper:
    """Verify _get_module_manager raises 503 or returns the module."""

    def test_returns_mm_when_available(self, client, mock_module_manager):
        """When module_manager is available, all endpoints succeed (implicitly tested above)."""
        resp = client.get("/admin/modules")
        assert resp.status_code == 200

    def test_503_detail_message(self):
        """The 503 detail says 'Module Manager not available.'."""
        app = _make_app_without_mm()
        c = TestClient(app, raise_server_exceptions=False)
        resp = c.get("/admin/modules")
        assert resp.status_code == 503
        assert resp.json()["detail"] == "Module Manager not available."


# ---------------------------------------------------------------------------
# Tests -- Auth dependency (api_token enforcement)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAuthEnforcement:
    """Verify that when api_token is set, requests without it are rejected."""

    def _make_authed_app(self, mock_registry) -> FastAPI:
        app = FastAPI()
        app.include_router(router)
        settings_mock = MagicMock()
        settings_mock.security.api_token = "secret-token-42"
        app.state.settings = settings_mock
        app.state.module_registry = mock_registry
        return app

    def test_401_without_token(self, mock_registry):
        app = self._make_authed_app(mock_registry)
        c = TestClient(app, raise_server_exceptions=False)
        resp = c.get("/admin/modules")
        assert resp.status_code == 401

    def test_200_with_valid_token(self, mock_registry):
        app = self._make_authed_app(mock_registry)
        c = TestClient(app, raise_server_exceptions=False)
        resp = c.get(
            "/admin/modules",
            headers={"X-LLMOS-Token": "secret-token-42"},
        )
        assert resp.status_code == 200

    def test_401_with_wrong_token(self, mock_registry):
        app = self._make_authed_app(mock_registry)
        c = TestClient(app, raise_server_exceptions=False)
        resp = c.get(
            "/admin/modules",
            headers={"X-LLMOS-Token": "wrong-token"},
        )
        assert resp.status_code == 401
