"""Unit tests — RemoteNode (HTTP-based remote action execution).

Tests cover:
- RemoteNode properties and lifecycle (start/stop)
- execute_action: success, sync response, poll fallback
- execute_action: error handling (connection, timeout, HTTP errors)
- heartbeat: success, failure, capability extraction
- is_available state management
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llmos_bridge.exceptions import NodeUnreachableError
from llmos_bridge.orchestration.nodes import BaseNode
from llmos_bridge.orchestration.remote_node import RemoteNode


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_response(
    status_code: int = 200,
    json_data: dict[str, Any] | None = None,
    text: str = "",
) -> MagicMock:
    """Create a mock httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.text = text
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        import httpx

        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=resp
        )
    return resp


# ---------------------------------------------------------------------------
# Properties & lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRemoteNodeBasics:
    def test_node_id(self) -> None:
        node = RemoteNode("node-1", "http://localhost:40000")
        assert node.node_id == "node-1"

    def test_is_base_node_subclass(self) -> None:
        node = RemoteNode("node-1", "http://localhost:40000")
        assert isinstance(node, BaseNode)

    def test_not_available_before_start(self) -> None:
        node = RemoteNode("node-1", "http://localhost:40000")
        assert node.is_available() is False

    def test_repr(self) -> None:
        node = RemoteNode("node-1", "http://remote:40000")
        r = repr(node)
        assert "node-1" in r
        assert "http://remote:40000" in r

    def test_base_url_trailing_slash_stripped(self) -> None:
        node = RemoteNode("n", "http://host:40000/")
        assert node._base_url == "http://host:40000"

    @pytest.mark.asyncio
    async def test_start_creates_client(self) -> None:
        node = RemoteNode("n", "http://localhost:40000")
        with patch.object(node, "heartbeat", new_callable=AsyncMock):
            await node.start()
        assert node._client is not None
        await node.stop()
        assert node._client is None

    @pytest.mark.asyncio
    async def test_start_with_api_token(self) -> None:
        node = RemoteNode("n", "http://localhost:40000", api_token="secret")
        with patch.object(node, "heartbeat", new_callable=AsyncMock):
            await node.start()
        assert node._client is not None
        # Token is set in the client headers.
        assert node._client.headers.get("X-LLMOS-Token") == "secret"
        await node.stop()

    @pytest.mark.asyncio
    async def test_start_handles_heartbeat_failure(self) -> None:
        """start() should not raise even if initial heartbeat fails."""
        node = RemoteNode("n", "http://localhost:40000")
        with patch.object(
            node, "heartbeat", new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            await node.start()  # Should not raise
        assert node._client is not None
        await node.stop()


# ---------------------------------------------------------------------------
# execute_action
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRemoteNodeExecuteAction:
    @pytest.mark.asyncio
    async def test_execute_success_sync_response(self) -> None:
        """async_execution=False: result is in the response."""
        node = RemoteNode("n", "http://localhost:40000")
        with patch.object(node, "heartbeat", new_callable=AsyncMock):
            await node.start()

        node._available = True
        mock_resp = _mock_response(
            json_data={
                "plan_id": "p1",
                "status": "completed",
                "actions": [
                    {
                        "action_id": "remote-action-0",
                        "status": "completed",
                        "result": {"content": "hello"},
                    }
                ],
            }
        )
        node._client.post = AsyncMock(return_value=mock_resp)  # type: ignore[union-attr]

        result = await node.execute_action("filesystem", "read_file", {"path": "/tmp/x"})
        assert result == {"content": "hello"}
        await node.stop()

    @pytest.mark.asyncio
    async def test_execute_raises_when_not_started(self) -> None:
        node = RemoteNode("n", "http://localhost:40000")
        with pytest.raises(NodeUnreachableError, match="not started"):
            await node.execute_action("fs", "read", {})

    @pytest.mark.asyncio
    async def test_execute_raises_when_not_available(self) -> None:
        node = RemoteNode("n", "http://localhost:40000")
        with patch.object(node, "heartbeat", new_callable=AsyncMock):
            await node.start()
        node._available = False
        with pytest.raises(NodeUnreachableError, match="not available"):
            await node.execute_action("fs", "read", {})
        await node.stop()

    @pytest.mark.asyncio
    async def test_execute_raises_on_connection_error(self) -> None:
        import httpx

        node = RemoteNode("n", "http://localhost:40000")
        with patch.object(node, "heartbeat", new_callable=AsyncMock):
            await node.start()
        node._available = True
        node._client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))  # type: ignore[union-attr]

        with pytest.raises(NodeUnreachableError, match="Connection failed"):
            await node.execute_action("fs", "read", {})
        # Node should be marked unavailable after connection error.
        assert node._available is False
        await node.stop()

    @pytest.mark.asyncio
    async def test_execute_raises_on_timeout(self) -> None:
        import httpx

        node = RemoteNode("n", "http://localhost:40000")
        with patch.object(node, "heartbeat", new_callable=AsyncMock):
            await node.start()
        node._available = True
        node._client.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))  # type: ignore[union-attr]

        with pytest.raises(NodeUnreachableError, match="timed out"):
            await node.execute_action("fs", "read", {})
        await node.stop()

    @pytest.mark.asyncio
    async def test_execute_raises_on_remote_action_failure(self) -> None:
        node = RemoteNode("n", "http://localhost:40000")
        with patch.object(node, "heartbeat", new_callable=AsyncMock):
            await node.start()
        node._available = True

        mock_resp = _mock_response(
            json_data={
                "actions": [
                    {
                        "action_id": "remote-action-0",
                        "status": "failed",
                        "error": "Permission denied",
                    }
                ],
            }
        )
        node._client.post = AsyncMock(return_value=mock_resp)  # type: ignore[union-attr]

        with pytest.raises(NodeUnreachableError, match="Permission denied"):
            await node.execute_action("fs", "read", {})
        await node.stop()

    @pytest.mark.asyncio
    async def test_execute_http_error(self) -> None:
        import httpx

        node = RemoteNode("n", "http://localhost:40000")
        with patch.object(node, "heartbeat", new_callable=AsyncMock):
            await node.start()
        node._available = True

        mock_resp = _mock_response(status_code=500, text="Internal Server Error")
        node._client.post = AsyncMock(return_value=mock_resp)  # type: ignore[union-attr]

        with pytest.raises(NodeUnreachableError, match="HTTP 500"):
            await node.execute_action("fs", "read", {})
        await node.stop()


