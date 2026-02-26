"""Unit tests — triggers/store.py (TriggerStore)."""

from __future__ import annotations

from pathlib import Path

import pytest

from llmos_bridge.triggers.models import (
    TriggerCondition,
    TriggerDefinition,
    TriggerState,
    TriggerType,
)
from llmos_bridge.triggers.store import TriggerStore


def _make_trigger(name: str = "test", state: TriggerState = TriggerState.ACTIVE) -> TriggerDefinition:
    t = TriggerDefinition(
        name=name,
        condition=TriggerCondition(
            type=TriggerType.TEMPORAL,
            params={"interval_seconds": 60},
        ),
        plan_template={"plan_id": "t", "protocol_version": "2.0", "actions": []},
    )
    t.state = state
    return t


@pytest.fixture
async def store(tmp_path: Path) -> TriggerStore:
    s = TriggerStore(tmp_path / "triggers_test.db")
    await s.init()
    yield s
    await s.close()


@pytest.mark.unit
class TestTriggerStore:
    async def test_save_and_get(self, store: TriggerStore) -> None:
        t = _make_trigger("watch_docs")
        await store.save(t)
        loaded = await store.get(t.trigger_id)
        assert loaded is not None
        assert loaded.name == "watch_docs"
        assert loaded.condition.type == TriggerType.TEMPORAL

    async def test_get_nonexistent_returns_none(self, store: TriggerStore) -> None:
        result = await store.get("nonexistent-id")
        assert result is None

    async def test_upsert_updates_existing(self, store: TriggerStore) -> None:
        t = _make_trigger("original")
        await store.save(t)
        t.name = "updated"
        await store.save(t)
        loaded = await store.get(t.trigger_id)
        assert loaded is not None
        assert loaded.name == "updated"

    async def test_list_all(self, store: TriggerStore) -> None:
        t1 = _make_trigger("t1")
        t2 = _make_trigger("t2")
        await store.save(t1)
        await store.save(t2)
        all_triggers = await store.list_all()
        ids = [t.trigger_id for t in all_triggers]
        assert t1.trigger_id in ids
        assert t2.trigger_id in ids

    async def test_load_active_filters_state(self, store: TriggerStore) -> None:
        active = _make_trigger("active", TriggerState.ACTIVE)
        inactive = _make_trigger("inactive", TriggerState.INACTIVE)
        inactive.enabled = False
        await store.save(active)
        await store.save(inactive)
        loaded = await store.load_active()
        ids = [t.trigger_id for t in loaded]
        assert active.trigger_id in ids
        assert inactive.trigger_id not in ids

    async def test_load_active_includes_watching(self, store: TriggerStore) -> None:
        watching = _make_trigger("watching", TriggerState.WATCHING)
        watching.enabled = True
        await store.save(watching)
        loaded = await store.load_active()
        assert any(t.trigger_id == watching.trigger_id for t in loaded)

    async def test_list_by_state(self, store: TriggerStore) -> None:
        t_active = _make_trigger("a", TriggerState.ACTIVE)
        t_failed = _make_trigger("f", TriggerState.FAILED)
        await store.save(t_active)
        await store.save(t_failed)
        active_list = await store.list_by_state(TriggerState.ACTIVE)
        assert any(t.trigger_id == t_active.trigger_id for t in active_list)
        assert not any(t.trigger_id == t_failed.trigger_id for t in active_list)

    async def test_update_state(self, store: TriggerStore) -> None:
        t = _make_trigger("state_test", TriggerState.ACTIVE)
        await store.save(t)
        await store.update_state(t.trigger_id, TriggerState.FIRED)
        loaded = await store.get(t.trigger_id)
        assert loaded is not None
        assert loaded.state == TriggerState.FIRED

    async def test_delete_existing(self, store: TriggerStore) -> None:
        t = _make_trigger("to_delete")
        await store.save(t)
        deleted = await store.delete(t.trigger_id)
        assert deleted is True
        assert await store.get(t.trigger_id) is None

    async def test_delete_nonexistent_returns_false(self, store: TriggerStore) -> None:
        deleted = await store.delete("nonexistent")
        assert deleted is False

    async def test_purge_expired(self, store: TriggerStore) -> None:
        import time

        t_expired = _make_trigger("expired")
        t_expired.expires_at = time.time() - 1  # already expired
        t_permanent = _make_trigger("permanent")
        await store.save(t_expired)
        await store.save(t_permanent)
        count = await store.purge_expired()
        assert count == 1
        assert await store.get(t_expired.trigger_id) is None
        assert await store.get(t_permanent.trigger_id) is not None

    async def test_health_persisted(self, store: TriggerStore) -> None:
        t = _make_trigger("health_test")
        t.health.record_fire(latency_ms=42.0)
        t.health.record_fail("some error")
        await store.save(t)
        loaded = await store.get(t.trigger_id)
        assert loaded is not None
        assert loaded.health.fire_count == 1
        assert loaded.health.fail_count == 1
        assert loaded.health.last_error == "some error"
