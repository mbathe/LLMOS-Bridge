"""Unit tests — Admin hub REST API endpoints.

Tests all six endpoints in llmos_bridge.api.routes.admin_hub:
  - GET    /admin/hub/search?q=...&limit=20
  - GET    /admin/hub/installed?enabled_only=false
  - POST   /admin/hub/install
  - DELETE /admin/hub/modules/{module_id}
  - POST   /admin/hub/modules/{module_id}/upgrade
  - GET    /admin/hub/modules/{module_id}/verify
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from llmos_bridge.api.routes.admin_hub import router


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_module_manager():
    """Create a mock ModuleManagerModule with all hub action methods."""
    mm = AsyncMock()
    mm._action_search_hub = AsyncMock(return_value={"results": [], "count": 0})
    mm._action_list_installed = AsyncMock(return_value={"modules": [], "count": 0})
    mm._action_install_module = AsyncMock(
        return_value={"module_id": "test", "installed": True}
    )
    mm._action_uninstall_module = AsyncMock(
        return_value={"module_id": "test", "uninstalled": True}
    )
    mm._action_upgrade_module = AsyncMock(
        return_value={"module_id": "test", "upgraded": True}
    )
    mm._action_verify_module = AsyncMock(
        return_value={"module_id": "test", "valid": True}
    )
    return mm


@pytest.fixture
def mock_registry(mock_module_manager):
    """Create a mock ModuleRegistry that returns the mock module manager."""
    registry = MagicMock()
    registry.is_available.return_value = True
    registry.get.return_value = mock_module_manager
    return registry


@pytest.fixture
def unavailable_registry():
    """Create a mock ModuleRegistry where module_manager is NOT available."""
    registry = MagicMock()
    registry.is_available.return_value = False
    return registry


@pytest.fixture
def admin_app(mock_registry) -> FastAPI:
    """Build a minimal FastAPI app with the admin hub router."""
    app = FastAPI()
    app.include_router(router)
    settings_mock = MagicMock()
    settings_mock.security.api_token = None
    app.state.settings = settings_mock
    app.state.module_registry = mock_registry
    return app


@pytest.fixture
def unavailable_app(unavailable_registry) -> FastAPI:
    """Build a FastAPI app where module_manager is unavailable (503 path)."""
    app = FastAPI()
    app.include_router(router)
    settings_mock = MagicMock()
    settings_mock.security.api_token = None
    app.state.settings = settings_mock
    app.state.module_registry = unavailable_registry
    return app


@pytest.fixture
def client(admin_app) -> TestClient:
    return TestClient(admin_app)


@pytest.fixture
def unavailable_client(unavailable_app) -> TestClient:
    return TestClient(unavailable_app)


# ---------------------------------------------------------------------------
# Tests — _get_module_manager 503 fallback
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestModuleManagerUnavailable:
    """All endpoints return 503 when module_manager is not available."""

    def test_search_hub_503(self, unavailable_client):
        resp = unavailable_client.get("/admin/hub/search")
        assert resp.status_code == 503
        assert "not available" in resp.json()["detail"].lower()

    def test_list_installed_503(self, unavailable_client):
        resp = unavailable_client.get("/admin/hub/installed")
        assert resp.status_code == 503
        assert "not available" in resp.json()["detail"].lower()

    def test_install_module_503(self, unavailable_client):
        resp = unavailable_client.post(
            "/admin/hub/install",
            json={"source": "hub", "module_id": "some_mod", "path": "", "version": "latest"},
        )
        assert resp.status_code == 503

    def test_uninstall_module_503(self, unavailable_client):
        resp = unavailable_client.delete("/admin/hub/modules/some_mod")
        assert resp.status_code == 503

    def test_upgrade_module_503(self, unavailable_client):
        resp = unavailable_client.post(
            "/admin/hub/modules/some_mod/upgrade",
            json={"path": "/tmp/pkg"},
        )
        assert resp.status_code == 503

    def test_verify_module_503(self, unavailable_client):
        resp = unavailable_client.get("/admin/hub/modules/some_mod/verify")
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Tests — GET /admin/hub/search
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSearchHub:
    """GET /admin/hub/search"""

    def test_search_returns_200(self, client, mock_module_manager):
        resp = client.get("/admin/hub/search")
        assert resp.status_code == 200
        assert resp.json() == {"results": [], "count": 0}

    def test_search_forwards_default_params(self, client, mock_module_manager):
        """Default query params are q='' and limit=20."""
        client.get("/admin/hub/search")
        mock_module_manager._action_search_hub.assert_awaited_once_with(
            {"query": "", "limit": 20}
        )

    def test_search_forwards_custom_query(self, client, mock_module_manager):
        client.get("/admin/hub/search", params={"q": "vision", "limit": 5})
        mock_module_manager._action_search_hub.assert_awaited_once_with(
            {"query": "vision", "limit": 5}
        )

    def test_search_with_results(self, client, mock_module_manager):
        mock_module_manager._action_search_hub.return_value = {
            "results": [{"module_id": "ocr-plugin", "version": "1.0.0"}],
            "count": 1,
        }
        resp = client.get("/admin/hub/search", params={"q": "ocr"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["results"][0]["module_id"] == "ocr-plugin"


# ---------------------------------------------------------------------------
# Tests — GET /admin/hub/installed
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListInstalled:
    """GET /admin/hub/installed"""

    def test_list_installed_returns_200(self, client, mock_module_manager):
        resp = client.get("/admin/hub/installed")
        assert resp.status_code == 200
        assert resp.json() == {"modules": [], "count": 0}

    def test_list_installed_default_enabled_only(self, client, mock_module_manager):
        """Default enabled_only is False."""
        client.get("/admin/hub/installed")
        mock_module_manager._action_list_installed.assert_awaited_once_with(
            {"enabled_only": False}
        )

    def test_list_installed_enabled_only_true(self, client, mock_module_manager):
        client.get("/admin/hub/installed", params={"enabled_only": "true"})
        mock_module_manager._action_list_installed.assert_awaited_once_with(
            {"enabled_only": True}
        )

    def test_list_installed_with_modules(self, client, mock_module_manager):
        mock_module_manager._action_list_installed.return_value = {
            "modules": [
                {"module_id": "community-ocr", "enabled": True, "version": "2.1.0"}
            ],
            "count": 1,
        }
        resp = client.get("/admin/hub/installed")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["modules"][0]["module_id"] == "community-ocr"


# ---------------------------------------------------------------------------
# Tests — POST /admin/hub/install
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestInstallModule:
    """POST /admin/hub/install"""

    def test_install_returns_200(self, client, mock_module_manager):
        resp = client.post(
            "/admin/hub/install",
            json={
                "source": "hub",
                "module_id": "my-plugin",
                "path": "",
                "version": "1.0.0",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["installed"] is True

    def test_install_forwards_body_params(self, client, mock_module_manager):
        client.post(
            "/admin/hub/install",
            json={
                "source": "local",
                "module_id": "custom-mod",
                "path": "/opt/modules/custom-mod",
                "version": "latest",
            },
        )
        mock_module_manager._action_install_module.assert_awaited_once_with(
            {
                "source": "local",
                "module_id": "custom-mod",
                "path": "/opt/modules/custom-mod",
                "version": "latest",
            }
        )

    def test_install_with_defaults(self, client, mock_module_manager):
        """source defaults to 'hub', module_id to '', path to '', version to 'latest'."""
        client.post("/admin/hub/install", json={})
        mock_module_manager._action_install_module.assert_awaited_once_with(
            {"source": "hub", "module_id": "", "path": "", "version": "latest"}
        )


# ---------------------------------------------------------------------------
# Tests — DELETE /admin/hub/modules/{module_id}
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUninstallModule:
    """DELETE /admin/hub/modules/{module_id}"""

    def test_uninstall_returns_200(self, client, mock_module_manager):
        resp = client.delete("/admin/hub/modules/my-plugin")
        assert resp.status_code == 200
        body = resp.json()
        assert body["uninstalled"] is True

    def test_uninstall_forwards_module_id(self, client, mock_module_manager):
        client.delete("/admin/hub/modules/community-ocr")
        mock_module_manager._action_uninstall_module.assert_awaited_once_with(
            {"module_id": "community-ocr"}
        )


# ---------------------------------------------------------------------------
# Tests — POST /admin/hub/modules/{module_id}/upgrade
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUpgradeModule:
    """POST /admin/hub/modules/{module_id}/upgrade"""

    def test_upgrade_returns_200(self, client, mock_module_manager):
        resp = client.post(
            "/admin/hub/modules/my-plugin/upgrade",
            json={"path": "/tmp/new-version"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["upgraded"] is True

    def test_upgrade_forwards_params(self, client, mock_module_manager):
        client.post(
            "/admin/hub/modules/community-ocr/upgrade",
            json={"path": "/opt/modules/v2"},
        )
        mock_module_manager._action_upgrade_module.assert_awaited_once_with(
            {"module_id": "community-ocr", "path": "/opt/modules/v2"}
        )


# ---------------------------------------------------------------------------
# Tests — GET /admin/hub/modules/{module_id}/verify
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestVerifyModule:
    """GET /admin/hub/modules/{module_id}/verify"""

    def test_verify_returns_200(self, client, mock_module_manager):
        resp = client.get("/admin/hub/modules/my-plugin/verify")
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is True

    def test_verify_forwards_module_id(self, client, mock_module_manager):
        client.get("/admin/hub/modules/community-ocr/verify")
        mock_module_manager._action_verify_module.assert_awaited_once_with(
            {"module_id": "community-ocr"}
        )

    def test_verify_invalid_module(self, client, mock_module_manager):
        """Verify returns the backend result even when valid=False."""
        mock_module_manager._action_verify_module.return_value = {
            "module_id": "broken-mod",
            "valid": False,
            "errors": ["checksum mismatch"],
        }
        resp = client.get("/admin/hub/modules/broken-mod/verify")
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is False
        assert "checksum mismatch" in body["errors"]
