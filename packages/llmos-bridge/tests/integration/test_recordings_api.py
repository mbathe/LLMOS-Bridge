"""Integration tests for Shadow Recorder REST API.

Tests the full lifecycle via FastAPI TestClient:
  - 503 when recording is disabled (default)
  - Start, list, get, stop, replay, delete lifecycle
  - Auto-tagging plans to active recording (POST /plans auto-appends)
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
def client_no_recording(tmp_path: Path) -> TestClient:
    """App with recording disabled (default)."""
    settings = Settings(
        memory={"state_db_path": str(tmp_path / "state.db"), "vector_db_path": str(tmp_path / "vector")},
        logging={"level": "warning", "format": "console", "audit_file": None},
        modules={"enabled": ["filesystem"]},
        security={"permission_profile": "unrestricted", "require_approval_for": []},
        security_advanced={"enable_decorators": False},
    )
    app = create_app(settings=settings)
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    """App with recording enabled."""
    settings = Settings(
        memory={"state_db_path": str(tmp_path / "state.db"), "vector_db_path": str(tmp_path / "vector")},
        logging={"level": "warning", "format": "console", "audit_file": None},
        modules={"enabled": ["filesystem", "recording"]},
        security={"permission_profile": "unrestricted", "require_approval_for": []},
        security_advanced={"enable_decorators": False},
        recording={"enabled": True, "db_path": str(tmp_path / "recordings.db")},
    )
    app = create_app(settings=settings)
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


# ---------------------------------------------------------------------------
# Tests: recording disabled
# ---------------------------------------------------------------------------


class TestRecordingDisabled:
    def test_list_returns_503_when_disabled(self, client_no_recording: TestClient) -> None:
        resp = client_no_recording.get("/recordings")
        assert resp.status_code == 503
        assert "not enabled" in resp.json()["detail"].lower()

    def test_start_returns_503_when_disabled(self, client_no_recording: TestClient) -> None:
        resp = client_no_recording.post("/recordings", json={"title": "test"})
        assert resp.status_code == 503

    def test_get_returns_503_when_disabled(self, client_no_recording: TestClient) -> None:
        resp = client_no_recording.get("/recordings/rec-123")
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Tests: recording enabled — basic lifecycle
# ---------------------------------------------------------------------------


class TestRecordingLifecycle:
    def test_list_empty_initially(self, client: TestClient) -> None:
        resp = client.get("/recordings")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["recordings"] == []
        assert data["active_recording_id"] is None

    def test_start_recording_returns_201(self, client: TestClient) -> None:
        resp = client.post("/recordings", json={"title": "My Workflow", "description": "Testing"})
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "My Workflow"
        assert data["status"] == "active"
        assert data["recording_id"].startswith("rec-")
        assert data["message"] == "Recording started"

    def test_start_recording_appears_in_list(self, client: TestClient) -> None:
        client.post("/recordings", json={"title": "Listed"})
        resp = client.get("/recordings")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["active_recording_id"] is not None

    def test_get_recording_returns_full_details(self, client: TestClient) -> None:
        start_resp = client.post("/recordings", json={"title": "Detail Test"})
        recording_id = start_resp.json()["recording_id"]
        resp = client.get(f"/recordings/{recording_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["recording_id"] == recording_id
        assert data["title"] == "Detail Test"
        assert data["plans"] == []
        assert data["generated_plan"] is None

    def test_get_nonexistent_recording_returns_404(self, client: TestClient) -> None:
        resp = client.get("/recordings/nonexistent-id")
        assert resp.status_code == 404

    def test_stop_recording_sets_status_stopped(self, client: TestClient) -> None:
        start_resp = client.post("/recordings", json={"title": "Stop Me"})
        recording_id = start_resp.json()["recording_id"]
        resp = client.post(f"/recordings/{recording_id}/stop")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "stopped"
        assert data["stopped_at"] is not None
        assert data["message"] == "Recording stopped"

    def test_stop_nonexistent_recording_returns_404(self, client: TestClient) -> None:
        resp = client.post("/recordings/nonexistent/stop")
        assert resp.status_code == 404

    def test_replay_plan_available_after_stop(self, client: TestClient) -> None:
        start_resp = client.post("/recordings", json={"title": "Replay Test"})
        recording_id = start_resp.json()["recording_id"]
        client.post(f"/recordings/{recording_id}/stop")
        resp = client.get(f"/recordings/{recording_id}/replay")
        assert resp.status_code == 200
        plan = resp.json()
        assert "plan_id" in plan
        assert plan["execution_mode"] == "sequential"

    def test_replay_unavailable_before_stop(self, client: TestClient) -> None:
        start_resp = client.post("/recordings", json={"title": "Not Yet"})
        recording_id = start_resp.json()["recording_id"]
        resp = client.get(f"/recordings/{recording_id}/replay")
        assert resp.status_code == 409

    def test_delete_recording(self, client: TestClient) -> None:
        start_resp = client.post("/recordings", json={"title": "Delete Me"})
        recording_id = start_resp.json()["recording_id"]
        resp = client.delete(f"/recordings/{recording_id}")
        assert resp.status_code == 204
        assert client.get(f"/recordings/{recording_id}").status_code == 503 or \
               client.get(f"/recordings/{recording_id}").status_code == 404

    def test_delete_nonexistent_returns_404(self, client: TestClient) -> None:
        resp = client.delete("/recordings/nonexistent")
        assert resp.status_code == 404

    def test_list_filter_by_status_active(self, client: TestClient) -> None:
        client.post("/recordings", json={"title": "Active One"})
        start2 = client.post("/recordings", json={"title": "To Stop"})
        rec2_id = start2.json()["recording_id"]
        client.post(f"/recordings/{rec2_id}/stop")
        resp = client.get("/recordings?status=active")
        assert resp.status_code == 200
        data = resp.json()
        # Only one active (starting second auto-stopped the first)
        for r in data["recordings"]:
            assert r["status"] == "active"

    def test_list_filter_by_status_stopped(self, client: TestClient) -> None:
        start = client.post("/recordings", json={"title": "Will Stop"})
        rec_id = start.json()["recording_id"]
        client.post(f"/recordings/{rec_id}/stop")
        resp = client.get("/recordings?status=stopped")
        assert resp.status_code == 200
        data = resp.json()
        for r in data["recordings"]:
            assert r["status"] == "stopped"


# ---------------------------------------------------------------------------
# Tests: auto-tagging plans to active recording
# ---------------------------------------------------------------------------


def _submit_sync(client: TestClient, plan_id: str) -> dict:
    """Submit a simple plan synchronously and return the response JSON."""
    resp = client.post(
        "/plans",
        json={
            "plan": {
                "plan_id": plan_id,
                "description": "Test plan for recording",
                "actions": [
                    {
                        "id": "a1",
                        "action": "read_file",
                        "module": "filesystem",
                        "params": {"path": "/tmp/nonexistent_test_file.txt"},
                    }
                ],
            },
            "async_execution": False,
        },
    )
    return resp.json()


class TestAutoTagging:
    def test_plans_captured_in_active_recording(self, client: TestClient, tmp_path: Path) -> None:
        """Plans executed during an active recording are captured."""
        start_resp = client.post("/recordings", json={"title": "Auto Tag Test"})
        recording_id = start_resp.json()["recording_id"]

        # Submit a plan (it may fail but should still be recorded)
        _submit_sync(client, "auto-tag-plan-001")

        # Stop and verify
        stop_resp = client.post(f"/recordings/{recording_id}/stop")
        assert stop_resp.status_code == 200

        # Get the recording
        get_resp = client.get(f"/recordings/{recording_id}")
        data = get_resp.json()
        assert data["plan_count"] >= 1
        plan_ids = [p["plan_id"] for p in data["plans"]]
        assert "auto-tag-plan-001" in plan_ids

    def test_plans_not_captured_when_no_active_recording(
        self, client: TestClient
    ) -> None:
        """Plans submitted without an active recording are not captured anywhere."""
        _submit_sync(client, "no-record-plan-001")
        resp = client.get("/recordings")
        assert resp.json()["count"] == 0  # No recordings exist

    def test_replay_plan_contains_captured_actions(self, client: TestClient, tmp_path: Path) -> None:
        """The generated replay plan includes actions from captured plans."""
        start_resp = client.post("/recordings", json={"title": "Replay Content Test"})
        recording_id = start_resp.json()["recording_id"]

        _submit_sync(client, "replay-content-plan-001")
        client.post(f"/recordings/{recording_id}/stop")

        replay_resp = client.get(f"/recordings/{recording_id}/replay")
        assert replay_resp.status_code == 200
        replay_plan = replay_resp.json()
        # The replay plan should have the action from our submitted plan (prefixed)
        action_ids = [a["id"] for a in replay_plan.get("actions", [])]
        # At least one action should be present, prefixed with "p1_"
        assert any(aid.startswith("p1_") for aid in action_ids)

    def test_multiple_plans_all_captured(self, client: TestClient) -> None:
        """Multiple plans submitted during a session are all captured."""
        start_resp = client.post("/recordings", json={"title": "Multi Plan"})
        recording_id = start_resp.json()["recording_id"]

        _submit_sync(client, "mp-plan-001")
        _submit_sync(client, "mp-plan-002")

        client.post(f"/recordings/{recording_id}/stop")
        get_resp = client.get(f"/recordings/{recording_id}")
        data = get_resp.json()
        assert data["plan_count"] == 2
