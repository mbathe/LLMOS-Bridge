"""Orchestration layer — ActionStream for real-time progress streaming.

An ``ActionStream`` is injected into the ``params`` dict of actions decorated
with ``@streams_progress``.  Module developers pop it from params and use it
to emit progress updates, intermediate data, and status changes that flow
through the EventBus to SSE-connected agents.

Usage in a module::

    from llmos_bridge.orchestration.streaming_decorators import streams_progress

    class MyModule(BaseModule):

        @streams_progress
        async def _action_download(self, params: dict) -> Any:
            stream: ActionStream = params.pop("_stream")
            for i, chunk in enumerate(download(url)):
                await stream.emit_progress(i / total * 100, f"Chunk {i}")
            return {"path": filepath}

The stream is only available when the executor has an AuditLogger with an
EventBus.  If ``_stream`` is not in params, the action should proceed
normally without streaming (graceful degradation).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from llmos_bridge.events.bus import TOPIC_ACTION_PROGRESS, EventBus


# Reserved key under which the ActionStream is injected into params.
_STREAM_KEY = "_stream"


@dataclass
class ActionStream:
    """Bidirectional channel for streaming progress from a running action.

    The executor creates an ``ActionStream`` for actions decorated with
    ``@streams_progress`` and injects it into ``params["_stream"]``.

    All ``emit_*`` methods are fire-and-forget — failures are silently
    swallowed so that streaming issues never block action execution.
    """

    plan_id: str
    action_id: str
    module_id: str
    action_name: str
    _bus: EventBus = field(repr=False)

    async def emit_progress(self, percent: float, message: str = "") -> None:
        """Emit a progress update (0–100) to the agent.

        Args:
            percent: Completion percentage, clamped to [0, 100].
            message: Optional human-readable progress message.
        """
        await self._bus.emit(TOPIC_ACTION_PROGRESS, {
            "event": "action_progress",
            "plan_id": self.plan_id,
            "action_id": self.action_id,
            "module_id": self.module_id,
            "action": self.action_name,
            "percent": max(0.0, min(100.0, percent)),
            "message": message,
        })

    async def emit_intermediate(self, data: dict[str, Any]) -> None:
        """Emit intermediate data (partial results, status info).

        Args:
            data: Arbitrary JSON-serialisable dict with partial results.
        """
        await self._bus.emit(TOPIC_ACTION_PROGRESS, {
            "event": "action_intermediate",
            "plan_id": self.plan_id,
            "action_id": self.action_id,
            "module_id": self.module_id,
            "action": self.action_name,
            "data": data,
        })

    async def emit_status(self, status: str) -> None:
        """Emit a status change (e.g., 'connecting', 'transferring', 'finalizing').

        Args:
            status: Short status string describing the current phase.
        """
        await self._bus.emit(TOPIC_ACTION_PROGRESS, {
            "event": "action_status",
            "plan_id": self.plan_id,
            "action_id": self.action_id,
            "module_id": self.module_id,
            "action": self.action_name,
            "status": status,
        })
