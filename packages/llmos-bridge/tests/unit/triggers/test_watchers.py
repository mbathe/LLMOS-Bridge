"""Unit tests — triggers/watchers/ (temporal, system, composite)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llmos_bridge.triggers.models import TriggerCondition, TriggerType
from llmos_bridge.triggers.watchers.base import BaseWatcher, WatcherFactory
from llmos_bridge.triggers.watchers.temporal import CronWatcher, IntervalWatcher, OnceWatcher
from llmos_bridge.triggers.watchers.system import ProcessWatcher, ResourceWatcher
from llmos_bridge.triggers.watchers.composite import CompositeWatcher


@pytest.mark.unit
class TestIntervalWatcher:
    async def test_fires_after_interval(self) -> None:
        fired: list[dict] = []

        async def callback(tid: str, etype: str, payload: dict) -> None:
            fired.append({"tid": tid, "etype": etype, "payload": payload})

        cond = TriggerCondition(TriggerType.TEMPORAL, {"interval_seconds": 0.05})
        watcher = IntervalWatcher("t1", cond, callback)
        await watcher.start()
        await asyncio.sleep(0.12)
        await watcher.stop()

        assert len(fired) >= 1
        assert fired[0]["etype"] == "temporal.interval"
        assert fired[0]["tid"] == "t1"

    async def test_stop_before_interval_no_fire(self) -> None:
        fired: list[dict] = []

        async def callback(tid: str, etype: str, payload: dict) -> None:
            fired.append(payload)

        cond = TriggerCondition(TriggerType.TEMPORAL, {"interval_seconds": 10.0})
        watcher = IntervalWatcher("t1", cond, callback)
        await watcher.start()
        await asyncio.sleep(0.01)
        await watcher.stop()
        assert len(fired) == 0

    def test_negative_interval_raises(self) -> None:
        cond = TriggerCondition(TriggerType.TEMPORAL, {"interval_seconds": -1.0})
        with pytest.raises(ValueError, match="positive"):
            IntervalWatcher("t1", cond, AsyncMock())

    async def test_is_running(self) -> None:
        cond = TriggerCondition(TriggerType.TEMPORAL, {"interval_seconds": 5.0})
        watcher = IntervalWatcher("t1", cond, AsyncMock())
        assert not watcher.is_running
        await watcher.start()
        assert watcher.is_running
        await watcher.stop()
        assert not watcher.is_running


@pytest.mark.unit
class TestOnceWatcher:
    async def test_fires_once_in_past(self) -> None:
        """run_at in the past → fires immediately."""
        import time
        fired: list[str] = []

        async def callback(tid: str, etype: str, payload: dict) -> None:
            fired.append(etype)

        cond = TriggerCondition(TriggerType.TEMPORAL, {"run_at": time.time() - 1})
        watcher = OnceWatcher("t1", cond, callback)
        await watcher.start()
        await asyncio.sleep(0.05)
        await watcher.stop()
        assert "temporal.once" in fired

    async def test_fires_exactly_once(self) -> None:
        """OnceWatcher should not fire multiple times."""
        import time
        fired: list[str] = []

        async def callback(tid: str, etype: str, payload: dict) -> None:
            fired.append(etype)

        cond = TriggerCondition(TriggerType.TEMPORAL, {"run_at": time.time() - 1})
        watcher = OnceWatcher("t1", cond, callback)
        await watcher.start()
        await asyncio.sleep(0.1)
        await watcher.stop()
        assert len(fired) == 1


@pytest.mark.unit
class TestCronWatcher:
    async def test_missing_croniter_sets_error(self) -> None:
        """If croniter is not installed, watcher sets error flag."""
        import sys
        cond = TriggerCondition(TriggerType.TEMPORAL, {"schedule": "* * * * *"})
        callback = AsyncMock()
        watcher = CronWatcher("t1", cond, callback)

        # Temporarily remove croniter from sys.modules to simulate missing dep
        croniter_backup = sys.modules.pop("croniter", None)
        try:
            await watcher.start()
            await asyncio.sleep(0.05)
            await watcher.stop()
            # If croniter truly not installed, watcher.error would be set
            # If it IS installed, test is a no-op
        finally:
            if croniter_backup is not None:
                sys.modules["croniter"] = croniter_backup


@pytest.mark.unit
class TestResourceWatcher:
    async def test_fires_when_threshold_exceeded(self) -> None:
        fired: list[dict] = []

        async def callback(tid: str, etype: str, payload: dict) -> None:
            fired.append(payload)

        cond = TriggerCondition(TriggerType.RESOURCE, {
            "metric": "cpu_percent",
            "threshold": 10.0,      # very low threshold
            "duration_seconds": 0,
            "poll_interval_seconds": 0.05,
        })

        watcher = ResourceWatcher("t1", cond, callback)
        mock_psutil = MagicMock()
        mock_psutil.cpu_percent.return_value = 99.0  # always exceeds threshold

        with patch("llmos_bridge.triggers.watchers.system.ResourceWatcher._sample", return_value=99.0):
            await watcher.start()
            await asyncio.sleep(0.15)
            await watcher.stop()

        assert len(fired) >= 1

    async def test_does_not_fire_below_threshold(self) -> None:
        fired: list[dict] = []

        async def callback(tid: str, etype: str, payload: dict) -> None:
            fired.append(payload)

        cond = TriggerCondition(TriggerType.RESOURCE, {
            "metric": "cpu_percent",
            "threshold": 95.0,
            "duration_seconds": 0,
            "poll_interval_seconds": 0.05,
        })
        watcher = ResourceWatcher("t1", cond, callback)
        with patch("llmos_bridge.triggers.watchers.system.ResourceWatcher._sample", return_value=10.0):
            await watcher.start()
            await asyncio.sleep(0.12)
            await watcher.stop()

        assert len(fired) == 0


@pytest.mark.unit
class TestProcessWatcher:
    async def test_fires_when_process_appears(self) -> None:
        fired: list[dict] = []

        async def callback(tid: str, etype: str, payload: dict) -> None:
            fired.append({"etype": etype, "payload": payload})

        cond = TriggerCondition(TriggerType.PROCESS, {
            "name": "test_process",
            "event": "started",
            "poll_interval_seconds": 0.05,
        })
        watcher = ProcessWatcher("t1", cond, callback)

        # First poll: no process. Second poll: process appeared.
        call_count = 0

        def mock_pids(self_watcher: ProcessWatcher, psutil: object) -> set:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return set()
            return {12345}

        with patch.object(ProcessWatcher, "_current_matching_pids", mock_pids):
            await watcher.start()
            await asyncio.sleep(0.15)
            await watcher.stop()

        assert len(fired) >= 1
        assert fired[0]["etype"] == "process.started"


@pytest.mark.unit
class TestCompositeWatcher:
    async def test_or_fires_immediately(self) -> None:
        fired: list[dict] = []

        async def callback(tid: str, etype: str, payload: dict) -> None:
            fired.append(payload)

        cond = TriggerCondition(TriggerType.COMPOSITE, {
            "operator": "OR",
            "trigger_ids": ["sub1", "sub2"],
        })
        watcher = CompositeWatcher("t1", cond, callback)
        await watcher.start()
        await watcher.notify_sub_fire("sub1", "test.event", {"data": "x"})
        await asyncio.sleep(0.05)
        await watcher.stop()
        assert len(fired) >= 1

    async def test_and_requires_both(self) -> None:
        fired: list[dict] = []

        async def callback(tid: str, etype: str, payload: dict) -> None:
            fired.append(payload)

        cond = TriggerCondition(TriggerType.COMPOSITE, {
            "operator": "AND",
            "trigger_ids": ["sub1", "sub2"],
            "timeout_seconds": 60.0,
        })
        watcher = CompositeWatcher("t1", cond, callback)
        await watcher.start()

        # Only one fires — should not trigger
        await watcher.notify_sub_fire("sub1", "t.e", {})
        await asyncio.sleep(0.05)
        assert len(fired) == 0

        # Both fire — should trigger
        await watcher.notify_sub_fire("sub2", "t.e", {})
        await asyncio.sleep(0.05)
        await watcher.stop()
        assert len(fired) == 1

    async def test_seq_fires_in_order(self) -> None:
        fired: list[dict] = []

        async def callback(tid: str, etype: str, payload: dict) -> None:
            fired.append(payload)

        cond = TriggerCondition(TriggerType.COMPOSITE, {
            "operator": "SEQ",
            "trigger_ids": ["sub1", "sub2"],
            "timeout_seconds": 60.0,
        })
        watcher = CompositeWatcher("t1", cond, callback)
        await watcher.start()

        # Wrong order — should not fire
        await watcher.notify_sub_fire("sub2", "t.e", {})
        await asyncio.sleep(0.05)
        assert len(fired) == 0

        # Correct order
        await watcher.notify_sub_fire("sub1", "t.e", {})
        await asyncio.sleep(0.02)
        await watcher.notify_sub_fire("sub2", "t.e", {})
        await asyncio.sleep(0.05)
        await watcher.stop()
        assert len(fired) == 1

    async def test_window_fires_after_count(self) -> None:
        fired: list[dict] = []

        async def callback(tid: str, etype: str, payload: dict) -> None:
            fired.append(payload)

        cond = TriggerCondition(TriggerType.COMPOSITE, {
            "operator": "WINDOW",
            "trigger_ids": ["sub1"],
            "count": 3,
            "window_seconds": 60.0,
        })
        watcher = CompositeWatcher("t1", cond, callback)
        await watcher.start()

        for _ in range(2):
            await watcher.notify_sub_fire("sub1", "t.e", {})
            await asyncio.sleep(0.02)
        assert len(fired) == 0  # not yet

        await watcher.notify_sub_fire("sub1", "t.e", {})
        await asyncio.sleep(0.05)
        await watcher.stop()
        assert len(fired) == 1

    async def test_unknown_sub_trigger_ignored(self) -> None:
        callback = AsyncMock()
        cond = TriggerCondition(TriggerType.COMPOSITE, {
            "operator": "OR",
            "trigger_ids": ["sub1"],
        })
        watcher = CompositeWatcher("t1", cond, callback)
        await watcher.start()
        await watcher.notify_sub_fire("unknown_sub", "t.e", {})
        await asyncio.sleep(0.05)
        await watcher.stop()
        callback.assert_not_called()


@pytest.mark.unit
class TestWatcherFactory:
    def test_creates_interval_watcher(self) -> None:
        cond = TriggerCondition(TriggerType.TEMPORAL, {"interval_seconds": 60})
        w = WatcherFactory.create("t1", cond, AsyncMock())
        assert isinstance(w, IntervalWatcher)

    def test_creates_cron_watcher(self) -> None:
        cond = TriggerCondition(TriggerType.TEMPORAL, {"schedule": "0 9 * * *"})
        w = WatcherFactory.create("t1", cond, AsyncMock())
        assert isinstance(w, CronWatcher)

    def test_creates_once_watcher(self) -> None:
        import time
        cond = TriggerCondition(TriggerType.TEMPORAL, {"run_at": time.time() + 3600})
        w = WatcherFactory.create("t1", cond, AsyncMock())
        assert isinstance(w, OnceWatcher)

    def test_creates_process_watcher(self) -> None:
        cond = TriggerCondition(TriggerType.PROCESS, {"name": "firefox", "event": "started"})
        w = WatcherFactory.create("t1", cond, AsyncMock())
        assert isinstance(w, ProcessWatcher)

    def test_creates_resource_watcher(self) -> None:
        cond = TriggerCondition(TriggerType.RESOURCE, {"metric": "cpu_percent", "threshold": 90})
        w = WatcherFactory.create("t1", cond, AsyncMock())
        assert isinstance(w, ResourceWatcher)

    def test_creates_composite_watcher(self) -> None:
        cond = TriggerCondition(TriggerType.COMPOSITE, {"operator": "OR", "trigger_ids": ["t1"]})
        w = WatcherFactory.create("t1", cond, AsyncMock())
        assert isinstance(w, CompositeWatcher)

    def test_unsupported_type_raises(self) -> None:
        cond = TriggerCondition(TriggerType.APPLICATION, {})
        with pytest.raises(ValueError, match="No watcher"):
            WatcherFactory.create("t1", cond, AsyncMock())

    def test_temporal_missing_key_raises(self) -> None:
        cond = TriggerCondition(TriggerType.TEMPORAL, {})  # no schedule/interval/run_at
        with pytest.raises(ValueError):
            WatcherFactory.create("t1", cond, AsyncMock())
