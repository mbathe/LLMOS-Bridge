"""Integration tests — Plans API routes (/plans, /plans/{id}, DELETE /plans/{id}, approve).

Uses FastAPI TestClient with a real (but in-memory/tmp) app instance.
"""

from __future__ import annotations

import time
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
    )
    app = create_app(settings=settings)
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


def _make_plan(tmp_path: Path, plan_id: str = "test-plan-001") -> dict:
    """Build a minimal valid IML plan dict."""
    (tmp_path / "hello.txt").write_text("hello")
    return {
        "protocol_version": "2.0",
        "plan_id": plan_id,
        "description": "Test plan",
        "actions": [
            {
                "id": "a1",
                "action": "read_file",
                "module": "filesystem",
                "params": {"path": str(tmp_path / "hello.txt")},
            }
        ],
    }


# ---------------------------------------------------------------------------
# POST /plans
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestSubmitPlan:
    def test_async_submit_returns_202(self, client: TestClient, tmp_path: Path) -> None:
        plan = _make_plan(tmp_path)
        resp = client.post("/plans", json={"plan": plan, "async_execution": True})
        assert resp.status_code == 202
        data = resp.json()
        assert "plan_id" in data
        assert data["status"] in ("pending", "completed", "running")

    def test_sync_submit_completes(self, client: TestClient, tmp_path: Path) -> None:
        plan = _make_plan(tmp_path)
        resp = client.post("/plans", json={"plan": plan, "async_execution": False})
        assert resp.status_code == 202
        data = resp.json()
        assert data["status"] in ("completed", "failed")

    def test_invalid_plan_returns_400_or_422(self, client: TestClient) -> None:
        resp = client.post(
            "/plans",
            json={"plan": {"completely": "invalid"}, "async_execution": True},
        )
        assert resp.status_code in (400, 422)

    def test_missing_plan_field_returns_422(self, client: TestClient) -> None:
        resp = client.post("/plans", json={"async_execution": True})
        assert resp.status_code == 422

    def test_plan_with_missing_action_fields_returns_error(
        self, client: TestClient
    ) -> None:
        plan = {
            "protocol_version": "2.0",
            "description": "Bad action",
            "actions": [{"id": "a1"}],  # missing module/action/params
        }
        resp = client.post("/plans", json={"plan": plan, "async_execution": True})
        assert resp.status_code in (400, 422)


# ---------------------------------------------------------------------------
# GET /plans
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestListPlans:
    def test_list_plans_empty(self, client: TestClient) -> None:
        resp = client.get("/plans")
        assert resp.status_code == 200
        data = resp.json()
        assert "plans" in data
        assert "total" in data
        assert isinstance(data["plans"], list)

    def test_list_plans_after_submission(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        plan = _make_plan(tmp_path)
        post_resp = client.post(
            "/plans", json={"plan": plan, "async_execution": False}
        )
        assert post_resp.status_code == 202

        resp = client.get("/plans")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1

    def test_list_plans_pagination_params(self, client: TestClient) -> None:
        resp = client.get("/plans?limit=10&page=1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["per_page"] == 10
        assert data["page"] == 1

    def test_list_plans_filter_by_status(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        plan = _make_plan(tmp_path, "filter-test-plan")
        client.post("/plans", json={"plan": plan, "async_execution": False})

        resp = client.get("/plans?status=completed")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /plans/{plan_id}
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestGetPlan:
    def test_get_nonexistent_plan_returns_404(self, client: TestClient) -> None:
        resp = client.get("/plans/no-such-plan-xyz")
        assert resp.status_code == 404

    def test_get_plan_after_submission(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        plan = _make_plan(tmp_path, "get-plan-test-001")
        post_resp = client.post(
            "/plans", json={"plan": plan, "async_execution": False}
        )
        assert post_resp.status_code == 202
        plan_id = post_resp.json()["plan_id"]

        get_resp = client.get(f"/plans/{plan_id}")
        assert get_resp.status_code == 200
        data = get_resp.json()
        assert data["plan_id"] == plan_id
        assert "status" in data
        assert "actions" in data

    def test_get_plan_actions_list(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        plan = _make_plan(tmp_path, "actions-test-001")
        post_resp = client.post(
            "/plans", json={"plan": plan, "async_execution": False}
        )
        plan_id = post_resp.json()["plan_id"]

        get_resp = client.get(f"/plans/{plan_id}")
        data = get_resp.json()
        assert len(data["actions"]) == 1
        action = data["actions"][0]
        assert "action_id" in action
        assert "status" in action


# ---------------------------------------------------------------------------
# DELETE /plans/{plan_id}
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestCancelPlan:
    def test_cancel_nonexistent_plan_returns_404(self, client: TestClient) -> None:
        resp = client.delete("/plans/no-such-plan-cancel")
        assert resp.status_code == 404

    def test_cancel_completed_plan_returns_204(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        plan = _make_plan(tmp_path, "cancel-test-001")
        post_resp = client.post(
            "/plans", json={"plan": plan, "async_execution": False}
        )
        plan_id = post_resp.json()["plan_id"]

        del_resp = client.delete(f"/plans/{plan_id}")
        assert del_resp.status_code == 204


# ---------------------------------------------------------------------------
# POST /plans/{plan_id}/actions/{action_id}/approve
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestApproveAction:
    def test_approve_nonexistent_plan_returns_409(self, client: TestClient) -> None:
        """Nonexistent plan has no pending approval → 409 not pending."""
        resp = client.post(
            "/plans/no-such-plan/actions/a1/approve",
            json={"approved": True},
        )
        assert resp.status_code == 409

    def test_approve_nonexistent_action_returns_409(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """Nonexistent action has no pending approval → 409 not pending."""
        plan = _make_plan(tmp_path, "approve-test-001")
        post_resp = client.post(
            "/plans", json={"plan": plan, "async_execution": False}
        )
        plan_id = post_resp.json()["plan_id"]

        resp = client.post(
            f"/plans/{plan_id}/actions/no-such-action/approve",
            json={"approved": True},
        )
        assert resp.status_code == 409

    def test_approve_action_not_awaiting_returns_409(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        plan = _make_plan(tmp_path, "approve-conflict-001")
        post_resp = client.post(
            "/plans", json={"plan": plan, "async_execution": False}
        )
        plan_id = post_resp.json()["plan_id"]

        # a1 is already completed (plan ran synchronously)
        resp = client.post(
            f"/plans/{plan_id}/actions/a1/approve",
            json={"approved": True},
        )
        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Auth token
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestAuthToken:
    def test_no_token_required_when_not_configured(
        self, client: TestClient
    ) -> None:
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_with_api_token_configured(self, tmp_path: Path) -> None:
        settings = Settings(
            memory={
                "state_db_path": str(tmp_path / "auth_state.db"),
                "vector_db_path": str(tmp_path / "vector"),
            },
            logging={"level": "warning", "format": "console", "audit_file": None},
            modules={"enabled": ["filesystem"]},
            security={"api_token": "secret-token", "permission_profile": "unrestricted"},
        )
        app = create_app(settings=settings)
        with TestClient(app, raise_server_exceptions=False) as c:
            # Without token — should return 401
            resp = c.get("/plans")
            assert resp.status_code == 401

            # With correct token — should succeed
            resp = c.get("/plans", headers={"X-LLMOS-Token": "secret-token"})
            assert resp.status_code == 200
