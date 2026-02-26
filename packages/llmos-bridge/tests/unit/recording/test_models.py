"""Unit tests for Shadow Recorder data models."""

import pytest

from llmos_bridge.recording.models import RecordedPlan, RecordingStatus, WorkflowRecording


class TestWorkflowRecording:
    def test_create_generates_unique_ids(self) -> None:
        r1 = WorkflowRecording.create(title="A")
        r2 = WorkflowRecording.create(title="B")
        assert r1.recording_id != r2.recording_id
        assert r1.recording_id.startswith("rec-")

    def test_create_sets_active_status(self) -> None:
        r = WorkflowRecording.create(title="Test")
        assert r.status == RecordingStatus.ACTIVE
        assert r.stopped_at is None
        assert r.plans == []
        assert r.generated_plan is None

    def test_to_summary_dict(self) -> None:
        r = WorkflowRecording.create(title="My Recording", description="desc")
        d = r.to_summary_dict()
        assert d["title"] == "My Recording"
        assert d["description"] == "desc"
        assert d["status"] == "active"
        assert d["plan_count"] == 0
        assert d["stopped_at"] is None
        assert "recording_id" in d

    def test_to_full_dict_includes_plans(self) -> None:
        r = WorkflowRecording.create(title="Full")
        r.plans.append(
            RecordedPlan(
                plan_id="p1",
                sequence=1,
                added_at=1.0,
                plan_data={"plan_id": "p1", "actions": []},
                final_status="completed",
                action_count=0,
            )
        )
        d = r.to_full_dict()
        assert len(d["plans"]) == 1
        assert d["plans"][0]["plan_id"] == "p1"
        assert d["generated_plan"] is None

    def test_to_full_dict_plan_count_reflects_plans(self) -> None:
        r = WorkflowRecording.create(title="Count")
        assert r.to_full_dict()["plan_count"] == 0
        r.plans.append(
            RecordedPlan(
                plan_id="x",
                sequence=1,
                added_at=0.0,
                plan_data={},
                final_status="completed",
                action_count=1,
            )
        )
        assert r.to_full_dict()["plan_count"] == 1


class TestRecordingStatus:
    def test_values(self) -> None:
        assert RecordingStatus.ACTIVE.value == "active"
        assert RecordingStatus.STOPPED.value == "stopped"

    def test_is_str(self) -> None:
        assert isinstance(RecordingStatus.ACTIVE, str)
