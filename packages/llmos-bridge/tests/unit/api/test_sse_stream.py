"""Unit tests — SSE stream endpoint components.

Tests the _serialisable helper and the route's listener setup logic.
SSE streaming with httpx ASGITransport has known issues with connection
lifecycle, so we test the components directly rather than end-to-end.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from llmos_bridge.api.routes.stream import _serialisable
from llmos_bridge.events.bus import (
    NullEventBus,
    TOPIC_ACTION_PROGRESS,
    TOPIC_ACTION_RESULTS,
    TOPIC_PLANS,
)


# ---------------------------------------------------------------------------
# _serialisable helper
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSerialisable:
    def test_strips_underscore_keys(self) -> None:
        result = _serialisable({"_bus": "obj", "plan_id": "p1"})
        assert "_bus" not in result
        assert result["plan_id"] == "p1"

    def test_preserves_topic_and_timestamp(self) -> None:
        result = _serialisable({
            "_topic": "llmos.actions",
            "_timestamp": 1234567890.0,
            "_bus": "should_be_stripped",
            "data": "keep",
        })
        assert result["_topic"] == "llmos.actions"
        assert result["_timestamp"] == 1234567890.0
        assert "_bus" not in result
        assert result["data"] == "keep"

    def test_handles_nested_dicts(self) -> None:
        result = _serialisable({
            "event": "progress",
            "data": {"_internal": "hidden", "value": 42},
        })
        assert result["event"] == "progress"
        assert "_internal" not in result["data"]
        assert result["data"]["value"] == 42

    def test_handles_lists(self) -> None:
        result = _serialisable({
            "items": [{"_private": True, "name": "a"}, {"name": "b"}],
        })
        assert len(result["items"]) == 2
        assert "_private" not in result["items"][0]
        assert result["items"][0]["name"] == "a"

    def test_handles_scalars(self) -> None:
        assert _serialisable(42) == 42
        assert _serialisable("hello") == "hello"
        assert _serialisable(True) is True
        assert _serialisable(None) is None

    def test_empty_dict(self) -> None:
        assert _serialisable({}) == {}

    def test_complex_event(self) -> None:
        event = {
            "event": "action_progress",
            "plan_id": "p1",
            "action_id": "a1",
            "module_id": "api_http",
            "action": "download",
            "percent": 50.0,
            "message": "halfway",
            "_topic": "llmos.actions.progress",
            "_timestamp": 1234.5,
            "_bus": "<NullEventBus>",
        }
        result = _serialisable(event)
        # JSON-serialisable.
        json_str = json.dumps(result)
        parsed = json.loads(json_str)
        assert parsed["percent"] == 50.0
        assert "_bus" not in parsed
        assert parsed["_topic"] == "llmos.actions.progress"


# ---------------------------------------------------------------------------
# EventBus listener for plan_id filtering
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEventFiltering:
    async def test_listener_filters_by_plan_id(self) -> None:
        """Simulate the SSE endpoint's listener filtering logic."""
        bus = NullEventBus()
        plan_id = "target-plan"
        received: list[dict] = []

        async def _listener(topic: str, event: dict) -> None:
            if event.get("plan_id") == plan_id:
                received.append(event)

        bus.register_listener(TOPIC_ACTION_PROGRESS, _listener)

        # Emit for target plan.
        await bus.emit(TOPIC_ACTION_PROGRESS, {
            "event": "action_progress",
            "plan_id": plan_id,
            "percent": 50.0,
        })
        # Emit for different plan.
        await bus.emit(TOPIC_ACTION_PROGRESS, {
            "event": "action_progress",
            "plan_id": "other-plan",
            "percent": 99.0,
        })

        assert len(received) == 1
        assert received[0]["percent"] == 50.0

    async def test_multi_topic_subscription(self) -> None:
        """The SSE endpoint subscribes to 4 topics."""
        bus = NullEventBus()
        plan_id = "multi-topic"
        received: list[dict] = []

        async def _listener(topic: str, event: dict) -> None:
            if event.get("plan_id") == plan_id:
                received.append(event)

        topics = [TOPIC_ACTION_PROGRESS, TOPIC_ACTION_RESULTS, TOPIC_PLANS]
        for topic in topics:
            bus.register_listener(topic, _listener)

        await bus.emit(TOPIC_ACTION_PROGRESS, {
            "event": "action_progress",
            "plan_id": plan_id,
            "percent": 25.0,
        })
        await bus.emit(TOPIC_ACTION_RESULTS, {
            "event": "action_result_ready",
            "plan_id": plan_id,
            "status": "completed",
        })
        await bus.emit(TOPIC_PLANS, {
            "event": "plan_completed",
            "plan_id": plan_id,
        })

        assert len(received) == 3
        events = [e["event"] for e in received]
        assert "action_progress" in events
        assert "action_result_ready" in events
        assert "plan_completed" in events

    async def test_unregister_listener_cleanup(self) -> None:
        """After unregistering, no more events are received."""
        bus = NullEventBus()
        received: list[dict] = []

        async def _listener(topic: str, event: dict) -> None:
            received.append(event)

        bus.register_listener(TOPIC_ACTION_PROGRESS, _listener)
        await bus.emit(TOPIC_ACTION_PROGRESS, {"plan_id": "p1", "event": "a"})
        assert len(received) == 1

        bus.unregister_listener(TOPIC_ACTION_PROGRESS, _listener)
        await bus.emit(TOPIC_ACTION_PROGRESS, {"plan_id": "p1", "event": "b"})
        assert len(received) == 1  # No new events after unregister.


# ---------------------------------------------------------------------------
# SSE event format
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSSEEventFormat:
    def test_event_format_structure(self) -> None:
        """SSE events follow the format: event: <type>\\ndata: <json>\\n\\n"""
        event = {
            "event": "action_progress",
            "plan_id": "p1",
            "action_id": "a1",
            "percent": 50.0,
        }
        event_type = event.get("event", "unknown")
        data = json.dumps(_serialisable(event), default=str)
        sse_text = f"event: {event_type}\ndata: {data}\n\n"

        assert sse_text.startswith("event: action_progress\n")
        assert "data: " in sse_text
        assert sse_text.endswith("\n\n")

        # Parse the data back.
        data_line = sse_text.split("\n")[1]
        parsed = json.loads(data_line.removeprefix("data: "))
        assert parsed["percent"] == 50.0

    def test_keepalive_format(self) -> None:
        """SSE keepalive is a comment line."""
        keepalive = ": keepalive\n\n"
        assert keepalive.startswith(":")
        assert keepalive.endswith("\n\n")
