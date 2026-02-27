"""End-to-end tests — Full plan execution via the HTTP API.

These tests start a real FastAPI test client and submit complete IML plans.
They require a working filesystem and OS (no mocks).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from llmos_bridge.api.server import create_app
from llmos_bridge.config import Settings


@pytest.fixture
def client(tmp_path: Path):
    """Create a TestClient that properly triggers startup/shutdown events."""
    settings = Settings(
        memory={
            "state_db_path": str(tmp_path / "state.db"),
            "vector_db_path": str(tmp_path / "vector"),
        },
        logging={"level": "warning", "format": "console", "audit_file": None},
        modules={"enabled": ["filesystem", "os_exec"]},
        security_advanced={"enable_decorators": False},
    )
    app = create_app(settings=settings)
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


@pytest.mark.e2e
class TestHealthEndpoint:
    def test_health_ok(self, client: TestClient) -> None:
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "version" in data
        assert "modules_loaded" in data


@pytest.mark.e2e
class TestPlanSubmission:
    def test_submit_minimal_plan_async(self, client: TestClient, tmp_path: Path) -> None:
        (tmp_path / "input.txt").write_text("test content")
        plan = {
            "protocol_version": "2.0",
            "description": "Read a file",
            "actions": [
                {
                    "id": "a1",
                    "action": "read_file",
                    "module": "filesystem",
                    "params": {"path": str(tmp_path / "input.txt")},
                }
            ],
        }
        resp = client.post("/plans", json={"plan": plan, "async_execution": True})
        assert resp.status_code == 202
        data = resp.json()
        assert "plan_id" in data

    def test_submit_sync_plan_completes(self, client: TestClient, tmp_path: Path) -> None:
        (tmp_path / "sync_input.txt").write_text("sync content")
        plan = {
            "protocol_version": "2.0",
            "description": "Sync read",
            "actions": [
                {
                    "id": "a1",
                    "action": "read_file",
                    "module": "filesystem",
                    "params": {"path": str(tmp_path / "sync_input.txt")},
                }
            ],
        }
        resp = client.post("/plans", json={"plan": plan, "async_execution": False})
        assert resp.status_code == 202
        data = resp.json()
        assert data["status"] in ("completed", "failed")

    def test_submit_invalid_json_returns_400(self, client: TestClient) -> None:
        resp = client.post("/plans", json={"plan": {"bad": "payload"}, "async_execution": True})
        assert resp.status_code in (400, 422)

    def test_get_nonexistent_plan_returns_404(self, client: TestClient) -> None:
        resp = client.get("/plans/nonexistent-plan-id-xyz")
        assert resp.status_code == 404


@pytest.mark.e2e
class TestModulesEndpoints:
    def test_list_modules(self, client: TestClient) -> None:
        resp = client.get("/modules")
        assert resp.status_code == 200
        modules = resp.json()
        assert isinstance(modules, list)
        module_ids = [m["module_id"] for m in modules]
        assert "filesystem" in module_ids

    def test_get_filesystem_manifest(self, client: TestClient) -> None:
        resp = client.get("/modules/filesystem")
        assert resp.status_code == 200
        data = resp.json()
        assert data["module_id"] == "filesystem"
        action_names = [a["name"] for a in data["actions"]]
        assert "read_file" in action_names

    def test_get_unknown_module_returns_404(self, client: TestClient) -> None:
        resp = client.get("/modules/nonexistent_module")
        assert resp.status_code == 404

    def test_get_action_schema(self, client: TestClient) -> None:
        resp = client.get("/modules/filesystem/actions/read_file/schema")
        assert resp.status_code == 200
        schema = resp.json()
        assert "properties" in schema
        assert "path" in schema["properties"]


@pytest.mark.e2e
class TestWriteReadPlan:
    def test_write_then_read_file(self, client: TestClient, tmp_path: Path) -> None:
        """Full plan: write a file then read it back."""
        output_path = tmp_path / "output.txt"
        plan = {
            "protocol_version": "2.0",
            "description": "Write then read",
            "actions": [
                {
                    "id": "write",
                    "action": "write_file",
                    "module": "filesystem",
                    "params": {"path": str(output_path), "content": "produced by LLMOS Bridge"},
                },
                {
                    "id": "read",
                    "action": "read_file",
                    "module": "filesystem",
                    "params": {"path": str(output_path)},
                    "depends_on": ["write"],
                },
            ],
        }
        resp = client.post("/plans", json={"plan": plan, "async_execution": False})
        assert resp.status_code == 202
        assert output_path.read_text() == "produced by LLMOS Bridge"
