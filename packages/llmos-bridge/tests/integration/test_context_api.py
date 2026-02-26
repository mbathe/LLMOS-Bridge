"""Integration tests — GET /context endpoint.

Uses FastAPI TestClient with a real (but tmp) app instance to test the
system prompt generation endpoint end-to-end.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from llmos_bridge.api.server import create_app
from llmos_bridge.config import Settings


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    settings = Settings(
        memory={
            "state_db_path": str(tmp_path / "state.db"),
            "vector_db_path": str(tmp_path / "vector"),
        },
        logging={"level": "warning", "format": "console", "audit_file": None},
        modules={"enabled": ["filesystem", "os_exec"]},
    )
    app = create_app(settings=settings)
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


@pytest.fixture
def client_single_module(tmp_path: Path) -> TestClient:
    settings = Settings(
        memory={
            "state_db_path": str(tmp_path / "state.db"),
            "vector_db_path": str(tmp_path / "vector"),
        },
        logging={"level": "warning", "format": "console", "audit_file": None},
        modules={"enabled": ["filesystem"]},
    )
    app = create_app(settings=settings)
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


# ---------------------------------------------------------------------------
# GET /context (format=full, default)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestContextEndpointFull:
    def test_returns_200(self, client: TestClient) -> None:
        resp = client.get("/context")
        assert resp.status_code == 200

    def test_response_has_system_prompt(self, client: TestClient) -> None:
        data = client.get("/context").json()
        assert "system_prompt" in data
        assert isinstance(data["system_prompt"], str)
        assert len(data["system_prompt"]) > 100

    def test_response_has_metadata(self, client: TestClient) -> None:
        data = client.get("/context").json()
        assert "permission_profile" in data
        assert data["permission_profile"] == "local_worker"
        assert "daemon_version" in data
        assert "modules" in data
        assert "total_actions" in data

    def test_modules_listed(self, client: TestClient) -> None:
        data = client.get("/context").json()
        module_ids = [m["module_id"] for m in data["modules"]]
        assert "filesystem" in module_ids
        assert "os_exec" in module_ids

    def test_total_actions_correct(self, client: TestClient) -> None:
        data = client.get("/context").json()
        total = data["total_actions"]
        assert total > 0
        # Sum of per-module counts should match
        assert total == sum(m["action_count"] for m in data["modules"])

    def test_prompt_contains_iml_rules(self, client: TestClient) -> None:
        data = client.get("/context").json()
        prompt = data["system_prompt"]
        assert "IML Protocol v2" in prompt
        assert "protocol_version" in prompt

    def test_prompt_contains_modules(self, client: TestClient) -> None:
        data = client.get("/context").json()
        prompt = data["system_prompt"]
        assert "filesystem" in prompt
        assert "os_exec" in prompt


# ---------------------------------------------------------------------------
# GET /context?format=prompt (raw text)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestContextEndpointPrompt:
    def test_returns_plain_text(self, client: TestClient) -> None:
        resp = client.get("/context", params={"format": "prompt"})
        assert resp.status_code == 200
        assert "text/plain" in resp.headers["content-type"]

    def test_text_contains_system_prompt(self, client: TestClient) -> None:
        resp = client.get("/context", params={"format": "prompt"})
        text = resp.text
        assert "LLMOS Bridge" in text
        assert "IML Protocol v2" in text
        assert "filesystem" in text


# ---------------------------------------------------------------------------
# Query parameters
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestContextEndpointParams:
    def test_include_schemas_false(self, client: TestClient) -> None:
        data = client.get(
            "/context", params={"include_schemas": "false"}
        ).json()
        prompt = data["system_prompt"]
        # Should have module names but not detailed param schemas
        assert "filesystem" in prompt
        # No "*(required)*" markers for params
        assert "*(required)*" not in prompt

    def test_include_examples_false(self, client: TestClient) -> None:
        data = client.get(
            "/context", params={"include_examples": "false"}
        ).json()
        prompt = data["system_prompt"]
        assert "Examples" not in prompt

    def test_max_actions_per_module(self, client: TestClient) -> None:
        data = client.get(
            "/context", params={"max_actions_per_module": 2}
        ).json()
        prompt = data["system_prompt"]
        assert "more actions" in prompt

    def test_single_module_no_chained_example(self, client_single_module: TestClient) -> None:
        """When only filesystem is loaded (no os_exec), no chained example."""
        data = client_single_module.get("/context").json()
        prompt = data["system_prompt"]
        # Should still have the file read example
        assert "Read a file" in prompt


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestContextEdgeCases:
    def test_no_modules_loaded(self, tmp_path: Path) -> None:
        settings = Settings(
            memory={
                "state_db_path": str(tmp_path / "state.db"),
                "vector_db_path": str(tmp_path / "vector"),
            },
            logging={"level": "warning", "format": "console", "audit_file": None},
            modules={"enabled": []},
        )
        app = create_app(settings=settings)
        with TestClient(app, raise_server_exceptions=True) as c:
            data = c.get("/context").json()
            prompt = data["system_prompt"]
            assert "No modules loaded" in prompt
            assert data["total_actions"] == 0
