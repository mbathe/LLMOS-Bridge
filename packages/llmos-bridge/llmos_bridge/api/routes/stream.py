"""API layer — Server-Sent Events endpoint for real-time plan streaming.

Provides ``GET /plans/{plan_id}/stream`` — an SSE endpoint that streams
action events (started, progress, intermediate, status, result, plan
completion) in real time.  This replaces HTTP polling for agents that
support SSE.

Event types:
  - ``action_started``       — action execution begins
  - ``action_progress``      — progress update (percent, message)
  - ``action_intermediate``  — partial data from action
  - ``action_status``        — status change within action
  - ``action_result_ready``  — action completed with result
  - ``plan_completed``       — plan finished
  - ``plan_failed``          — plan failed

Usage::

    curl -N http://localhost:8741/plans/{plan_id}/stream
    # => event: action_progress
    # => data: {"plan_id":"...", "percent": 50, "message": "Downloading..."}
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from llmos_bridge.events.bus import (
    TOPIC_ACTIONS,
    TOPIC_ACTION_PROGRESS,
    TOPIC_ACTION_RESULTS,
    TOPIC_PLANS,
    EventBus,
)

router = APIRouter(tags=["streaming"])


@router.get("/plans/{plan_id}/stream")
async def stream_plan_events(plan_id: str, request: Request) -> StreamingResponse:
    """SSE endpoint streaming real-time events for a specific plan.

    The client receives events as they occur.  A keepalive comment is sent
    every 30 seconds to prevent connection timeout.  The stream ends when
    the client disconnects.
    """
    bus: EventBus = request.app.state.event_bus
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def _listener(topic: str, event: dict[str, Any]) -> None:
        if event.get("plan_id") == plan_id:
            await queue.put(event)

    # Subscribe to all relevant topics.
    topics = [TOPIC_ACTIONS, TOPIC_ACTION_PROGRESS, TOPIC_ACTION_RESULTS, TOPIC_PLANS]
    for topic in topics:
        bus.register_listener(topic, _listener)

    async def _event_generator():  # noqa: C901
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    event_type = event.get("event", "unknown")
                    # Serialise — skip non-serialisable fields like ActionStream.
                    data = json.dumps(
                        _serialisable(event), default=str, ensure_ascii=False
                    )
                    yield f"event: {event_type}\ndata: {data}\n\n"
                except asyncio.TimeoutError:
                    # SSE keepalive comment.
                    yield ": keepalive\n\n"
        finally:
            for topic in topics:
                bus.unregister_listener(topic, _listener)

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


def _serialisable(obj: Any) -> Any:
    """Recursively strip non-serialisable values from an event dict."""
    if isinstance(obj, dict):
        return {
            k: _serialisable(v)
            for k, v in obj.items()
            if not k.startswith("_") or k in ("_topic", "_timestamp")
        }
    if isinstance(obj, (list, tuple)):
        return [_serialisable(v) for v in obj]
    return obj
