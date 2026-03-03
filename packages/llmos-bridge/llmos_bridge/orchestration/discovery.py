"""Node discovery — register remote nodes from config or dynamically at runtime.

``NodeDiscoveryService`` is the single entry point for all node registration.
At startup it reads ``settings.node.peers`` and creates a ``RemoteNode``
for each configured peer.  At runtime, new nodes can be registered or
unregistered dynamically via the REST API.

mDNS-based automatic discovery (via ``zeroconf``) is designed but not
implemented in this phase — the hook is present for Phase 3+.

Usage::

    discovery = NodeDiscoveryService(node_registry, event_bus, settings)
    await discovery.start()      # registers peers from config
    node = await discovery.register_node("node_lyon", "http://192.168.1.50:40000")
    await discovery.unregister_node("node_lyon")
    await discovery.stop()       # stops all remote nodes
"""

from __future__ import annotations

import time
from typing import Any

from llmos_bridge.events.bus import TOPIC_NODES, EventBus
from llmos_bridge.exceptions import NodeNotFoundError
from llmos_bridge.logging import get_logger
from llmos_bridge.orchestration.nodes import NodeRegistry
from llmos_bridge.orchestration.remote_node import RemoteNode

log = get_logger(__name__)


class NodeDiscoveryService:
    """Manages discovery and registration of remote nodes."""

    def __init__(
        self,
        node_registry: NodeRegistry,
        event_bus: EventBus,
        settings: Any,
    ) -> None:
        self._registry = node_registry
        self._bus = event_bus
        self._settings = settings
        self._managed_nodes: dict[str, RemoteNode] = {}

    async def start(self) -> None:
        """Register peers from configuration and start mDNS if enabled."""
        peers = getattr(self._settings.node, "peers", [])
        for peer in peers:
            try:
                node = RemoteNode(
                    node_id=peer.node_id,
                    base_url=peer.url,
                    api_token=peer.api_token,
                    location=getattr(peer, "location", ""),
                )
                await node.start()
                self._registry.register(node)
                self._managed_nodes[peer.node_id] = node
                log.info(
                    "peer_node_registered",
                    node_id=peer.node_id,
                    url=peer.url,
                )
            except Exception as exc:
                log.warning(
                    "peer_node_registration_failed",
                    node_id=peer.node_id,
                    url=peer.url,
                    error=str(exc),
                )

        await self._bus.emit(
            TOPIC_NODES,
            {
                "event": "discovery_started",
                "peers_registered": len(self._managed_nodes),
                "timestamp": time.time(),
            },
        )
        log.info(
            "node_discovery_started",
            peers_configured=len(peers),
            peers_registered=len(self._managed_nodes),
        )

    async def stop(self) -> None:
        """Stop all managed remote nodes."""
        for node_id, node in list(self._managed_nodes.items()):
            try:
                await node.stop()
                self._registry.unregister(node_id)
            except Exception as exc:
                log.warning("node_stop_failed", node_id=node_id, error=str(exc))
        self._managed_nodes.clear()
        log.info("node_discovery_stopped")

    async def register_node(
        self,
        node_id: str,
        url: str,
        api_token: str | None = None,
        location: str = "",
    ) -> RemoteNode:
        """Register a new remote node dynamically (e.g. via REST API).

        Returns the created RemoteNode instance.
        """
        node = RemoteNode(
            node_id=node_id,
            base_url=url,
            api_token=api_token,
            location=location,
        )
        await node.start()
        self._registry.register(node)
        self._managed_nodes[node_id] = node

        await self._bus.emit(
            TOPIC_NODES,
            {
                "event": "node_registered",
                "node_id": node_id,
                "url": url,
                "timestamp": time.time(),
            },
        )
        log.info("node_registered_dynamic", node_id=node_id, url=url)
        return node

    async def unregister_node(self, node_id: str) -> bool:
        """Unregister and stop a remote node.

        Returns True if the node was found and removed.
        Raises NodeNotFoundError if the node is not managed by discovery.
        """
        node = self._managed_nodes.pop(node_id, None)
        if node is None:
            # Check if it exists in the registry at all.
            if self._registry.get_node(node_id) is None:
                raise NodeNotFoundError(node_id)
            # Node exists but not managed by us — just unregister.
            self._registry.unregister(node_id)
            return True

        await node.stop()
        self._registry.unregister(node_id)

        await self._bus.emit(
            TOPIC_NODES,
            {
                "event": "node_unregistered",
                "node_id": node_id,
                "timestamp": time.time(),
            },
        )
        log.info("node_unregistered_dynamic", node_id=node_id)
        return True

    @property
    def managed_nodes(self) -> list[str]:
        """Return IDs of nodes managed by this discovery service."""
        return list(self._managed_nodes)
