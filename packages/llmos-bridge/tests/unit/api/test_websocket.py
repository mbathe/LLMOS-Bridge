"""Unit tests — WebSocket routes and ConnectionManager."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from llmos_bridge.api.routes.websocket import (
    ConnectionManager,
    WebSocketEventBus,
    router,
)
from llmos_bridge.api.schemas import WSMessage


@pytest.fixture
def ws_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    # Provide minimal settings so _verify_ws_token can access api_token.
    settings_mock = MagicMock()
    settings_mock.security.api_token = None  # No auth → local-only mode
    app.state.settings = settings_mock
    return app


@pytest.fixture
def ws_client(ws_app: FastAPI) -> TestClient:
    return TestClient(ws_app)


@pytest.mark.unit
class TestConnectionManager:
    def test_connect_adds_to_all_bucket_when_no_plan_id(self) -> None:
        mgr = ConnectionManager()
        ws = MagicMock()
        ws.accept = AsyncMock(return_value=None)

        import asyncio
        asyncio.get_event_loop().run_until_complete(mgr.connect(ws))
        assert ws in mgr._connections.get("__all__", [])

    def test_connect_adds_to_plan_bucket(self) -> None:
        mgr = ConnectionManager()
        ws = MagicMock()
        ws.accept = AsyncMock(return_value=None)

        import asyncio
        asyncio.get_event_loop().run_until_complete(mgr.connect(ws, plan_id="plan_1"))
        assert ws in mgr._connections.get("plan_1", [])

    def test_disconnect_removes_websocket(self) -> None:
        mgr = ConnectionManager()
        ws = MagicMock()
        ws.accept = AsyncMock(return_value=None)

        import asyncio
        asyncio.get_event_loop().run_until_complete(mgr.connect(ws, plan_id="plan_1"))
        mgr.disconnect(ws, plan_id="plan_1")
        assert ws not in mgr._connections.get("plan_1", [])

    def test_disconnect_nonexistent_is_noop(self) -> None:
        mgr = ConnectionManager()
        ws = MagicMock()
        mgr.disconnect(ws, plan_id="nonexistent")  # should not raise

    def test_broadcast_sends_to_plan_subscribers(self) -> None:
        mgr = ConnectionManager()
        ws1 = MagicMock()
        ws1.send_text = AsyncMock(return_value=None)
        ws1.accept = AsyncMock(return_value=None)

        import asyncio
        asyncio.get_event_loop().run_until_complete(mgr.connect(ws1, plan_id="plan_1"))

        msg = WSMessage(type="plan_started", payload={"plan_id": "plan_1"})
        asyncio.get_event_loop().run_until_complete(mgr.broadcast(msg, plan_id="plan_1"))
        ws1.send_text.assert_called_once()

    def test_broadcast_removes_dead_connections(self) -> None:
        mgr = ConnectionManager()
        ws1 = MagicMock()
        ws1.send_text = AsyncMock(side_effect=RuntimeError("disconnected"))
        ws1.accept = AsyncMock(return_value=None)

        import asyncio
        asyncio.get_event_loop().run_until_complete(mgr.connect(ws1, plan_id="plan_x"))

        msg = WSMessage(type="test", payload={})
        asyncio.get_event_loop().run_until_complete(mgr.broadcast(msg, plan_id="plan_x"))
        # Dead connection should be removed
        assert ws1 not in mgr._connections.get("plan_x", [])

    def test_broadcast_all_when_no_plan_id(self) -> None:
        mgr = ConnectionManager()
        ws_all = MagicMock()
        ws_all.send_text = AsyncMock(return_value=None)
        ws_all.accept = AsyncMock(return_value=None)

        import asyncio
        asyncio.get_event_loop().run_until_complete(mgr.connect(ws_all))  # __all__

        msg = WSMessage(type="global_event", payload={})
        asyncio.get_event_loop().run_until_complete(mgr.broadcast(msg, plan_id=None))
        ws_all.send_text.assert_called_once()


@pytest.mark.unit
class TestWebSocketEventBus:
    async def test_emit_with_no_connections(self) -> None:
        mgr = ConnectionManager()
        bus = WebSocketEventBus(mgr)
        # No connections — should not raise
        await bus.emit("plan_started", {"event": "started", "plan_id": "p1"})

    async def test_emit_broadcasts_to_subscribers(self) -> None:
        mgr = ConnectionManager()
        ws = MagicMock()
        ws.send_text = AsyncMock(return_value=None)
        ws.accept = AsyncMock(return_value=None)

        await mgr.connect(ws, plan_id="p1")
        bus = WebSocketEventBus(mgr)
        await bus.emit("plan_started", {"event": "started", "plan_id": "p1"})
        ws.send_text.assert_called_once()

    async def test_emit_handles_broadcast_failure(self) -> None:
        mgr = MagicMock()
        mgr.broadcast = AsyncMock(side_effect=RuntimeError("boom"))
        bus = WebSocketEventBus(mgr)
        # Should not raise
        await bus.emit("plan_started", {"event": "started"})


@pytest.mark.unit
class TestWebSocketRoutes:
    def test_stream_all_ping_pong(self, ws_client: TestClient) -> None:
        with ws_client.websocket_connect("/ws/stream") as ws:
            ws.send_text("ping")
            data = ws.receive_text()
            assert data == "pong"

    def test_stream_plan_ping_pong(self, ws_client: TestClient) -> None:
        with ws_client.websocket_connect("/ws/plans/plan_abc") as ws:
            ws.send_text("ping")
            data = ws.receive_text()
            assert data == "pong"

    def test_stream_all_disconnect(self, ws_client: TestClient) -> None:
        # Just connect and close — should not raise
        with ws_client.websocket_connect("/ws/stream"):
            pass

    def test_stream_plan_disconnect(self, ws_client: TestClient) -> None:
        with ws_client.websocket_connect("/ws/plans/plan_xyz"):
            pass
