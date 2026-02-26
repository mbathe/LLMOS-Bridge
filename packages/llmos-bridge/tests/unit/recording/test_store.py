"""Unit tests for RecordingStore (SQLite persistence)."""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from llmos_bridge.recording.models import RecordedPlan, RecordingStatus, WorkflowRecording
from llmos_bridge.recording.store import RecordingStore


@pytest_asyncio.fixture
async def store(tmp_path: Path) -> RecordingStore:
    s = RecordingStore(tmp_path / "recordings.db")
    await s.init()
    yield s
    await s.close()


def _make_recording(title: str = "Test") -> WorkflowRecording:
    return WorkflowRecording.create(title=title, description="desc")


def _make_plan(recording_id: str, plan_id: str, seq: int) -> RecordedPlan:
    return RecordedPlan(
        plan_id=plan_id,
        sequence=seq,
        added_at=1.0,
        plan_data={"plan_id": plan_id, "actions": []},
        final_status="completed",
        action_count=0,
    )


@pytest.mark.asyncio
class TestRecordingStore:
    async def test_init_creates_tables(self, store: RecordingStore) -> None:
        # Should not raise; store is already initialised by fixture
        assert store._conn is not None

    async def test_save_and_get_roundtrip(self, store: RecordingStore) -> None:
        rec = _make_recording("My Workflow")
        await store.save(rec)
        loaded = await store.get(rec.recording_id)
        assert loaded is not None
        assert loaded.recording_id == rec.recording_id
        assert loaded.title == "My Workflow"
        assert loaded.status == RecordingStatus.ACTIVE

    async def test_get_nonexistent_returns_none(self, store: RecordingStore) -> None:
        result = await store.get("nonexistent-id")
        assert result is None

    async def test_add_plan_and_get_loads_plans(self, store: RecordingStore) -> None:
        rec = _make_recording()
        await store.save(rec)
        plan = _make_plan(rec.recording_id, "p1", 1)
        await store.add_plan(rec.recording_id, plan)
        loaded = await store.get(rec.recording_id)
        assert loaded is not None
        assert len(loaded.plans) == 1
        assert loaded.plans[0].plan_id == "p1"
        assert loaded.plans[0].sequence == 1

    async def test_plans_ordered_by_sequence(self, store: RecordingStore) -> None:
        rec = _make_recording()
        await store.save(rec)
        await store.add_plan(rec.recording_id, _make_plan(rec.recording_id, "p2", 2))
        await store.add_plan(rec.recording_id, _make_plan(rec.recording_id, "p1", 1))
        loaded = await store.get(rec.recording_id)
        assert loaded is not None
        assert loaded.plans[0].sequence == 1
        assert loaded.plans[1].sequence == 2

    async def test_update_status_to_stopped(self, store: RecordingStore) -> None:
        rec = _make_recording()
        await store.save(rec)
        generated = {"plan_id": "replay-x", "actions": []}
        await store.update_status(
            rec.recording_id,
            RecordingStatus.STOPPED,
            stopped_at=99.0,
            generated_plan=generated,
        )
        loaded = await store.get(rec.recording_id)
        assert loaded is not None
        assert loaded.status == RecordingStatus.STOPPED
        assert loaded.stopped_at == 99.0
        assert loaded.generated_plan is not None
        assert loaded.generated_plan["plan_id"] == "replay-x"

    async def test_delete_removes_recording_and_plans(self, store: RecordingStore) -> None:
        rec = _make_recording()
        await store.save(rec)
        await store.add_plan(rec.recording_id, _make_plan(rec.recording_id, "p1", 1))
        deleted = await store.delete(rec.recording_id)
        assert deleted is True
        assert await store.get(rec.recording_id) is None

    async def test_delete_nonexistent_returns_false(self, store: RecordingStore) -> None:
        result = await store.delete("does-not-exist")
        assert result is False

    async def test_list_all_empty(self, store: RecordingStore) -> None:
        result = await store.list_all()
        assert result == []

    async def test_list_all_returns_all(self, store: RecordingStore) -> None:
        r1 = _make_recording("R1")
        r2 = _make_recording("R2")
        await store.save(r1)
        await store.save(r2)
        result = await store.list_all()
        assert len(result) == 2

    async def test_list_all_status_filter(self, store: RecordingStore) -> None:
        r1 = _make_recording("Active")
        r2 = _make_recording("Stopped")
        await store.save(r1)
        await store.save(r2)
        await store.update_status(r2.recording_id, RecordingStatus.STOPPED, stopped_at=1.0)
        active = await store.list_all(status=RecordingStatus.ACTIVE)
        stopped = await store.list_all(status=RecordingStatus.STOPPED)
        assert len(active) == 1
        assert active[0].title == "Active"
        assert len(stopped) == 1
        assert stopped[0].title == "Stopped"

    async def test_save_idempotent_replace(self, store: RecordingStore) -> None:
        rec = _make_recording("Original")
        await store.save(rec)
        rec.title = "Updated"
        await store.save(rec)
        loaded = await store.get(rec.recording_id)
        assert loaded is not None
        assert loaded.title == "Updated"
