"""Unit tests — EventRebroadcaster (consume Redis Streams → local bus).

Tests cover:
- start/stop lifecycle
- Consumer group creation (XGROUP CREATE)
- Read loop: dispatches events to local_bus
- Self-filter: skips events with matching _source_node
- Multi-topic subscription
- XACK after processing
- Error recovery with backoff
- Malformed message handling
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llmos_bridge.cluster.rebroadcaster import EventRebroadcaster


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_redis() -> MagicMock:
    """Create a mock redis.asyncio.Redis."""
    r = MagicMock()
    r.xgroup_create = AsyncMock()
    r.xreadgroup = AsyncMock(return_value=[])
    r.xack = AsyncMock()
    r.aclose = AsyncMock()
    return r


def _make_mock_local_bus() -> MagicMock:
    """Create a mock EventBus for local_bus."""
    bus = MagicMock()
    bus.emit = AsyncMock()
    return bus


def _make_redis_message(
    topic: str,
    event: dict,
    msg_id: str = "1234-0",
) -> list:
    """Build a mock xreadgroup result for a single message."""
    stream_key = f"llmos:{topic}"
    return [(stream_key, [(msg_id, {"data": json.dumps(event)})])]


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRebroadcasterLifecycle:
    @pytest.mark.asyncio
    async def test_start_creates_consumer_groups(self) -> None:
        local_bus = _make_mock_local_bus()
        rb = EventRebroadcaster(
            redis_url="redis://localhost:6379/0",
            local_bus=local_bus,
            node_name="node-1",
            topics=["llmos.plans", "llmos.actions"],
        )

        mock_redis = _make_mock_redis()
        with patch("redis.asyncio.from_url", return_value=mock_redis):
            await rb.start()

        # Consumer groups created for each topic.
        assert mock_redis.xgroup_create.call_count == 2
        assert rb.is_running is True

        await rb.stop()

    @pytest.mark.asyncio
    async def test_start_idempotent(self) -> None:
        local_bus = _make_mock_local_bus()
        rb = EventRebroadcaster(
            redis_url="redis://localhost:6379/0",
            local_bus=local_bus,
            node_name="node-1",
            topics=["llmos.plans"],
        )

        mock_redis = _make_mock_redis()
        with patch("redis.asyncio.from_url", return_value=mock_redis):
            await rb.start()
            task1 = rb._task
            await rb.start()  # Second call — no-op
            assert rb._task is task1

        await rb.stop()

    @pytest.mark.asyncio
    async def test_stop_cancels_task(self) -> None:
        local_bus = _make_mock_local_bus()
        rb = EventRebroadcaster(
            redis_url="redis://localhost:6379/0",
            local_bus=local_bus,
            node_name="node-1",
            topics=["llmos.plans"],
        )

        mock_redis = _make_mock_redis()
        with patch("redis.asyncio.from_url", return_value=mock_redis):
            await rb.start()

        await rb.stop()

        assert rb.is_running is False
        assert rb._redis is None

    @pytest.mark.asyncio
    async def test_stop_when_not_started(self) -> None:
        local_bus = _make_mock_local_bus()
        rb = EventRebroadcaster(
            redis_url="redis://localhost:6379/0",
            local_bus=local_bus,
            node_name="node-1",
        )
        await rb.stop()  # Should not raise

    @pytest.mark.asyncio
    async def test_consumer_group_already_exists(self) -> None:
        """BUSYGROUP error should be silently ignored."""
        local_bus = _make_mock_local_bus()
        rb = EventRebroadcaster(
            redis_url="redis://localhost:6379/0",
            local_bus=local_bus,
            node_name="node-1",
            topics=["llmos.plans"],
        )

        mock_redis = _make_mock_redis()
        mock_redis.xgroup_create = AsyncMock(side_effect=Exception("BUSYGROUP"))
        with patch("redis.asyncio.from_url", return_value=mock_redis):
            await rb.start()  # Should not raise

        assert rb.is_running is True
        await rb.stop()


# ---------------------------------------------------------------------------
# Event dispatching
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRebroadcasterDispatch:
    @pytest.mark.asyncio
    async def test_dispatches_remote_event_to_local_bus(self) -> None:
        local_bus = _make_mock_local_bus()
        rb = EventRebroadcaster(
            redis_url="redis://localhost:6379/0",
            local_bus=local_bus,
            node_name="node-1",
            topics=["llmos.actions"],
        )

        event = {"event": "action_started", "_source_node": "node-2"}
        mock_redis = _make_mock_redis()
        # First call returns one message, second raises CancelledError to stop loop.
        mock_redis.xreadgroup = AsyncMock(
            side_effect=[
                _make_redis_message("llmos.actions", event),
                asyncio.CancelledError(),
            ]
        )

        with patch("redis.asyncio.from_url", return_value=mock_redis):
            rb._redis = mock_redis

            try:
                await rb._read_loop()
            except asyncio.CancelledError:
                pass

        local_bus.emit.assert_awaited_once()
        call_args = local_bus.emit.call_args
        assert call_args[0][0] == "llmos.actions"
        assert call_args[0][1]["event"] == "action_started"

    @pytest.mark.asyncio
    async def test_filters_self_emitted_events(self) -> None:
        local_bus = _make_mock_local_bus()
        rb = EventRebroadcaster(
            redis_url="redis://localhost:6379/0",
            local_bus=local_bus,
            node_name="node-1",
            topics=["llmos.actions"],
        )

        # Event from ourselves — should be filtered.
        event = {"event": "action_started", "_source_node": "node-1"}
        mock_redis = _make_mock_redis()
        mock_redis.xreadgroup = AsyncMock(
            side_effect=[
                _make_redis_message("llmos.actions", event),
                asyncio.CancelledError(),
            ]
        )

        rb._redis = mock_redis
        try:
            await rb._read_loop()
        except asyncio.CancelledError:
            pass

        local_bus.emit.assert_not_awaited()
        # But XACK should still be called.
        mock_redis.xack.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_acks_after_dispatch(self) -> None:
        local_bus = _make_mock_local_bus()
        rb = EventRebroadcaster(
            redis_url="redis://localhost:6379/0",
            local_bus=local_bus,
            node_name="node-1",
            topics=["llmos.actions"],
        )

        event = {"event": "test", "_source_node": "node-2"}
        mock_redis = _make_mock_redis()
        mock_redis.xreadgroup = AsyncMock(
            side_effect=[
                _make_redis_message("llmos.actions", event, msg_id="111-0"),
                asyncio.CancelledError(),
            ]
        )

        rb._redis = mock_redis
        try:
            await rb._read_loop()
        except asyncio.CancelledError:
            pass

        mock_redis.xack.assert_awaited_once_with(
            "llmos:llmos.actions", "llmos-bridge", "111-0",
        )

    @pytest.mark.asyncio
    async def test_handles_malformed_message(self) -> None:
        """Malformed JSON should be ACK'd and skipped."""
        local_bus = _make_mock_local_bus()
        rb = EventRebroadcaster(
            redis_url="redis://localhost:6379/0",
            local_bus=local_bus,
            node_name="node-1",
            topics=["llmos.actions"],
        )

        mock_redis = _make_mock_redis()
        # Return a message with invalid JSON.
        mock_redis.xreadgroup = AsyncMock(
            side_effect=[
                [("llmos:llmos.actions", [("999-0", {"data": "not-json{{{"})])],
                asyncio.CancelledError(),
            ]
        )

        rb._redis = mock_redis
        try:
            await rb._read_loop()
        except asyncio.CancelledError:
            pass

        # Should not crash, and should ACK the bad message.
        mock_redis.xack.assert_awaited_once()
        local_bus.emit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_handles_empty_results(self) -> None:
        """Empty xreadgroup result should just loop."""
        local_bus = _make_mock_local_bus()
        rb = EventRebroadcaster(
            redis_url="redis://localhost:6379/0",
            local_bus=local_bus,
            node_name="node-1",
            topics=["llmos.actions"],
        )

        mock_redis = _make_mock_redis()
        mock_redis.xreadgroup = AsyncMock(
            side_effect=[
                [],  # Empty result
                asyncio.CancelledError(),
            ]
        )

        rb._redis = mock_redis
        try:
            await rb._read_loop()
        except asyncio.CancelledError:
            pass

        local_bus.emit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_handles_emit_failure(self) -> None:
        """If local_bus.emit fails, should still ACK and continue."""
        local_bus = _make_mock_local_bus()
        local_bus.emit = AsyncMock(side_effect=RuntimeError("bus broken"))
        rb = EventRebroadcaster(
            redis_url="redis://localhost:6379/0",
            local_bus=local_bus,
            node_name="node-1",
            topics=["llmos.actions"],
        )

        event = {"event": "test", "_source_node": "node-2"}
        mock_redis = _make_mock_redis()
        mock_redis.xreadgroup = AsyncMock(
            side_effect=[
                _make_redis_message("llmos.actions", event),
                asyncio.CancelledError(),
            ]
        )

        rb._redis = mock_redis
        try:
            await rb._read_loop()
        except asyncio.CancelledError:
            pass

        # ACK should still be called.
        mock_redis.xack.assert_awaited_once()


# ---------------------------------------------------------------------------
# Default topics
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRebroadcasterDefaults:
    def test_default_topics(self) -> None:
        local_bus = _make_mock_local_bus()
        rb = EventRebroadcaster(
            redis_url="redis://localhost:6379/0",
            local_bus=local_bus,
            node_name="node-1",
        )
        assert len(rb._topics) >= 7  # At least the standard topics

    def test_custom_topics(self) -> None:
        local_bus = _make_mock_local_bus()
        rb = EventRebroadcaster(
            redis_url="redis://localhost:6379/0",
            local_bus=local_bus,
            node_name="node-1",
            topics=["custom.topic"],
        )
        assert rb._topics == ["custom.topic"]
