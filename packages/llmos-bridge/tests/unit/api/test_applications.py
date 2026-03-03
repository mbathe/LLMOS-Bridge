"""Unit tests — Applications REST API endpoints.

Tests the 11 endpoints in llmos_bridge.api.routes.applications.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from llmos_bridge.api.routes.applications import router
from llmos_bridge.identity.store import IdentityStore


@pytest.fixture
async def identity_store(tmp_path: Path):
    s = IdentityStore(tmp_path / "identity.db")
    await s.init()
    await s.ensure_default_app()
    yield s
    await s.close()


@pytest.fixture
def app(identity_store: IdentityStore) -> FastAPI:
    """Build a minimal FastAPI app with the applications router."""
    app = FastAPI()
    app.include_router(router)

    # Wire mock state
    app.state.identity_store = identity_store
    app.state.settings = MagicMock()
    app.state.settings.security.api_token = None

    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


@pytest.mark.unit
class TestApplicationEndpoints:
    """Tests for Application CRUD endpoints."""

    def test_list_applications(self, client: TestClient) -> None:
        resp = client.get("/applications")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        # Default app should exist
        assert len(data) >= 1
        assert any(a["app_id"] == "default" for a in data)

    def test_create_application(self, client: TestClient) -> None:
        resp = client.post(
            "/applications",
            json={"name": "TestApp", "description": "A test application"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "TestApp"
        assert data["description"] == "A test application"
        assert data["enabled"] is True
        assert "app_id" in data

    def test_create_application_duplicate_name(self, client: TestClient) -> None:
        client.post("/applications", json={"name": "Dup"})
        resp = client.post("/applications", json={"name": "Dup"})
        assert resp.status_code == 409

    def test_get_application(self, client: TestClient) -> None:
        resp = client.get("/applications/default")
        assert resp.status_code == 200
        data = resp.json()
        assert data["app_id"] == "default"

    def test_get_application_not_found(self, client: TestClient) -> None:
        resp = client.get("/applications/nonexistent")
        assert resp.status_code == 404

    def test_update_application(self, client: TestClient) -> None:
        # Create first
        create_resp = client.post("/applications", json={"name": "Updatable"})
        app_id = create_resp.json()["app_id"]

        resp = client.put(
            f"/applications/{app_id}",
            json={"description": "Updated!", "max_concurrent_plans": 20},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["description"] == "Updated!"
        assert data["max_concurrent_plans"] == 20

    def test_update_application_not_found(self, client: TestClient) -> None:
        resp = client.put(
            "/applications/nonexistent",
            json={"description": "nope"},
        )
        assert resp.status_code == 404

    def test_delete_application(self, client: TestClient) -> None:
        create_resp = client.post("/applications", json={"name": "ToDelete"})
        app_id = create_resp.json()["app_id"]

        resp = client.delete(f"/applications/{app_id}")
        assert resp.status_code == 200
        assert "disabled" in resp.json()["detail"].lower() or "deleted" in resp.json()["detail"].lower()

    def test_delete_default_application_rejected(self, client: TestClient) -> None:
        resp = client.delete("/applications/default")
        assert resp.status_code == 400


@pytest.mark.unit
class TestAgentEndpoints:
    """Tests for Agent CRUD endpoints."""

    def test_create_and_list_agents(self, client: TestClient) -> None:
        resp = client.post(
            "/applications/default/agents",
            json={"name": "TestBot"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "TestBot"
        assert data["role"] == "agent"

        list_resp = client.get("/applications/default/agents")
        assert list_resp.status_code == 200
        agents = list_resp.json()
        assert len(agents) >= 1

    def test_create_agent_custom_role(self, client: TestClient) -> None:
        resp = client.post(
            "/applications/default/agents",
            json={"name": "AdminBot", "role": "operator"},
        )
        assert resp.status_code == 201
        assert resp.json()["role"] == "operator"

    def test_create_agent_invalid_role(self, client: TestClient) -> None:
        resp = client.post(
            "/applications/default/agents",
            json={"name": "BadBot", "role": "superuser"},
        )
        assert resp.status_code == 400

    def test_create_agent_nonexistent_app(self, client: TestClient) -> None:
        resp = client.post(
            "/applications/nonexistent/agents",
            json={"name": "Bot"},
        )
        assert resp.status_code == 404

    def test_delete_agent(self, client: TestClient) -> None:
        create_resp = client.post(
            "/applications/default/agents",
            json={"name": "ToDelete"},
        )
        agent_id = create_resp.json()["agent_id"]

        resp = client.delete(f"/applications/default/agents/{agent_id}")
        assert resp.status_code == 200


@pytest.mark.unit
class TestApiKeyEndpoints:
    """Tests for API key generation endpoints."""

    def test_create_and_revoke_api_key(self, client: TestClient) -> None:
        # Create agent first
        agent_resp = client.post(
            "/applications/default/agents",
            json={"name": "KeyBot"},
        )
        agent_id = agent_resp.json()["agent_id"]

        # Create key
        key_resp = client.post(
            f"/applications/default/agents/{agent_id}/keys"
        )
        assert key_resp.status_code == 201
        key_data = key_resp.json()
        assert "api_key" in key_data
        assert key_data["api_key"].startswith("llmos_")
        assert "key_id" in key_data

        # Revoke key
        key_id = key_data["key_id"]
        revoke_resp = client.delete(
            f"/applications/default/agents/{agent_id}/keys/{key_id}"
        )
        assert revoke_resp.status_code == 200


@pytest.mark.unit
class TestSessionEndpoints:
    """Tests for session listing endpoints."""

    def test_list_sessions_empty(self, client: TestClient) -> None:
        resp = client.get("/applications/default/sessions")
        assert resp.status_code == 200
        assert resp.json() == []
