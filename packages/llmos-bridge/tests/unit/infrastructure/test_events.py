"""Unit tests — EventBus implementations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from llmos_bridge.events.bus import (
    TOPIC_ACTIONS,
    TOPIC_PLANS,
    TOPIC_SECURITY,
    FanoutEventBus,
    LogEventBus,
    NullEventBus,
)


@pytest.mark.unit
class TestNullEventBus:
    async def test_emit_does_not_raise(self) -> None:
        bus = NullEventBus()
        await bus.emit(TOPIC_PLANS, {"event": "plan_started"})

    async def test_emit_multiple_topics(self) -> None:
        bus = NullEventBus()
        for topic in (TOPIC_PLANS, TOPIC_ACTIONS, TOPIC_SECURITY):
            await bus.emit(topic, {"event": "test"})

    async def test_subscribe_raises_not_implemented(self) -> None:
        bus = NullEventBus()
        with pytest.raises(NotImplementedError):
            async for _ in bus.subscribe([TOPIC_PLANS]):
                pass


@pytest.mark.unit
class TestLogEventBus:
    async def test_emit_writes_ndjson_file(self, tmp_path: Path) -> None:
        log_file = tmp_path / "events.ndjson"
        bus = LogEventBus(log_file)
        await bus.emit(TOPIC_ACTIONS, {"event": "action_started", "action_id": "a1"})
        assert log_file.exists()
        lines = log_file.read_text().strip().splitlines()
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["event"] == "action_started"
        assert data["_topic"] == TOPIC_ACTIONS
        assert "_timestamp" in data

    async def test_emit_appends_multiple_events(self, tmp_path: Path) -> None:
        log_file = tmp_path / "events.ndjson"
        bus = LogEventBus(log_file)
        for i in range(3):
            await bus.emit(TOPIC_PLANS, {"event": f"event_{i}"})
        lines = log_file.read_text().strip().splitlines()
        assert len(lines) == 3

    async def test_emit_null_file_no_error(self) -> None:
        bus = LogEventBus(None)
        await bus.emit(TOPIC_ACTIONS, {"event": "test"})

    async def test_emit_creates_parent_dirs(self, tmp_path: Path) -> None:
        log_file = tmp_path / "deep" / "dir" / "events.ndjson"
        bus = LogEventBus(log_file)
        await bus.emit(TOPIC_PLANS, {"event": "test"})
        assert log_file.exists()

    async def test_emit_sync_writes_file(self, tmp_path: Path) -> None:
        log_file = tmp_path / "sync_events.ndjson"
        bus = LogEventBus(log_file)
        bus.emit_sync(TOPIC_SECURITY, {"event": "permission_denied", "action": "delete"})
        assert log_file.exists()
        data = json.loads(log_file.read_text().strip())
        assert data["event"] == "permission_denied"

    async def test_emit_sync_null_file_no_error(self) -> None:
        bus = LogEventBus(None)
        bus.emit_sync(TOPIC_ACTIONS, {"event": "test"})

    async def test_stamp_adds_metadata(self, tmp_path: Path) -> None:
        log_file = tmp_path / "events.ndjson"
        bus = LogEventBus(log_file)
        await bus.emit(TOPIC_ACTIONS, {"event": "test", "extra": "data"})
        data = json.loads(log_file.read_text().strip())
        assert data["_topic"] == TOPIC_ACTIONS
        assert isinstance(data["_timestamp"], float)
        assert data["extra"] == "data"


@pytest.mark.unit
class TestFanoutEventBus:
    async def test_emit_calls_all_backends(self) -> None:
        backend1 = NullEventBus()
        backend2 = NullEventBus()

        b1_emit = AsyncMock()
        b2_emit = AsyncMock()
        backend1.emit = b1_emit
        backend2.emit = b2_emit

        bus = FanoutEventBus([backend1, backend2])
        await bus.emit(TOPIC_PLANS, {"event": "plan_started"})

        b1_emit.assert_called_once()
        b2_emit.assert_called_once()

    async def test_fanout_to_log_file(self, tmp_path: Path) -> None:
        log1 = tmp_path / "bus1.ndjson"
        log2 = tmp_path / "bus2.ndjson"
        bus = FanoutEventBus([LogEventBus(log1), LogEventBus(log2)])
        await bus.emit(TOPIC_ACTIONS, {"event": "test_fanout"})

        assert log1.exists()
        assert log2.exists()
        d1 = json.loads(log1.read_text().strip())
        d2 = json.loads(log2.read_text().strip())
        assert d1["event"] == "test_fanout"
        assert d2["event"] == "test_fanout"

    async def test_fanout_backend_failure_does_not_propagate(self) -> None:
        failing_backend = NullEventBus()
        failing_backend.emit = AsyncMock(side_effect=RuntimeError("Backend error"))
        good_backend = NullEventBus()
        good_backend.emit = AsyncMock()

        bus = FanoutEventBus([failing_backend, good_backend])
        # Should not raise despite backend failure (return_exceptions=True)
        await bus.emit(TOPIC_PLANS, {"event": "test"})
        good_backend.emit.assert_called_once()
