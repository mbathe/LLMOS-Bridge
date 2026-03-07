"""Tests for the /apps REST API endpoints."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from llmos_bridge.api.routes.apps import router, AppResponse
from llmos_bridge.apps.app_store import AppStore, AppRecord, AppStatus
from llmos_bridge.apps.agent_runtime import AgentRunResult, LLMProvider
from llmos_bridge.apps.runtime import AppRuntime


# ─── Helpers ──────────────────────────────────────────────────────────


MINIMAL_YAML = """\
app:
  name: test-app
  version: "1.0"
  description: "A test app"
  author: "tester"
  tags: [test]
agent:
  brain:
    provider: test
    model: test-model
  system_prompt: "You are a test assistant."
"""


class MockLLM(LLMProvider):
    async def chat(self, *, system, messages, tools, max_tokens=4096, **kwargs):
        return {"text": "Done.", "tool_calls": [], "done": True}

    async def close(self):
        pass


def create_test_app(store: AppStore, runtime: AppRuntime) -> FastAPI:
    """Create a FastAPI app with mocked dependencies."""
    app = FastAPI()
    app.include_router(router)

    # Wire up state
    app.state.app_store = store
    app.state.app_runtime = runtime
    app.state.settings = MagicMock()
    app.state.settings.security = MagicMock()
    app.state.settings.security.api_token = None  # no auth

    return app


@pytest.fixture
async def store(tmp_path):
    s = AppStore(tmp_path / "test_apps.db")
    await s.init()
    yield s
    await s.close()


@pytest.fixture
def runtime():
    return AppRuntime(llm_provider_factory=lambda b: MockLLM())


@pytest.fixture
def client(store, runtime):
    app = create_test_app(store, runtime)
    return TestClient(app)


# ─── Register ────────────────────────────────────────────────────────


class TestRegister:
    def test_register_from_yaml(self, client):
        resp = client.post("/apps/register", json={"yaml_text": MINIMAL_YAML})
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "test-app"
        assert data["version"] == "1.0"
        assert data["status"] == "registered"

    def test_register_no_input(self, client):
        resp = client.post("/apps/register", json={})
        assert resp.status_code == 400

    def test_register_invalid_yaml(self, client):
        resp = client.post("/apps/register", json={"yaml_text": "{{invalid"})
        assert resp.status_code == 422

    def test_register_from_file(self, client, tmp_path):
        f = tmp_path / "test.app.yaml"
        f.write_text(MINIMAL_YAML)
        resp = client.post("/apps/register", json={"file_path": str(f)})
        assert resp.status_code == 201
        assert resp.json()["file_path"] == str(f)


# ─── List ────────────────────────────────────────────────────────────


class TestList:
    def test_list_empty(self, client):
        resp = client.get("/apps")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_after_register(self, client):
        client.post("/apps/register", json={"yaml_text": MINIMAL_YAML})
        resp = client.get("/apps")
        assert resp.status_code == 200
        apps = resp.json()
        assert len(apps) == 1
        assert apps[0]["name"] == "test-app"

    def test_list_invalid_status_filter(self, client):
        resp = client.get("/apps", params={"status_filter": "bogus"})
        assert resp.status_code == 400


# ─── Get ─────────────────────────────────────────────────────────────


class TestGet:
    def test_get_app(self, client):
        resp = client.post("/apps/register", json={"yaml_text": MINIMAL_YAML})
        app_id = resp.json()["id"]
        resp = client.get(f"/apps/{app_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == app_id

    def test_get_nonexistent(self, client):
        resp = client.get("/apps/nonexistent")
        assert resp.status_code == 404


# ─── Delete ──────────────────────────────────────────────────────────


class TestDelete:
    def test_delete_app(self, client):
        resp = client.post("/apps/register", json={"yaml_text": MINIMAL_YAML})
        app_id = resp.json()["id"]
        resp = client.delete(f"/apps/{app_id}")
        assert resp.status_code == 204
        # Verify gone
        resp = client.get(f"/apps/{app_id}")
        assert resp.status_code == 404

    def test_delete_nonexistent(self, client):
        resp = client.delete("/apps/nonexistent")
        assert resp.status_code == 404


# ─── Run ─────────────────────────────────────────────────────────────


class TestRun:
    def test_run_app(self, client, tmp_path):
        f = tmp_path / "run.app.yaml"
        f.write_text(MINIMAL_YAML)
        resp = client.post("/apps/register", json={"file_path": str(f)})
        app_id = resp.json()["id"]

        resp = client.post(f"/apps/{app_id}/run", json={"input": "Hello"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["output"] == "Done."

    def test_run_nonexistent(self, client):
        resp = client.post("/apps/nonexistent/run", json={"input": "Hello"})
        assert resp.status_code == 404


# ─── Validate ────────────────────────────────────────────────────────


class TestValidate:
    def test_validate_valid(self, client, tmp_path):
        f = tmp_path / "valid.app.yaml"
        f.write_text(MINIMAL_YAML)
        resp = client.post("/apps/register", json={"file_path": str(f)})
        app_id = resp.json()["id"]

        resp = client.post(f"/apps/{app_id}/validate")
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is True
        assert data["errors"] == []

    def test_validate_nonexistent(self, client):
        resp = client.post("/apps/nonexistent/validate")
        assert resp.status_code == 404


# ─── Update Status ───────────────────────────────────────────────────


class TestUpdateStatus:
    def test_update_status(self, client):
        resp = client.post("/apps/register", json={"yaml_text": MINIMAL_YAML})
        app_id = resp.json()["id"]

        resp = client.put(f"/apps/{app_id}/status", json={"status": "running"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "running"

    def test_update_invalid_status(self, client):
        resp = client.post("/apps/register", json={"yaml_text": MINIMAL_YAML})
        app_id = resp.json()["id"]

        resp = client.put(f"/apps/{app_id}/status", json={"status": "invalid"})
        assert resp.status_code == 400

    def test_update_nonexistent(self, client):
        resp = client.put("/apps/nonexistent/status", json={"status": "running"})
        assert resp.status_code == 404
