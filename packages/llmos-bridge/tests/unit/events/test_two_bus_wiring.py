"""Unit tests — Two-bus architecture (local_bus + redis_bus isolation).

Tests verify:
- FanoutEventBus([local_bus, redis_bus]) broadcasts to both backends
- EventRebroadcaster writes to local_bus ONLY — no infinite loop
- Standalone mode: no Redis involved
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from llmos_bridge.events.bus import FanoutEventBus, LogEventBus, NullEventBus


# ---------------------------------------------------------------------------
# Two-bus emit
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTwoBusEmit:
    @pytest.mark.asyncio
    async def test_fanout_emits_to_both_backends(self) -> None:
        """Emit on full_bus should reach both local_bus and redis_bus."""
        local_bus = MagicMock()
        local_bus.emit = AsyncMock()
        redis_bus = MagicMock()
        redis_bus.emit = AsyncMock()

        full_bus = FanoutEventBus([local_bus, redis_bus])
        await full_bus.emit("llmos.actions", {"event": "test"})

        local_bus.emit.assert_awaited_once()
        redis_bus.emit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_fanout_isolates_failures(self) -> None:
        """If redis_bus fails, local_bus should still receive the event."""
        local_bus = MagicMock()
        local_bus.emit = AsyncMock()
        redis_bus = MagicMock()
        redis_bus.emit = AsyncMock(side_effect=ConnectionError("redis down"))

        full_bus = FanoutEventBus([local_bus, redis_bus])
        # FanoutEventBus uses return_exceptions=True → should not raise.
        await full_bus.emit("llmos.actions", {"event": "test"})

        local_bus.emit.assert_awaited_once()


# ---------------------------------------------------------------------------
# No-loop guarantee
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNoInfiniteLoop:
    @pytest.mark.asyncio
    async def test_rebroadcaster_writes_to_local_only(self) -> None:
        """EventRebroadcaster must write to local_bus, not to full_bus.

        This test verifies the architectural guarantee by checking that
        the rebroadcaster's `_local_bus` is NOT the same as a hypothetical
        full_bus (which includes Redis).
        """
        local_bus = NullEventBus()
        redis_bus = MagicMock()
        redis_bus.emit = AsyncMock()
        full_bus = FanoutEventBus([local_bus, redis_bus])

        # Simulate what server.py does:
        # rebroadcaster receives local_bus, NOT full_bus.
        from llmos_bridge.cluster.rebroadcaster import EventRebroadcaster

        rb = EventRebroadcaster(
            redis_url="redis://localhost:6379/0",
            local_bus=local_bus,  # ← local_bus, not full_bus
            node_name="node-1",
        )

        # Verify the architectural constraint.
        assert rb._local_bus is local_bus
        assert rb._local_bus is not full_bus


# ---------------------------------------------------------------------------
# Standalone mode
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStandaloneMode:
    @pytest.mark.asyncio
    async def test_standalone_no_redis(self) -> None:
        """In standalone mode, event_bus = local_bus (no Redis involved)."""
        # Simulate standalone wiring from server.py.
        from llmos_bridge.events.bus import LogEventBus
        from pathlib import Path
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".ndjson") as f:
            log_bus = LogEventBus(Path(f.name))
            ws_bus = NullEventBus()  # Stand-in for WebSocketEventBus.
            local_bus = FanoutEventBus([log_bus, ws_bus])

            # In standalone, event_bus = local_bus (no Redis layer).
            event_bus = local_bus
            await event_bus.emit("llmos.actions", {"event": "test"})

            # No Redis calls — just verify it works without error.
            assert len(event_bus._recent_events) == 1

    def test_redis_config_disabled_by_default(self) -> None:
        """RedisConfig.enabled defaults to False."""
        from llmos_bridge.config import RedisConfig

        cfg = RedisConfig()
        assert cfg.enabled is False
        assert cfg.url == "redis://localhost:6379/0"
