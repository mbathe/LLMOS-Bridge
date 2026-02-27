"""Integration tests — Plan Groups API route (POST /plan-groups).

Uses FastAPI TestClient with a real (but in-memory/tmp) app instance.
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
        modules={"enabled": ["filesystem"]},
        security_advanced={"enable_decorators": False},
    )
    app = create_app(settings=settings)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _make_plan(tmp_path: Path, plan_id: str, content: str = "hello") -> dict:
    """Build a minimal valid IML plan dict that reads a file."""
    f = tmp_path / f"{plan_id}.txt"
    f.write_text(content)
    return {
        "protocol_version": "2.0",
        "plan_id": plan_id,
        "description": f"Plan {plan_id}",
        "actions": [
            {
                "id": "a1",
                "action": "read_file",
                "module": "filesystem",
                "params": {"path": str(f)},
            }
        ],
    }


# ---------------------------------------------------------------------------
# POST /plan-groups — success
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestSubmitPlanGroup:
    def test_single_plan_completes(self, client: TestClient, tmp_path: Path) -> None:
        plan = _make_plan(tmp_path, "single-001")
        resp = client.post(
            "/plan-groups",
            json={"plans": [plan], "timeout": 30},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert data["summary"]["total"] == 1
        assert data["summary"]["completed"] == 1
        assert data["summary"]["failed"] == 0
        assert "single-001" in data["results"]
        assert data["duration"] >= 0

    def test_multiple_plans_complete(self, client: TestClient, tmp_path: Path) -> None:
        plans = [
            _make_plan(tmp_path, f"multi-{i}", f"content-{i}") for i in range(3)
        ]
        resp = client.post(
            "/plan-groups",
            json={"plans": plans, "timeout": 30},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert data["summary"]["total"] == 3
        assert data["summary"]["completed"] == 3
        assert len(data["results"]) == 3

    def test_custom_group_id(self, client: TestClient, tmp_path: Path) -> None:
        plan = _make_plan(tmp_path, "gid-test-001")
        resp = client.post(
            "/plan-groups",
            json={"plans": [plan], "group_id": "my-custom-group", "timeout": 30},
        )
        assert resp.status_code == 200
        assert resp.json()["group_id"] == "my-custom-group"

    def test_generated_group_id(self, client: TestClient, tmp_path: Path) -> None:
        plan = _make_plan(tmp_path, "gen-gid-001")
        resp = client.post(
            "/plan-groups",
            json={"plans": [plan], "timeout": 30},
        )
        assert resp.status_code == 200
        assert resp.json()["group_id"].startswith("group_")

    def test_max_concurrent(self, client: TestClient, tmp_path: Path) -> None:
        plans = [_make_plan(tmp_path, f"conc-{i}") for i in range(5)]
        resp = client.post(
            "/plan-groups",
            json={"plans": plans, "max_concurrent": 2, "timeout": 30},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "completed"
        assert resp.json()["summary"]["total"] == 5

    def test_results_contain_plan_status(self, client: TestClient, tmp_path: Path) -> None:
        plan = _make_plan(tmp_path, "status-check-001")
        resp = client.post(
            "/plan-groups",
            json={"plans": [plan], "timeout": 30},
        )
        data = resp.json()
        result = data["results"]["status-check-001"]
        assert result["status"] == "completed"
        assert result["actions"] == 1

    def test_errors_dict_empty_on_success(self, client: TestClient, tmp_path: Path) -> None:
        plan = _make_plan(tmp_path, "no-errors-001")
        resp = client.post(
            "/plan-groups",
            json={"plans": [plan], "timeout": 30},
        )
        assert resp.json()["errors"] == {}


# ---------------------------------------------------------------------------
# POST /plan-groups — partial failure
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestPlanGroupPartialFailure:
    def test_one_bad_plan_partial_failure(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        good_plan = _make_plan(tmp_path, "good-001")
        bad_plan = {
            "protocol_version": "2.0",
            "plan_id": "bad-001",
            "description": "Reads a nonexistent file",
            "actions": [
                {
                    "id": "a1",
                    "action": "read_file",
                    "module": "filesystem",
                    "params": {"path": "/nonexistent/file/that/does/not/exist.txt"},
                }
            ],
        }
        resp = client.post(
            "/plan-groups",
            json={"plans": [good_plan, bad_plan], "timeout": 30},
        )
        assert resp.status_code == 200
        data = resp.json()
        # At least the good plan should complete
        assert data["summary"]["total"] == 2
        assert data["summary"]["completed"] >= 1


# ---------------------------------------------------------------------------
# POST /plan-groups — validation errors
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestPlanGroupValidation:
    def test_empty_plans_returns_422(self, client: TestClient) -> None:
        resp = client.post(
            "/plan-groups",
            json={"plans": [], "timeout": 30},
        )
        assert resp.status_code == 422

    def test_invalid_plan_content_returns_422(self, client: TestClient) -> None:
        resp = client.post(
            "/plan-groups",
            json={"plans": [{"completely": "invalid"}], "timeout": 30},
        )
        assert resp.status_code == 422

    def test_missing_plans_field_returns_422(self, client: TestClient) -> None:
        resp = client.post(
            "/plan-groups",
            json={"timeout": 30},
        )
        assert resp.status_code == 422

    def test_max_concurrent_too_high_returns_422(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        plan = _make_plan(tmp_path, "conc-val-001")
        resp = client.post(
            "/plan-groups",
            json={"plans": [plan], "max_concurrent": 999},
        )
        assert resp.status_code == 422

    def test_timeout_too_low_returns_422(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        plan = _make_plan(tmp_path, "timeout-val-001")
        resp = client.post(
            "/plan-groups",
            json={"plans": [plan], "timeout": 1},
        )
        assert resp.status_code == 422
