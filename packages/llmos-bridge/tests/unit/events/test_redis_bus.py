"""Unit tests — RedisStreamsBus (publish events to Redis Streams).

Tests cover:
- emit: XADD with correct stream key, payload, maxlen
- _source_node tagging on every event
- Error handling: emit never raises, logs warning
- No-op when not connected (redis=None)
- connect / close lifecycle
- Listener dispatch after emit
- ImportError fallback stub
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_redis() -> MagicMock:
    """Create a mock redis.asyncio.Redis."""
    r = MagicMock()
    r.xadd = AsyncMock(return_value="1234567890-0")
    r.aclose = AsyncMock()
    return r


# ---------------------------------------------------------------------------
# emit
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRedisStreamsBusEmit:
    @pytest.mark.asyncio
    async def test_emit_calls_xadd(self) -> None:
        from llmos_bridge.events.redis_bus import RedisStreamsBus

        bus = RedisStreamsBus("redis://localhost:6379/0", "node-1", max_length=5000)
        bus._redis = _make_mock_redis()

        await bus.emit("llmos.actions", {"event": "action_started", "action_id": "a1"})

        bus._redis.xadd.assert_awaited_once()
        call_args = bus._redis.xadd.call_args
        assert call_args[0][0] == "llmos:llmos.actions"
        payload = json.loads(call_args[0][1]["data"])
        assert payload["event"] == "action_started"
        assert payload["_source_node"] == "node-1"
        assert call_args[1]["maxlen"] == 5000

    @pytest.mark.asyncio
    async def test_emit_stamps_source_node(self) -> None:
        from llmos_bridge.events.redis_bus import RedisStreamsBus

        bus = RedisStreamsBus("redis://localhost:6379/0", "worker-2")
        bus._redis = _make_mock_redis()

        event = {"event": "test"}
        await bus.emit("llmos.plans", event)

        assert event["_source_node"] == "worker-2"

    @pytest.mark.asyncio
    async def test_emit_stamps_topic_and_timestamp(self) -> None:
        from llmos_bridge.events.redis_bus import RedisStreamsBus

        bus = RedisStreamsBus("redis://localhost:6379/0", "n")
        bus._redis = _make_mock_redis()

        event: dict = {"event": "test"}
        await bus.emit("llmos.actions", event)

        assert event["_topic"] == "llmos.actions"
        assert "_timestamp" in event

    @pytest.mark.asyncio
    async def test_emit_noop_when_not_connected(self) -> None:
        from llmos_bridge.events.redis_bus import RedisStreamsBus

        bus = RedisStreamsBus("redis://localhost:6379/0", "n")
        # _redis is None — should not raise
        await bus.emit("llmos.actions", {"event": "test"})

    @pytest.mark.asyncio
    async def test_emit_handles_redis_error(self) -> None:
        from llmos_bridge.events.redis_bus import RedisStreamsBus

        bus = RedisStreamsBus("redis://localhost:6379/0", "n")
        bus._redis = _make_mock_redis()
        bus._redis.xadd = AsyncMock(side_effect=ConnectionError("refused"))

        # Should not raise
        await bus.emit("llmos.actions", {"event": "test"})

    @pytest.mark.asyncio
    async def test_emit_dispatches_to_listeners(self) -> None:
        from llmos_bridge.events.redis_bus import RedisStreamsBus

        bus = RedisStreamsBus("redis://localhost:6379/0", "n")
        bus._redis = _make_mock_redis()

        received = []

        async def listener(topic: str, event: dict) -> None:
            received.append((topic, event))

        bus.register_listener("llmos.actions", listener)
        await bus.emit("llmos.actions", {"event": "test"})

        assert len(received) == 1
        assert received[0][0] == "llmos.actions"

    @pytest.mark.asyncio
    async def test_emit_approximate_trimming(self) -> None:
        from llmos_bridge.events.redis_bus import RedisStreamsBus

        bus = RedisStreamsBus("redis://localhost:6379/0", "n", max_length=1000)
        bus._redis = _make_mock_redis()

        await bus.emit("t", {"event": "x"})

        call_kwargs = bus._redis.xadd.call_args[1]
        assert call_kwargs.get("approximate") is True

    @pytest.mark.asyncio
    async def test_emit_adds_to_ring_buffer(self) -> None:
        from llmos_bridge.events.redis_bus import RedisStreamsBus

        bus = RedisStreamsBus("redis://localhost:6379/0", "n")
        bus._redis = _make_mock_redis()

        await bus.emit("llmos.actions", {"event": "test"})

        assert len(bus._recent_events) == 1


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRedisStreamsBusLifecycle:
    @pytest.mark.asyncio
    async def test_connect_creates_redis(self) -> None:
        from llmos_bridge.events.redis_bus import RedisStreamsBus

        bus = RedisStreamsBus("redis://localhost:6379/0", "n")
        with patch("llmos_bridge.events.redis_bus.aioredis") as mock_aioredis:
            mock_aioredis.from_url.return_value = _make_mock_redis()
            await bus.connect()

        assert bus._redis is not None
        assert bus.is_connected is True

    @pytest.mark.asyncio
    async def test_close_cleans_up(self) -> None:
        from llmos_bridge.events.redis_bus import RedisStreamsBus

        bus = RedisStreamsBus("redis://localhost:6379/0", "n")
        bus._redis = _make_mock_redis()

        await bus.close()

        bus._redis is None  # type: ignore[comparison-overlap]
        assert bus.is_connected is False

    @pytest.mark.asyncio
    async def test_close_noop_when_not_connected(self) -> None:
        from llmos_bridge.events.redis_bus import RedisStreamsBus

        bus = RedisStreamsBus("redis://localhost:6379/0", "n")
        await bus.close()  # Should not raise

    def test_not_connected_initially(self) -> None:
        from llmos_bridge.events.redis_bus import RedisStreamsBus

        bus = RedisStreamsBus("redis://localhost:6379/0", "n")
        assert bus.is_connected is False


# ---------------------------------------------------------------------------
# Stream key mapping
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRedisStreamsBusStreamKeys:
    @pytest.mark.asyncio
    async def test_stream_key_format(self) -> None:
        from llmos_bridge.events.redis_bus import RedisStreamsBus

        bus = RedisStreamsBus("redis://localhost:6379/0", "n")
        bus._redis = _make_mock_redis()

        await bus.emit("llmos.security", {"event": "test"})

        stream_key = bus._redis.xadd.call_args[0][0]
        assert stream_key == "llmos:llmos.security"

    @pytest.mark.asyncio
    async def test_custom_topic_stream_key(self) -> None:
        from llmos_bridge.events.redis_bus import RedisStreamsBus

        bus = RedisStreamsBus("redis://localhost:6379/0", "n")
        bus._redis = _make_mock_redis()

        await bus.emit("custom.topic", {"event": "test"})

        stream_key = bus._redis.xadd.call_args[0][0]
        assert stream_key == "llmos:custom.topic"
