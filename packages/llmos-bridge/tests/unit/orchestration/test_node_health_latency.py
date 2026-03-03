"""Unit tests — NodeHealthMonitor latency tracking (Phase 4).

Tests cover:
- Latency recording on heartbeat
- Latency in emitted events
- Latency stored on node objects
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from llmos_bridge.orchestration.node_health import NodeHealthMonitor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_bus() -> MagicMock:
    bus = MagicMock()
    bus.emit = AsyncMock()
    return bus


def _make_mock_remote_node(
    node_id: str = "remote-1",
    available: bool = True,
    heartbeat_delay: float = 0.0,
) -> MagicMock:
    from llmos_bridge.orchestration.remote_node import RemoteNode

    node = MagicMock(spec=RemoteNode)
    type(node).node_id = PropertyMock(return_value=node_id)
    node.is_available.return_value = available
    node._last_latency_ms = None  # Will be set by health monitor

    async def slow_heartbeat() -> dict:
        if heartbeat_delay:
            await asyncio.sleep(heartbeat_delay)
        return {"status": "ok"}

    node.heartbeat = AsyncMock(side_effect=slow_heartbeat)
    return node


def _make_mock_registry(remotes: list[MagicMock]) -> MagicMock:
    registry = MagicMock()
    registry.get_remote_nodes.return_value = remotes
    registry.get_node.side_effect = lambda nid: next(
        (n for n in remotes if n.node_id == nid), None
    )
    return registry


# ---------------------------------------------------------------------------
# Latency recording
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLatencyRecording:
    @pytest.mark.asyncio
    async def test_latency_stored_on_node(self) -> None:
        """Heartbeat should set _last_latency_ms on the node."""
        node = _make_mock_remote_node("remote-1", available=True)
        registry = _make_mock_registry([node])
        bus = _make_mock_bus()
        monitor = NodeHealthMonitor(registry, bus, interval=1.0)

        await monitor._check_single_node(node)

        assert hasattr(node, "_last_latency_ms")
        assert node._last_latency_ms >= 0

    @pytest.mark.asyncio
    async def test_latency_in_emitted_event(self) -> None:
        """Emitted events should include latency_ms."""
        node = _make_mock_remote_node("remote-1", available=True)
        registry = _make_mock_registry([node])
        bus = _make_mock_bus()
        monitor = NodeHealthMonitor(registry, bus, interval=1.0)

        await monitor._check_single_node(node)

        bus.emit.assert_awaited_once()
        event_data = bus.emit.call_args[0][1]
        assert "latency_ms" in event_data
        assert event_data["latency_ms"] >= 0

    @pytest.mark.asyncio
    async def test_latency_reflects_heartbeat_duration(self) -> None:
        """Latency should reflect actual heartbeat call time."""
        node = _make_mock_remote_node(
            "remote-1", available=True, heartbeat_delay=0.05,
        )
        registry = _make_mock_registry([node])
        bus = _make_mock_bus()
        monitor = NodeHealthMonitor(registry, bus, interval=1.0)

        await monitor._check_single_node(node)

        # At least 50ms delay.
        assert node._last_latency_ms >= 40  # Allow some tolerance

    @pytest.mark.asyncio
    async def test_latency_on_heartbeat_failure(self) -> None:
        """Even if heartbeat fails, latency should be recorded."""
        node = _make_mock_remote_node("remote-1")
        node.heartbeat = AsyncMock(side_effect=Exception("timeout"))
        node.is_available.return_value = False
        registry = _make_mock_registry([node])
        bus = _make_mock_bus()
        monitor = NodeHealthMonitor(registry, bus, interval=1.0)

        await monitor._check_single_node(node)

        assert node._last_latency_ms >= 0
        event_data = bus.emit.call_args[0][1]
        assert event_data["latency_ms"] >= 0
        assert event_data["available"] is False

    @pytest.mark.asyncio
    async def test_latency_on_state_transition(self) -> None:
        """Latency should be included on state transition events."""
        node = _make_mock_remote_node("remote-1", available=True)
        registry = _make_mock_registry([node])
        bus = _make_mock_bus()
        monitor = NodeHealthMonitor(registry, bus, interval=1.0)

        # First check: initial state.
        await monitor._check_single_node(node)
        event1 = bus.emit.call_args[0][1]
        assert event1["event"] == "node_healthy"
        assert "latency_ms" in event1

        # Node goes down.
        node.heartbeat = AsyncMock(side_effect=Exception("fail"))
        node.is_available.return_value = False
        await monitor._check_single_node(node)
        event2 = bus.emit.call_args[0][1]
        assert event2["event"] == "node_unhealthy"
        assert "latency_ms" in event2

    @pytest.mark.asyncio
    async def test_concurrent_latency_tracking(self) -> None:
        """Multiple nodes should each get their own latency."""
        node1 = _make_mock_remote_node("n1", available=True)
        node2 = _make_mock_remote_node("n2", available=True, heartbeat_delay=0.02)
        registry = _make_mock_registry([node1, node2])
        bus = _make_mock_bus()
        monitor = NodeHealthMonitor(registry, bus, interval=1.0)

        await monitor._check_all_nodes()

        assert node1._last_latency_ms >= 0
        assert node2._last_latency_ms >= 0
        # node2 should have higher latency due to delay.
        assert node2._last_latency_ms >= node1._last_latency_ms

    @pytest.mark.asyncio
    async def test_check_node_returns_health(self) -> None:
        """check_node() should return health data."""
        node = _make_mock_remote_node("remote-1", available=True)
        registry = _make_mock_registry([node])
        bus = _make_mock_bus()
        monitor = NodeHealthMonitor(registry, bus, interval=1.0)

        health = await monitor.check_node("remote-1")
        assert health == {"status": "ok"}
