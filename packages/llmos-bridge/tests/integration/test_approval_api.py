"""Integration tests — Approval system via the HTTP API.

These tests run a real daemon (in-process via TestClient) and test the full
approval flow:  submit plan → action awaits approval → approve via API →
action executes → plan completes.
"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from llmos_bridge.api.server import create_app
from llmos_bridge.config import Settings


@pytest.fixture
def approval_settings(tmp_path: Path) -> Settings:
    """Settings with run_command requiring approval."""
    return Settings(
        memory={
            "state_db_path": str(tmp_path / "state.db"),
            "vector_db_path": str(tmp_path / "vector"),
        },
        logging={"level": "warning", "format": "console", "audit_file": None},
        modules={"enabled": ["filesystem", "os_exec"]},
        # run_command requires approval — this is the key config.
        security={
            "require_approval_for": ["os_exec.run_command"],
            "approval_timeout_seconds": 30,
        },
        security_advanced={"enable_decorators": False},
    )


@pytest.fixture
def no_approval_settings(tmp_path: Path) -> Settings:
    """Settings with NO approval requirements (baseline)."""
    return Settings(
        memory={
            "state_db_path": str(tmp_path / "state.db"),
            "vector_db_path": str(tmp_path / "vector"),
        },
        logging={"level": "warning", "format": "console", "audit_file": None},
        modules={"enabled": ["filesystem", "os_exec"]},
        security={"require_approval_for": []},
        security_advanced={"enable_decorators": False},
    )


@pytest.fixture
def approval_client(approval_settings: Settings) -> TestClient:
    app = create_app(settings=approval_settings)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def no_approval_client(no_approval_settings: Settings) -> TestClient:
    app = create_app(settings=no_approval_settings)
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


def _make_read_plan(file_path: str) -> dict:
    return {
        "plan_id": "test-read",
        "protocol_version": "2.0",
        "description": "Read a file",
        "actions": [
            {
                "id": "read",
                "action": "read_file",
                "module": "filesystem",
                "params": {"path": file_path},
            }
        ],
    }


def _make_command_plan(command: list[str], plan_id: str = "test-cmd") -> dict:
    return {
        "plan_id": plan_id,
        "protocol_version": "2.0",
        "description": "Run a command",
        "actions": [
            {
                "id": "cmd",
                "action": "run_command",
                "module": "os_exec",
                "params": {"command": command},
            }
        ],
    }


# ---------------------------------------------------------------------------
# Baseline: no approval required
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestNoApprovalBaseline:
    def test_read_file_no_approval_needed(
        self, no_approval_client: TestClient, tmp_path: Path
    ) -> None:
        test_file = tmp_path / "hello.txt"
        test_file.write_text("hello")

        resp = no_approval_client.post(
            "/plans",
            json={"plan": _make_read_plan(str(test_file)), "async_execution": False},
        )
        assert resp.status_code == 202
        data = resp.json()
        assert data["status"] == "completed"
        assert len(data["actions"]) == 1
        assert data["actions"][0]["status"] == "completed"

    def test_run_command_no_approval_when_not_configured(
        self, no_approval_client: TestClient
    ) -> None:
        resp = no_approval_client.post(
            "/plans",
            json={
                "plan": _make_command_plan(["echo", "hello"]),
                "async_execution": False,
            },
        )
        assert resp.status_code == 202
        data = resp.json()
        assert data["status"] == "completed"


# ---------------------------------------------------------------------------
# Approval flow: approve → execute → complete
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestApprovalFlow:
    def test_approve_then_complete(self, approval_client: TestClient) -> None:
        """Full flow: submit → async → approve → poll → completed."""
        # Submit plan async (run_command requires approval).
        resp = approval_client.post(
            "/plans",
            json={
                "plan": _make_command_plan(["echo", "approved"]),
                "async_execution": True,
            },
        )
        assert resp.status_code == 202
        plan_id = resp.json()["plan_id"]

        # Wait for action to reach AWAITING_APPROVAL.
        for _ in range(50):
            time.sleep(0.05)
            status_resp = approval_client.get(f"/plans/{plan_id}")
            actions = status_resp.json().get("actions", [])
            if actions and actions[0]["status"] == "awaiting_approval":
                break
        else:
            pytest.fail("Action never reached awaiting_approval")

        # Check pending approvals.
        pending_resp = approval_client.get(f"/plans/{plan_id}/pending-approvals")
        assert pending_resp.status_code == 200
        pending = pending_resp.json()
        assert len(pending) == 1
        assert pending[0]["action_id"] == "cmd"
        assert pending[0]["module"] == "os_exec"

        # Approve the action.
        approve_resp = approval_client.post(
            f"/plans/{plan_id}/actions/cmd/approve",
            json={"decision": "approve", "approved_by": "test_user"},
        )
        assert approve_resp.status_code == 200
        assert approve_resp.json()["applied"] is True

        # Wait for plan to complete.
        for _ in range(50):
            time.sleep(0.05)
            status_resp = approval_client.get(f"/plans/{plan_id}")
            if status_resp.json()["status"] in ("completed", "failed"):
                break
        else:
            pytest.fail("Plan never completed after approval")

        final = status_resp.json()
        assert final["status"] == "completed"
        assert final["actions"][0]["status"] == "completed"

    def test_reject_then_fail(self, approval_client: TestClient) -> None:
        """Reject → action fails → plan fails."""
        resp = approval_client.post(
            "/plans",
            json={
                "plan": _make_command_plan(["echo", "rejected"], plan_id="test-reject"),
                "async_execution": True,
            },
        )
        plan_id = resp.json()["plan_id"]

        # Wait for AWAITING_APPROVAL.
        for _ in range(50):
            time.sleep(0.05)
            status_resp = approval_client.get(f"/plans/{plan_id}")
            actions = status_resp.json().get("actions", [])
            if actions and actions[0]["status"] == "awaiting_approval":
                break

        # Reject.
        approve_resp = approval_client.post(
            f"/plans/{plan_id}/actions/cmd/approve",
            json={"decision": "reject", "reason": "too dangerous"},
        )
        assert approve_resp.status_code == 200

        # Wait for plan to fail.
        for _ in range(50):
            time.sleep(0.05)
            status_resp = approval_client.get(f"/plans/{plan_id}")
            if status_resp.json()["status"] == "failed":
                break

        final = status_resp.json()
        assert final["status"] == "failed"
        assert final["actions"][0]["status"] == "failed"

    def test_skip_decision(self, approval_client: TestClient) -> None:
        """Skip → action skipped → plan considers it as 'all_completed'."""
        resp = approval_client.post(
            "/plans",
            json={
                "plan": _make_command_plan(["echo", "skip"], plan_id="test-skip"),
                "async_execution": True,
            },
        )
        plan_id = resp.json()["plan_id"]

        for _ in range(50):
            time.sleep(0.05)
            status_resp = approval_client.get(f"/plans/{plan_id}")
            actions = status_resp.json().get("actions", [])
            if actions and actions[0]["status"] == "awaiting_approval":
                break

        approval_client.post(
            f"/plans/{plan_id}/actions/cmd/approve",
            json={"decision": "skip"},
        )

        for _ in range(50):
            time.sleep(0.05)
            status_resp = approval_client.get(f"/plans/{plan_id}")
            if status_resp.json()["status"] in ("completed", "failed"):
                break

        final = status_resp.json()
        # Skipped is considered terminal — plan should complete.
        assert final["actions"][0]["status"] == "skipped"


# ---------------------------------------------------------------------------
# Legacy approve/reject (backwards compatibility)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestLegacyApproval:
    def test_legacy_approved_true(self, approval_client: TestClient) -> None:
        resp = approval_client.post(
            "/plans",
            json={
                "plan": _make_command_plan(["echo", "legacy"], plan_id="test-legacy"),
                "async_execution": True,
            },
        )
        plan_id = resp.json()["plan_id"]

        for _ in range(50):
            time.sleep(0.05)
            status_resp = approval_client.get(f"/plans/{plan_id}")
            actions = status_resp.json().get("actions", [])
            if actions and actions[0]["status"] == "awaiting_approval":
                break

        # Legacy field: approved=true
        approve_resp = approval_client.post(
            f"/plans/{plan_id}/actions/cmd/approve",
            json={"approved": True},
        )
        assert approve_resp.status_code == 200

        for _ in range(50):
            time.sleep(0.05)
            status_resp = approval_client.get(f"/plans/{plan_id}")
            if status_resp.json()["status"] in ("completed", "failed"):
                break

        assert status_resp.json()["status"] == "completed"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestApprovalEdgeCases:
    def test_approve_nonexistent_action(self, approval_client: TestClient) -> None:
        resp = approval_client.post(
            "/plans/nonexistent/actions/nope/approve",
            json={"decision": "approve"},
        )
        assert resp.status_code == 409

    def test_invalid_decision(self, approval_client: TestClient) -> None:
        resp = approval_client.post(
            "/plans",
            json={
                "plan": _make_command_plan(["echo", "x"], plan_id="test-invalid"),
                "async_execution": True,
            },
        )
        plan_id = resp.json()["plan_id"]

        for _ in range(50):
            time.sleep(0.05)
            status_resp = approval_client.get(f"/plans/{plan_id}")
            actions = status_resp.json().get("actions", [])
            if actions and actions[0]["status"] == "awaiting_approval":
                break

        resp = approval_client.post(
            f"/plans/{plan_id}/actions/cmd/approve",
            json={"decision": "dance"},
        )
        assert resp.status_code == 400

        # Clean up: reject so the plan doesn't hang.
        approval_client.post(
            f"/plans/{plan_id}/actions/cmd/approve",
            json={"decision": "reject"},
        )

    def test_pending_approvals_empty_when_none(
        self, no_approval_client: TestClient
    ) -> None:
        resp = no_approval_client.get("/plans/some-plan/pending-approvals")
        assert resp.status_code == 200
        assert resp.json() == []
