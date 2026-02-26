"""Unit tests for WorkflowRecorder."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from llmos_bridge.recording.models import RecordingStatus, WorkflowRecording
from llmos_bridge.recording.recorder import WorkflowRecorder
from llmos_bridge.recording.store import RecordingStore


@pytest_asyncio.fixture
async def store(tmp_path: Path) -> RecordingStore:
    s = RecordingStore(tmp_path / "rec.db")
    await s.init()
    yield s
    await s.close()


@pytest_asyncio.fixture
async def recorder(store: RecordingStore) -> WorkflowRecorder:
    return WorkflowRecorder(store=store)


@pytest.mark.asyncio
class TestWorkflowRecorder:
    async def test_initial_state(self, recorder: WorkflowRecorder) -> None:
        assert recorder.active_recording_id is None

    async def test_start_creates_active_recording(self, recorder: WorkflowRecorder) -> None:
        rec = await recorder.start(title="Test Recording")
        assert rec.status == RecordingStatus.ACTIVE
        assert recorder.active_recording_id == rec.recording_id

    async def test_start_second_auto_stops_first(self, recorder: WorkflowRecorder, store: RecordingStore) -> None:
        rec1 = await recorder.start(title="First")
        rec2 = await recorder.start(title="Second")
        assert recorder.active_recording_id == rec2.recording_id
        # First recording should be stopped
        loaded1 = await store.get(rec1.recording_id)
        assert loaded1 is not None
        assert loaded1.status == RecordingStatus.STOPPED

    async def test_stop_clears_active(self, recorder: WorkflowRecorder) -> None:
        rec = await recorder.start(title="Stoppable")
        assert recorder.active_recording_id == rec.recording_id
        stopped = await recorder.stop(rec.recording_id)
        assert stopped.status == RecordingStatus.STOPPED
        assert recorder.active_recording_id is None

    async def test_stop_generates_plan(self, recorder: WorkflowRecorder) -> None:
        rec = await recorder.start(title="With Plan")
        stopped = await recorder.stop(rec.recording_id)
        assert stopped.generated_plan is not None
        assert stopped.generated_plan["plan_id"].startswith("replay-")

    async def test_stop_idempotent(self, recorder: WorkflowRecorder) -> None:
        rec = await recorder.start(title="Idempotent")
        await recorder.stop(rec.recording_id)
        # Stopping again should return the same stopped recording without error
        stopped_again = await recorder.stop(rec.recording_id)
        assert stopped_again.status == RecordingStatus.STOPPED

    async def test_stop_nonexistent_raises(self, recorder: WorkflowRecorder) -> None:
        with pytest.raises(KeyError):
            await recorder.stop("nonexistent-id")

    async def test_add_plan_to_active_recording(
        self, recorder: WorkflowRecorder, store: RecordingStore
    ) -> None:
        rec = await recorder.start(title="Recording Plans")
        plan_data = {
            "plan_id": "test-plan-001",
            "actions": [
                {"id": "a1", "module": "filesystem", "action": "read_file", "params": {}},
            ],
        }

        # Mock exec_state
        mock_state = MagicMock()
        mock_state.plan_status.value = "completed"
        mock_state.actions = {"a1": MagicMock()}

        await recorder.add_plan(
            rec.recording_id,
            plan_data,
            "completed",
            1,
        )
        loaded = await store.get(rec.recording_id)
        assert loaded is not None
        assert len(loaded.plans) == 1
        assert loaded.plans[0].plan_id == "test-plan-001"

    async def test_add_plan_noop_for_stopped_recording(
        self, recorder: WorkflowRecorder, store: RecordingStore
    ) -> None:
        rec = await recorder.start(title="Stopped")
        await recorder.stop(rec.recording_id)
        await recorder.add_plan(rec.recording_id, {"plan_id": "p1"}, "completed", 0)
        loaded = await store.get(rec.recording_id)
        assert loaded is not None
        assert len(loaded.plans) == 0  # Not added to stopped recording

    async def test_add_plan_noop_for_nonexistent(self, recorder: WorkflowRecorder) -> None:
        # Should not raise
        await recorder.add_plan("nonexistent", {"plan_id": "p"}, "completed", 0)

    async def test_start_description_persisted(
        self, recorder: WorkflowRecorder, store: RecordingStore
    ) -> None:
        rec = await recorder.start(title="Full", description="A description")
        loaded = await store.get(rec.recording_id)
        assert loaded is not None
        assert loaded.description == "A description"