# ---------------------------------------------------------------------------
# heartbeat
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRemoteNodeHeartbeat:
    @pytest.mark.asyncio
    async def test_heartbeat_success(self) -> None:
        node = RemoteNode("n", "http://localhost:40000")
        # Manually set client to avoid real heartbeat in start().
        node._client = MagicMock()
        mock_resp = _mock_response(
            json_data={
                "status": "ok",
                "version": "0.1.0",
                "modules": {"available": ["filesystem", "os_exec"], "failed": {}},
            }
        )
        node._client.get = AsyncMock(return_value=mock_resp)

        data = await node.heartbeat()

        assert data["status"] == "ok"
        assert node._available is True
        assert node._last_heartbeat is not None
        assert node._capabilities == ["filesystem", "os_exec"]

    @pytest.mark.asyncio
    async def test_heartbeat_failure_marks_unavailable(self) -> None:
        node = RemoteNode("n", "http://localhost:40000")
        node._client = MagicMock()
        node._client.get = AsyncMock(side_effect=Exception("connection refused"))
        node._available = True  # Was available before.

        data = await node.heartbeat()

        assert data["status"] == "error"
        assert node._available is False

    @pytest.mark.asyncio
    async def test_heartbeat_without_client(self) -> None:
        node = RemoteNode("n", "http://localhost:40000")
        data = await node.heartbeat()
        assert data["status"] == "error"
        assert node._available is False

    @pytest.mark.asyncio
    async def test_heartbeat_non_ok_status(self) -> None:
        node = RemoteNode("n", "http://localhost:40000")
        node._client = MagicMock()
        mock_resp = _mock_response(json_data={"status": "degraded"})
        node._client.get = AsyncMock(return_value=mock_resp)

        data = await node.heartbeat()
        assert node._available is False  # Only "ok" sets available=True

    @pytest.mark.asyncio
    async def test_heartbeat_modules_as_list(self) -> None:
        """Some responses may return modules as a plain list."""
        node = RemoteNode("n", "http://localhost:40000")
        node._client = MagicMock()
        mock_resp = _mock_response(
            json_data={"status": "ok", "modules": ["filesystem"]}
        )
        node._client.get = AsyncMock(return_value=mock_resp)

        await node.heartbeat()
        assert node._capabilities == ["filesystem"]
