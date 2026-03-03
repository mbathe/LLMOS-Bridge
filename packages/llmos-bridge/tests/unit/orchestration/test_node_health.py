"""Unit tests — NodeHealthMonitor (periodic heartbeat for remote nodes).

Tests cover:
- start/stop lifecycle
- _check_all_nodes: filters for RemoteNode only
- _check_single_node: state transitions and event emission
- check_node: manual heartbeat trigger
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llmos_bridge.orchestration.node_health import NodeHealthMonitor
from llmos_bridge.orchestration.nodes import BaseNode, LocalNode, NodeRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_registry() -> MagicMock:
    """Return a mock ModuleRegistry."""
    registry = MagicMock()
    module = MagicMock()
    module.execute = AsyncMock(return_value={})
    registry.get.return_value = module
    return registry


def _make_remote_node(node_id: str, available: bool = True) -> MagicMock:
    """Create a mock RemoteNode."""
    # Use a real spec-like mock.
    from llmos_bridge.orchestration.remote_node import RemoteNode

    node = MagicMock(spec=RemoteNode)
    node.node_id = node_id
    node.is_available.return_value = available
    node.heartbeat = AsyncMock(return_value={"status": "ok" if available else "error"})
    node._location = ""
    node._capabilities = []
    node._last_heartbeat = None
    node._base_url = f"http://{node_id}:40000"
    return node


def _make_event_bus() -> MagicMock:
    bus = MagicMock()
    bus.emit = AsyncMock()
    return bus


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNodeHealthMonitorLifecycle:
    @pytest.mark.asyncio
    async def test_start_stop(self) -> None:
        registry = NodeRegistry(LocalNode(_make_mock_registry()))
        bus = _make_event_bus()
        monitor = NodeHealthMonitor(registry, bus, interval=0.1)

        await monitor.start()
        assert monitor.is_running is True

        await monitor.stop()
        assert monitor.is_running is False

    @pytest.mark.asyncio
    async def test_start_idempotent(self) -> None:
        registry = NodeRegistry(LocalNode(_make_mock_registry()))
        bus = _make_event_bus()
        monitor = NodeHealthMonitor(registry, bus, interval=0.1)

        await monitor.start()
        task1 = monitor._task
        await monitor.start()  # Second call should be no-op.
        assert monitor._task is task1

        await monitor.stop()

    @pytest.mark.asyncio
    async def test_stop_when_not_started(self) -> None:
        registry = NodeRegistry(LocalNode(_make_mock_registry()))
        bus = _make_event_bus()
        monitor = NodeHealthMonitor(registry, bus)
        await monitor.stop()  # Should not raise.


# ---------------------------------------------------------------------------
# Health checking
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNodeHealthChecking:
    @pytest.mark.asyncio
    async def test_check_all_nodes_skips_local(self) -> None:
        """Local nodes should not be heartbeated."""
        registry = NodeRegistry(LocalNode(_make_mock_registry()))
        bus = _make_event_bus()
        monitor = NodeHealthMonitor(registry, bus)

        await monitor._check_all_nodes()
        # No events emitted because there are no remote nodes.
        bus.emit.assert_not_called()

    @pytest.mark.asyncio
    async def test_check_all_nodes_heartbeats_remote(self) -> None:
        registry = NodeRegistry(LocalNode(_make_mock_registry()))
        remote = _make_remote_node("node-1", available=True)
        registry.register(remote)

        bus = _make_event_bus()
        monitor = NodeHealthMonitor(registry, bus)

        await monitor._check_all_nodes()

        remote.heartbeat.assert_awaited_once()
        bus.emit.assert_called_once()
        event_data = bus.emit.call_args[0][1]
        assert event_data["event"] == "node_healthy"
        assert event_data["node_id"] == "node-1"

    @pytest.mark.asyncio
    async def test_check_emits_unhealthy_on_failure(self) -> None:
        registry = NodeRegistry(LocalNode(_make_mock_registry()))
        remote = _make_remote_node("node-1", available=False)
        remote.heartbeat = AsyncMock(side_effect=Exception("timeout"))
        registry.register(remote)

        bus = _make_event_bus()
        monitor = NodeHealthMonitor(registry, bus)

        await monitor._check_all_nodes()

        event_data = bus.emit.call_args[0][1]
        assert event_data["event"] == "node_unhealthy"
        assert event_data["available"] is False

    @pytest.mark.asyncio
    async def test_state_transition_healthy_to_unhealthy(self) -> None:
        registry = NodeRegistry(LocalNode(_make_mock_registry()))
        remote = _make_remote_node("node-1", available=True)
        registry.register(remote)

        bus = _make_event_bus()
        monitor = NodeHealthMonitor(registry, bus)

        # First check: healthy.
        await monitor._check_all_nodes()
        assert bus.emit.call_args[0][1]["event"] == "node_healthy"

        # Second check: unhealthy.
        remote.is_available.return_value = False
        remote.heartbeat = AsyncMock(return_value={"status": "error"})
        bus.emit.reset_mock()
        await monitor._check_all_nodes()
        assert bus.emit.call_args[0][1]["event"] == "node_unhealthy"

    @pytest.mark.asyncio
    async def test_state_transition_unhealthy_to_recovered(self) -> None:
        registry = NodeRegistry(LocalNode(_make_mock_registry()))
        remote = _make_remote_node("node-1", available=False)
        remote.heartbeat = AsyncMock(return_value={"status": "error"})
        registry.register(remote)

        bus = _make_event_bus()
        monitor = NodeHealthMonitor(registry, bus)

        # First check: unhealthy.
        await monitor._check_all_nodes()
        assert bus.emit.call_args[0][1]["event"] == "node_unhealthy"

        # Second check: recovered.
        remote.is_available.return_value = True
        remote.heartbeat = AsyncMock(return_value={"status": "ok"})
        bus.emit.reset_mock()
        await monitor._check_all_nodes()
        assert bus.emit.call_args[0][1]["event"] == "node_recovered"

    @pytest.mark.asyncio
    async def test_check_multiple_remote_nodes(self) -> None:
        registry = NodeRegistry(LocalNode(_make_mock_registry()))
        remote1 = _make_remote_node("node-1", available=True)
        remote2 = _make_remote_node("node-2", available=False)
        remote2.heartbeat = AsyncMock(return_value={"status": "error"})
        registry.register(remote1)
        registry.register(remote2)

        bus = _make_event_bus()
        monitor = NodeHealthMonitor(registry, bus)

        await monitor._check_all_nodes()
        assert bus.emit.call_count == 2


# ---------------------------------------------------------------------------
# Manual heartbeat (check_node)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCheckNode:
    @pytest.mark.asyncio
    async def test_check_node_not_found(self) -> None:
        registry = NodeRegistry(LocalNode(_make_mock_registry()))
        bus = _make_event_bus()
        monitor = NodeHealthMonitor(registry, bus)

        result = await monitor.check_node("nonexistent")
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_check_local_node(self) -> None:
        registry = NodeRegistry(LocalNode(_make_mock_registry()))
        bus = _make_event_bus()
        monitor = NodeHealthMonitor(registry, bus)

        result = await monitor.check_node("local")
        assert result["status"] == "ok"
        assert result["type"] == "local"

    @pytest.mark.asyncio
    async def test_check_remote_node(self) -> None:
        registry = NodeRegistry(LocalNode(_make_mock_registry()))
        remote = _make_remote_node("node-1", available=True)
        registry.register(remote)

        bus = _make_event_bus()
        monitor = NodeHealthMonitor(registry, bus)

        result = await monitor.check_node("node-1")
        remote.heartbeat.assert_awaited_once()
        assert result["status"] == "ok"
