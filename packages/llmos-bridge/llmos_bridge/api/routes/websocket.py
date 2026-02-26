"""WS /ws/stream — Real-time plan event streaming.

The ``WebSocketEventBus`` bridges the EventBus infrastructure to live
WebSocket clients.  Inject it into the ``AuditLogger`` (or a
``FanoutEventBus``) at startup so all audit events are forwarded to
connected dashboard clients in real time::

    from llmos_bridge.api.routes.websocket import WebSocketEventBus, manager
    from llmos_bridge.events import FanoutEventBus, LogEventBus
    from llmos_bridge.security.audit import AuditLogger

    bus = FanoutEventBus([LogEventBus(audit_file), WebSocketEventBus(manager)])
    audit_logger = AuditLogger(bus=bus)
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from llmos_bridge.api.schemas import WSMessage
from llmos_bridge.events.bus import EventBus
from llmos_bridge.logging import get_logger

log = get_logger(__name__)
router = APIRouter(tags=["websocket"])


class ConnectionManager:
    """Manages active WebSocket connections and broadcasts plan events."""

    def __init__(self) -> None:
        self._connections: dict[str, list[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, plan_id: str | None = None) -> None:
        await websocket.accept()
        key = plan_id or "__all__"
        self._connections.setdefault(key, []).append(websocket)
        log.debug("ws_connected", plan_id=plan_id)

    def disconnect(self, websocket: WebSocket, plan_id: str | None = None) -> None:
        key = plan_id or "__all__"
        connections = self._connections.get(key, [])
        if websocket in connections:
            connections.remove(websocket)
        log.debug("ws_disconnected", plan_id=plan_id)

    async def broadcast(self, message: WSMessage, plan_id: str | None = None) -> None:
        """Send *message* to all connections subscribed to *plan_id*."""
        targets: list[WebSocket] = []
        if plan_id:
            targets.extend(self._connections.get(plan_id, []))
        targets.extend(self._connections.get("__all__", []))

        payload = message.model_dump_json()
        dead: list[tuple[WebSocket, str | None]] = []

        for ws in targets:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append((ws, plan_id))

        for ws, pid in dead:
            self.disconnect(ws, pid)


# Module-level singleton — shared between the routes and WebSocketEventBus.
manager = ConnectionManager()


class WebSocketEventBus(EventBus):
    """EventBus backend that forwards events to WebSocket clients.

    This is a thin adapter: every ``emit()`` call builds a ``WSMessage``
    and broadcasts it via the ``ConnectionManager``.  Failures are logged
    and swallowed so a missing WebSocket client never affects execution.

    Usage (in server.py):

        from llmos_bridge.events import FanoutEventBus, LogEventBus
        from llmos_bridge.api.routes.websocket import WebSocketEventBus, manager

        bus = FanoutEventBus([LogEventBus(audit_file), WebSocketEventBus(manager)])
        audit_logger = AuditLogger(bus=bus)
    """

    def __init__(self, connection_manager: ConnectionManager) -> None:
        self._manager = connection_manager

    async def emit(self, topic: str, event: dict[str, Any]) -> None:
        """Broadcast *event* to WebSocket subscribers for the relevant plan."""
        self._stamp(topic, event)
        event_type = event.get("event", topic)
        plan_id = event.get("plan_id")  # None → broadcast to __all__

        msg = WSMessage(type=str(event_type), payload=dict(event))
        try:
            await self._manager.broadcast(msg, plan_id=plan_id)
        except Exception as exc:
            log.warning("ws_broadcast_failed", topic=topic, error=str(exc))


@router.websocket("/ws/stream")
async def stream_all(websocket: WebSocket) -> None:
    """Subscribe to events for all plans."""
    await manager.connect(websocket)
    try:
        while True:
            # Keep alive — client can send ping frames.
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        manager.disconnect(websocket)


@router.websocket("/ws/plans/{plan_id}")
async def stream_plan(websocket: WebSocket, plan_id: str) -> None:
    """Subscribe to events for a specific plan."""
    await manager.connect(websocket, plan_id=plan_id)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        manager.disconnect(websocket, plan_id=plan_id)
