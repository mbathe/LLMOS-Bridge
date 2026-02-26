"""Unit tests — triggers/daemon.py (TriggerDaemon)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llmos_bridge.events.bus import NullEventBus
from llmos_bridge.triggers.daemon import TriggerDaemon
from llmos_bridge.triggers.models import (
    TriggerCondition,
    TriggerDefinition,
    TriggerState,
    TriggerType,
)
from llmos_bridge.triggers.store import TriggerStore


def _make_interval_trigger(name: str = "test") -> TriggerDefinition:
    return TriggerDefinition(
        name=name,
        condition=TriggerCondition(TriggerType.TEMPORAL, {"interval_seconds": 3600}),
        plan_template={"protocol_version": "2.0", "actions": []},
        enabled=True,
    )


@pytest.fixture
async def daemon(tmp_path: Path) -> TriggerDaemon:
    store = TriggerStore(tmp_path / "triggers.db")
    await store.init()
    d = TriggerDaemon(store=store, event_bus=NullEventBus())
    await d.start()
    yield d
    await d.stop()
    await store.close()


@pytest.mark.unit
class TestTriggerDaemonLifecycle:
    async def test_start_and_stop(self, tmp_path: Path) -> None:
        store = TriggerStore(tmp_path / "t.db")
        await store.init()
        d = TriggerDaemon(store=store, event_bus=NullEventBus())
        await d.start()
        assert d._started is True
        await d.stop()
        assert d._started is False
        await store.close()

    async def test_double_start_is_idempotent(self, tmp_path: Path) -> None:
        store = TriggerStore(tmp_path / "t.db")
        await store.init()
        d = TriggerDaemon(store=store, event_bus=NullEventBus())
        await d.start()
        await d.start()  # should not raise or create duplicate tasks
        await d.stop()
        await store.close()


@pytest.mark.unit
class TestTriggerDaemonRegister:
    async def test_register_creates_trigger(self, daemon: TriggerDaemon) -> None:
        t = _make_interval_trigger("my_watch")
        registered = await daemon.register(t)
        assert registered.trigger_id == t.trigger_id
        assert registered.state in (TriggerState.ACTIVE,)

    async def test_register_disabled_stays_inactive(self, daemon: TriggerDaemon) -> None:
        t = _make_interval_trigger()
        t.enabled = False
        registered = await daemon.register(t)
        assert registered.state == TriggerState.REGISTERED

    async def test_register_persists_to_store(self, daemon: TriggerDaemon) -> None:
        t = _make_interval_trigger("persistent")
        await daemon.register(t)
        from_store = await daemon._store.get(t.trigger_id)
        assert from_store is not None
        assert from_store.name == "persistent"

    async def test_register_chain_depth_exceeded_raises(self, daemon: TriggerDaemon) -> None:
        t = _make_interval_trigger()
        t.chain_depth = 10
        t.max_chain_depth = 5
        with pytest.raises(ValueError, match="chain depth"):
            await daemon.register(t)


@pytest.mark.unit
class TestTriggerDaemonActivateDeactivate:
    async def test_activate_arms_watcher(self, daemon: TriggerDaemon) -> None:
        t = _make_interval_trigger()
        t.enabled = False
        await daemon.register(t)
        await daemon.activate(t.trigger_id)
        assert t.trigger_id in daemon._watchers

    async def test_deactivate_disarms_watcher(self, daemon: TriggerDaemon) -> None:
        t = _make_interval_trigger()
        await daemon.register(t)
        assert t.trigger_id in daemon._watchers
        await daemon.deactivate(t.trigger_id)
        assert t.trigger_id not in daemon._watchers

    async def test_activate_unknown_raises(self, daemon: TriggerDaemon) -> None:
        with pytest.raises(KeyError):
            await daemon.activate("nonexistent-id")

    async def test_deactivate_unknown_raises(self, daemon: TriggerDaemon) -> None:
        with pytest.raises(KeyError):
            await daemon.deactivate("nonexistent-id")


@pytest.mark.unit
class TestTriggerDaemonDelete:
    async def test_delete_removes_trigger(self, daemon: TriggerDaemon) -> None:
        t = _make_interval_trigger()
        await daemon.register(t)
        deleted = await daemon.delete(t.trigger_id)
        assert deleted is True
        assert await daemon.get(t.trigger_id) is None

    async def test_delete_nonexistent_returns_false(self, daemon: TriggerDaemon) -> None:
        deleted = await daemon.delete("nonexistent")
        assert deleted is False


@pytest.mark.unit
class TestTriggerDaemonList:
    async def test_list_all(self, daemon: TriggerDaemon) -> None:
        t1 = _make_interval_trigger("a")
        t2 = _make_interval_trigger("b")
        await daemon.register(t1)
        await daemon.register(t2)
        all_triggers = await daemon.list_all()
        ids = [t.trigger_id for t in all_triggers]
        assert t1.trigger_id in ids
        assert t2.trigger_id in ids

    async def test_list_active(self, daemon: TriggerDaemon) -> None:
        t = _make_interval_trigger("active_one")
        await daemon.register(t)
        active = await daemon.list_active()
        assert any(x.trigger_id == t.trigger_id for x in active)


@pytest.mark.unit
class TestTriggerDaemonFireCallback:
    async def test_fire_enqueues_to_scheduler(self, daemon: TriggerDaemon) -> None:
        t = _make_interval_trigger("fire_test")
        await daemon.register(t)
        t.state = TriggerState.ACTIVE

        enqueued: list = []
        original_enqueue = daemon._scheduler.enqueue

        async def mock_enqueue(trigger, fire_event):
            enqueued.append((trigger, fire_event))

        daemon._scheduler.enqueue = mock_enqueue
        await daemon._on_watcher_fire(t.trigger_id, "temporal.interval", {"interval_seconds": 3600})
        assert len(enqueued) == 1
        assert enqueued[0][0].trigger_id == t.trigger_id

    async def test_fire_unknown_trigger_is_noop(self, daemon: TriggerDaemon) -> None:
        # Should not raise
        await daemon._on_watcher_fire("nonexistent-id", "some.event", {})

    async def test_fire_throttled_when_cannot_fire(self, daemon: TriggerDaemon) -> None:
        import time
        t = _make_interval_trigger("throttle_test")
        t.state = TriggerState.ACTIVE
        t.min_interval_seconds = 3600.0
        t.health.last_fired_at = time.time()  # just fired
        await daemon.register(t)

        enqueued: list = []
        daemon._scheduler.enqueue = AsyncMock(side_effect=lambda *a, **k: enqueued.append(a))
        await daemon._on_watcher_fire(t.trigger_id, "temporal.interval", {})
        assert len(enqueued) == 0  # throttled


@pytest.mark.unit
class TestTriggerDaemonBuildPlan:
    async def test_build_plan_injects_plan_id(self, daemon: TriggerDaemon) -> None:
        from llmos_bridge.triggers.models import TriggerFireEvent
        t = _make_interval_trigger()
        t.plan_template = {"protocol_version": "2.0", "actions": []}
        fire_event = TriggerFireEvent(
            trigger_id=t.trigger_id,
            trigger_name=t.name,
            event_type="temporal.interval",
            payload={},
            plan_id="test_plan_123",
        )
        plan = daemon._build_plan(t, fire_event, "test_plan_123")
        assert plan["plan_id"] == "test_plan_123"
        assert plan["execution_mode"] == "reactive"
        assert plan["metadata"]["trigger_id"] == t.trigger_id
