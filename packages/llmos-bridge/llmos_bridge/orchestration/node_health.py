"""Cluster health monitoring — periodic heartbeat for remote nodes.

``NodeHealthMonitor`` runs a background asyncio task that periodically
calls ``heartbeat()`` on every registered ``RemoteNode``.  It emits
events on ``TOPIC_NODES`` when a node's availability changes (healthy,
unhealthy, recovered).

Usage::

    monitor = NodeHealthMonitor(node_registry, event_bus, interval=10.0)
    await monitor.start()
    # ... later ...
    await monitor.stop()
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from llmos_bridge.events.bus import TOPIC_NODES, EventBus
from llmos_bridge.logging import get_logger
from llmos_bridge.orchestration.nodes import NodeRegistry

log = get_logger(__name__)


class NodeHealthMonitor:
    """Periodically heartbeats all remote nodes and emits health events."""

    def __init__(
        self,
        node_registry: NodeRegistry,
        event_bus: EventBus,
        interval: float = 10.0,
        timeout: float = 5.0,
    ) -> None:
        self._registry = node_registry
        self._bus = event_bus
        self._interval = interval
        self._timeout = timeout
        self._task: asyncio.Task[None] | None = None
        self._previous_states: dict[str, bool] = {}

    async def start(self) -> None:
        """Start the background heartbeat loop."""
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._heartbeat_loop())
        log.info("node_health_monitor_started", interval=self._interval)

    async def stop(self) -> None:
        """Stop the background heartbeat loop."""
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
            log.info("node_health_monitor_stopped")

    async def _heartbeat_loop(self) -> None:
        """Main loop: heartbeat all remote nodes, emit status events."""
        while True:
            try:
                await self._check_all_nodes()
            except Exception:
                log.exception("node_health_check_error")
            await asyncio.sleep(self._interval)

    async def _check_all_nodes(self) -> None:
        """Run heartbeat on all remote nodes concurrently."""
        from llmos_bridge.orchestration.remote_node import RemoteNode

        remote_nodes = self._registry.get_remote_nodes()
        if not remote_nodes:
            return

        tasks = []
        for node in remote_nodes:
            if isinstance(node, RemoteNode):
                tasks.append(self._check_single_node(node))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _check_single_node(self, node: Any) -> None:
        """Heartbeat a single node and emit events on state changes."""
        node_id = node.node_id
        was_available = self._previous_states.get(node_id)

        t0 = time.time()
        try:
            health_data = await node.heartbeat()
            is_available = node.is_available()
        except Exception:
            is_available = False
            health_data = {"status": "error"}

        latency_ms = (time.time() - t0) * 1000
        # Store latency on the node for API display.
        node._last_latency_ms = latency_ms

        self._previous_states[node_id] = is_available

        # Emit events on state transitions.
        if was_available is None:
            # First check — emit initial state.
            event_type = "node_healthy" if is_available else "node_unhealthy"
        elif was_available and not is_available:
            event_type = "node_unhealthy"
        elif not was_available and is_available:
            event_type = "node_recovered"
        else:
            # No state change — emit periodic healthy/unhealthy.
            event_type = "node_healthy" if is_available else "node_unhealthy"

        await self._bus.emit(
            TOPIC_NODES,
            {
                "event": event_type,
                "node_id": node_id,
                "available": is_available,
                "timestamp": time.time(),
                "latency_ms": latency_ms,
                "health": health_data,
            },
        )

    async def check_node(self, node_id: str) -> dict[str, Any]:
        """Trigger a heartbeat on a specific node.  Returns health data."""
        from llmos_bridge.orchestration.remote_node import RemoteNode

        node = self._registry.get_node(node_id)
        if node is None:
            return {"status": "error", "reason": f"Node '{node_id}' not found"}
        if not isinstance(node, RemoteNode):
            return {
                "status": "ok",
                "node_id": node_id,
                "type": "local",
                "available": node.is_available(),
            }
        health = await node.heartbeat()
        return health

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()
