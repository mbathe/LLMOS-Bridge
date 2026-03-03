"""Unit tests — NodeDiscoveryService (peer registration + dynamic management).

Tests cover:
- start(): registers peers from config
- start(): handles peer registration failures gracefully
- stop(): cleans up managed nodes
- register_node / unregister_node (dynamic)
- managed_nodes property
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llmos_bridge.exceptions import NodeNotFoundError
from llmos_bridge.orchestration.discovery import NodeDiscoveryService
from llmos_bridge.orchestration.nodes import LocalNode, NodeRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_module_registry() -> MagicMock:
    module = MagicMock()
    module.execute = AsyncMock(return_value={})
    registry = MagicMock()
    registry.get.return_value = module
    return registry


def _make_event_bus() -> MagicMock:
    bus = MagicMock()
    bus.emit = AsyncMock()
    return bus


def _make_settings(peers: list | None = None) -> MagicMock:
    settings = MagicMock()
    if peers is None:
        settings.node.peers = []
    else:
        settings.node.peers = peers
    return settings


def _make_peer(node_id: str, url: str, api_token: str | None = None) -> MagicMock:
    peer = MagicMock()
    peer.node_id = node_id
    peer.url = url
    peer.api_token = api_token
    peer.location = ""
    return peer


# ---------------------------------------------------------------------------
# Startup / shutdown
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDiscoveryStartup:
    @pytest.mark.asyncio
    async def test_start_no_peers(self) -> None:
        """Empty peers config → no nodes registered."""
        registry = NodeRegistry(LocalNode(_make_mock_module_registry()))
        bus = _make_event_bus()
        settings = _make_settings(peers=[])

        discovery = NodeDiscoveryService(registry, bus, settings)
        with patch("llmos_bridge.orchestration.discovery.RemoteNode") as MockNode:
            await discovery.start()

        assert len(registry) == 1  # Only local.
        assert discovery.managed_nodes == []

    @pytest.mark.asyncio
    async def test_start_registers_peers(self) -> None:
        registry = NodeRegistry(LocalNode(_make_mock_module_registry()))
        bus = _make_event_bus()
        peers = [_make_peer("node-1", "http://host1:40000")]
        settings = _make_settings(peers=peers)

        discovery = NodeDiscoveryService(registry, bus, settings)

        with patch("llmos_bridge.orchestration.discovery.RemoteNode") as MockNode:
            mock_instance = MagicMock()
            mock_instance.node_id = "node-1"
            mock_instance.start = AsyncMock()
            MockNode.return_value = mock_instance

            await discovery.start()

        assert "node-1" in registry.list_nodes()
        assert "node-1" in discovery.managed_nodes
        mock_instance.start.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_start_handles_peer_failure(self) -> None:
        """If a peer fails to start, it should be skipped without crashing."""
        registry = NodeRegistry(LocalNode(_make_mock_module_registry()))
        bus = _make_event_bus()
        peers = [
            _make_peer("node-1", "http://host1:40000"),
            _make_peer("node-2", "http://host2:40000"),
        ]
        settings = _make_settings(peers=peers)

        discovery = NodeDiscoveryService(registry, bus, settings)

        with patch("llmos_bridge.orchestration.discovery.RemoteNode") as MockNode:
            call_count = 0

            def side_effect(*args: object, **kwargs: object) -> MagicMock:
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise ConnectionError("unreachable")
                m = MagicMock()
                m.node_id = "node-2"
                m.start = AsyncMock()
                return m

            MockNode.side_effect = side_effect
            await discovery.start()

        # Only node-2 registered (node-1 failed).
        assert "node-1" not in registry.list_nodes()
        assert "node-2" in registry.list_nodes()

    @pytest.mark.asyncio
    async def test_start_emits_event(self) -> None:
        registry = NodeRegistry(LocalNode(_make_mock_module_registry()))
        bus = _make_event_bus()
        settings = _make_settings(peers=[])

        discovery = NodeDiscoveryService(registry, bus, settings)
        with patch("llmos_bridge.orchestration.discovery.RemoteNode"):
            await discovery.start()

        bus.emit.assert_called_once()
        event_data = bus.emit.call_args[0][1]
        assert event_data["event"] == "discovery_started"


# ---------------------------------------------------------------------------
# Stop
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDiscoveryStop:
    @pytest.mark.asyncio
    async def test_stop_cleans_up_nodes(self) -> None:
        registry = NodeRegistry(LocalNode(_make_mock_module_registry()))
        bus = _make_event_bus()
        settings = _make_settings()

        discovery = NodeDiscoveryService(registry, bus, settings)

        # Manually add a managed node.
        mock_node = MagicMock()
        mock_node.node_id = "node-1"
        mock_node.stop = AsyncMock()
        discovery._managed_nodes["node-1"] = mock_node
        registry.register(mock_node)

        await discovery.stop()

        mock_node.stop.assert_awaited_once()
        assert "node-1" not in registry.list_nodes()
        assert discovery.managed_nodes == []


# ---------------------------------------------------------------------------
# Dynamic registration
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDynamicRegistration:
    @pytest.mark.asyncio
    async def test_register_node(self) -> None:
        registry = NodeRegistry(LocalNode(_make_mock_module_registry()))
        bus = _make_event_bus()
        settings = _make_settings()

        discovery = NodeDiscoveryService(registry, bus, settings)

        with patch("llmos_bridge.orchestration.discovery.RemoteNode") as MockNode:
            mock_instance = MagicMock()
            mock_instance.node_id = "dyn-1"
            mock_instance.start = AsyncMock()
            MockNode.return_value = mock_instance

            result = await discovery.register_node("dyn-1", "http://host:40000")

        assert result is mock_instance
        assert "dyn-1" in registry.list_nodes()
        assert "dyn-1" in discovery.managed_nodes
        bus.emit.assert_called()

    @pytest.mark.asyncio
    async def test_unregister_node(self) -> None:
        registry = NodeRegistry(LocalNode(_make_mock_module_registry()))
        bus = _make_event_bus()
        settings = _make_settings()

        discovery = NodeDiscoveryService(registry, bus, settings)

        # Add a managed node.
        mock_node = MagicMock()
        mock_node.node_id = "dyn-1"
        mock_node.stop = AsyncMock()
        discovery._managed_nodes["dyn-1"] = mock_node
        registry.register(mock_node)

        result = await discovery.unregister_node("dyn-1")
        assert result is True
        mock_node.stop.assert_awaited_once()
        assert "dyn-1" not in registry.list_nodes()

    @pytest.mark.asyncio
    async def test_unregister_nonexistent_raises(self) -> None:
        registry = NodeRegistry(LocalNode(_make_mock_module_registry()))
        bus = _make_event_bus()
        settings = _make_settings()

        discovery = NodeDiscoveryService(registry, bus, settings)

        with pytest.raises(NodeNotFoundError):
            await discovery.unregister_node("nonexistent")

    @pytest.mark.asyncio
    async def test_unregister_non_managed_node(self) -> None:
        """Node in registry but not managed by discovery."""
        registry = NodeRegistry(LocalNode(_make_mock_module_registry()))
        bus = _make_event_bus()
        settings = _make_settings()

        discovery = NodeDiscoveryService(registry, bus, settings)

        # Add directly to registry (not via discovery).
        external_node = MagicMock()
        external_node.node_id = "ext-1"
        registry.register(external_node)

        result = await discovery.unregister_node("ext-1")
        assert result is True
        assert "ext-1" not in registry.list_nodes()
