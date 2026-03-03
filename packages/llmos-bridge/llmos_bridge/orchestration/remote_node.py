"""Distributed node — RemoteNode implementation over HTTP.

``RemoteNode`` executes IML actions on a remote LLMOS Bridge daemon via HTTP.
It wraps httpx to:
  1. Submit a minimal single-action plan to the remote daemon (POST /plans).
  2. Poll for completion (GET /plans/{id}).
  3. Extract and return the action result.

The remote daemon handles its own security, permissions, and module execution,
so RemoteNode is a thin transport layer.

Health monitoring is performed via GET /health on the remote daemon.

Usage::

    node = RemoteNode("node_lyon", "http://192.168.1.50:40000")
    await node.start()
    result = await node.execute_action("filesystem", "read_file", {"path": "/tmp/x"})
    await node.stop()
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

import httpx

from llmos_bridge.exceptions import NodeUnreachableError
from llmos_bridge.logging import get_logger
from llmos_bridge.orchestration.nodes import BaseNode

log = get_logger(__name__)

# How long to wait between polls when waiting for a plan to complete.
_POLL_INTERVAL = 0.25
_MAX_POLL_ATTEMPTS = 1200  # 5 minutes at 0.25s interval


class RemoteNode(BaseNode):
    """Executes actions on a remote LLMOS Bridge daemon via HTTP."""

    def __init__(
        self,
        node_id: str,
        base_url: str,
        api_token: str | None = None,
        location: str = "",
        timeout: float = 30.0,
    ) -> None:
        self._node_id = node_id
        self._base_url = base_url.rstrip("/")
        self._api_token = api_token
        self._location = location
        self._timeout = timeout
        self._available = False  # Not available until first successful heartbeat
        self._last_heartbeat: float | None = None
        self._capabilities: list[str] = []
        self._client: httpx.AsyncClient | None = None

    @property
    def node_id(self) -> str:
        return self._node_id

    async def start(self) -> None:
        """Create the httpx client and perform an initial heartbeat."""
        headers: dict[str, str] = {}
        if self._api_token:
            headers["X-LLMOS-Token"] = self._api_token
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers=headers,
            timeout=httpx.Timeout(self._timeout),
        )
        # Initial heartbeat — marks the node as available (or not).
        try:
            await self.heartbeat()
        except Exception:
            log.warning(
                "remote_node_initial_heartbeat_failed",
                node_id=self._node_id,
                url=self._base_url,
            )

    async def stop(self) -> None:
        """Close the httpx client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def execute_action(
        self,
        module_id: str,
        action_name: str,
        params: dict[str, Any],
    ) -> Any:
        """Submit a single-action plan to the remote daemon and return the result.

        Raises:
            NodeUnreachableError: if the remote daemon is not reachable.
        """
        if self._client is None:
            raise NodeUnreachableError(self._node_id, "Client not started")
        if not self._available:
            raise NodeUnreachableError(self._node_id, "Node is not available")

        plan_id = f"remote-{uuid.uuid4().hex[:12]}"
        action_id = "remote-action-0"

        plan_payload = {
            "plan_id": plan_id,
            "protocol_version": "2.0",
            "description": f"Remote action: {module_id}.{action_name}",
            "actions": [
                {
                    "id": action_id,
                    "module": module_id,
                    "action": action_name,
                    "params": params,
                }
            ],
        }

        try:
            # Submit the plan synchronously (async_execution=False blocks until done).
            resp = await self._client.post(
                "/plans",
                json={"plan": plan_payload, "async_execution": False},
                timeout=httpx.Timeout(max(self._timeout, 300.0)),
            )
            resp.raise_for_status()
        except httpx.ConnectError as exc:
            self._available = False
            raise NodeUnreachableError(self._node_id, f"Connection failed: {exc}") from exc
        except httpx.TimeoutException as exc:
            raise NodeUnreachableError(self._node_id, f"Request timed out: {exc}") from exc
        except httpx.HTTPStatusError as exc:
            raise NodeUnreachableError(
                self._node_id,
                f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
            ) from exc

        data = resp.json()

        # Synchronous execution: result is in the response.
        if "actions" in data and data["actions"]:
            action_result = data["actions"][0]
            if action_result.get("status") == "failed":
                raise NodeUnreachableError(
                    self._node_id,
                    f"Remote action failed: {action_result.get('error', 'unknown error')}",
                )
            return action_result.get("result")

        # Fallback: if async_execution was used or response format differs,
        # poll for completion.
        return await self._poll_for_result(plan_id, action_id)

    async def _poll_for_result(self, plan_id: str, action_id: str) -> Any:
        """Poll GET /plans/{plan_id} until the action completes."""
        assert self._client is not None
        for _ in range(_MAX_POLL_ATTEMPTS):
            await asyncio.sleep(_POLL_INTERVAL)
            try:
                resp = await self._client.get(f"/plans/{plan_id}")
                resp.raise_for_status()
            except Exception as exc:
                raise NodeUnreachableError(
                    self._node_id, f"Failed to poll plan status: {exc}"
                ) from exc

            data = resp.json()
            plan_status = data.get("status", "")

            if plan_status in ("completed", "failed", "cancelled"):
                # Find the action result.
                for action_data in data.get("actions", []):
                    if action_data.get("action_id") == action_id:
                        if action_data.get("status") == "failed":
                            raise NodeUnreachableError(
                                self._node_id,
                                f"Remote action failed: {action_data.get('error', 'unknown')}",
                            )
                        return action_data.get("result")
                # Action not found in response — return None.
                return None

        raise NodeUnreachableError(
            self._node_id,
            f"Timed out waiting for plan {plan_id} to complete",
        )

    def is_available(self) -> bool:
        return self._available

    async def heartbeat(self) -> dict[str, Any]:
        """Call GET /health on the remote daemon.

        Updates ``_available``, ``_capabilities``, and ``_last_heartbeat``.
        Returns the health response dict.
        """
        if self._client is None:
            self._available = False
            return {"status": "error", "reason": "client not started"}

        try:
            resp = await self._client.get("/health", timeout=httpx.Timeout(5.0))
            resp.raise_for_status()
        except Exception as exc:
            self._available = False
            log.debug(
                "remote_node_heartbeat_failed",
                node_id=self._node_id,
                error=str(exc),
            )
            return {"status": "error", "reason": str(exc)}

        data = resp.json()
        self._available = data.get("status") == "ok"
        self._last_heartbeat = time.time()

        # Extract available modules as capabilities.
        modules = data.get("modules")
        if modules and isinstance(modules, dict):
            self._capabilities = modules.get("available", [])
        elif isinstance(modules, list):
            self._capabilities = modules

        log.debug(
            "remote_node_heartbeat_ok",
            node_id=self._node_id,
            capabilities=self._capabilities,
        )
        return data

    def __repr__(self) -> str:
        return (
            f"RemoteNode(node_id={self._node_id!r}, "
            f"url={self._base_url!r}, available={self._available})"
        )
