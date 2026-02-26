"""Unit tests for WorkflowReplayer."""

import pytest

from llmos_bridge.recording.models import RecordedPlan, RecordingStatus, WorkflowRecording
from llmos_bridge.recording.replayer import WorkflowReplayer


def _make_recording(title: str = "Test") -> WorkflowRecording:
    return WorkflowRecording(
        recording_id="rec-abc123",
        title=title,
        description="",
        status=RecordingStatus.ACTIVE,
        created_at=1.0,
        stopped_at=None,
    )


def _make_plan(plan_id: str, actions: list[dict]) -> dict:
    return {"plan_id": plan_id, "actions": actions}


class TestWorkflowReplayer:
    def setup_method(self) -> None:
        self.replayer = WorkflowReplayer()

    def test_empty_recording_returns_empty_plan(self) -> None:
        rec = _make_recording()
        result = self.replayer.generate(rec)
        assert result["actions"] == []
        assert result["plan_id"].startswith("replay-")
        assert result["protocol_version"] == "2.0"
        assert result["execution_mode"] == "sequential"

    def test_single_plan_actions_are_prefixed(self) -> None:
        rec = _make_recording()
        rec.plans.append(
            RecordedPlan(
                plan_id="p1",
                sequence=1,
                added_at=0.0,
                plan_data=_make_plan("p1", [
                    {"id": "a1", "action": "read_file", "module": "filesystem", "params": {}},
                    {"id": "a2", "action": "write_file", "module": "filesystem", "params": {},
                     "depends_on": ["a1"]},
                ]),
                final_status="completed",
                action_count=2,
            )
        )
        result = self.replayer.generate(rec)
        action_ids = [a["id"] for a in result["actions"]]
        assert "p1_a1" in action_ids
        assert "p1_a2" in action_ids

    def test_depends_on_remapped_to_prefixed_ids(self) -> None:
        rec = _make_recording()
        rec.plans.append(
            RecordedPlan(
                plan_id="p1",
                sequence=1,
                added_at=0.0,
                plan_data=_make_plan("p1", [
                    {"id": "a1", "action": "read_file", "module": "filesystem", "params": {}},
                    {"id": "a2", "action": "write_file", "module": "filesystem", "params": {},
                     "depends_on": ["a1"]},
                ]),
                final_status="completed",
                action_count=2,
            )
        )
        result = self.replayer.generate(rec)
        a2 = next(a for a in result["actions"] if a["id"] == "p1_a2")
        assert a2["depends_on"] == ["p1_a1"]

    def test_two_plans_chained(self) -> None:
        rec = _make_recording()
        rec.plans.append(
            RecordedPlan(
                plan_id="p1",
                sequence=1,
                added_at=0.0,
                plan_data=_make_plan("p1", [
                    {"id": "a1", "action": "read_file", "module": "filesystem", "params": {}},
                ]),
                final_status="completed",
                action_count=1,
            )
        )
        rec.plans.append(
            RecordedPlan(
                plan_id="p2",
                sequence=2,
                added_at=1.0,
                plan_data=_make_plan("p2", [
                    {"id": "b1", "action": "write_file", "module": "filesystem", "params": {}},
                ]),
                final_status="completed",
                action_count=1,
            )
        )
        result = self.replayer.generate(rec)
        b1 = next(a for a in result["actions"] if a["id"] == "p2_b1")
        # b1 should depend on the last action of plan 1
        assert "p1_a1" in b1.get("depends_on", [])

    def test_metadata_includes_recording_id(self) -> None:
        rec = _make_recording()
        result = self.replayer.generate(rec)
        assert result["metadata"]["recording_id"] == rec.recording_id
        assert result["metadata"]["source"] == "shadow_recorder"

    def test_generate_llm_context_empty(self) -> None:
        rec = _make_recording(title="My Workflow")
        ctx = self.replayer.generate_llm_context(rec)
        assert "My Workflow" in ctx
        assert "Plans captured: 0" in ctx

    def test_generate_llm_context_with_plan(self) -> None:
        rec = _make_recording(title="Office Workflow")
        rec.plans.append(
            RecordedPlan(
                plan_id="p1",
                sequence=1,
                added_at=0.0,
                plan_data=_make_plan("p1", [
                    {"id": "a1", "action": "read_file", "module": "filesystem",
                     "params": {"path": "/tmp/x.txt"}},
                ]),
                final_status="completed",
                action_count=1,
            )
        )
        ctx = self.replayer.generate_llm_context(rec)
        assert "Step 1" in ctx
        assert "filesystem.read_file" in ctx
