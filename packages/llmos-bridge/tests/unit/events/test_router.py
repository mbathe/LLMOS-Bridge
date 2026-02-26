"""Unit tests — events/router.py (EventRouter, topic_matches)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from llmos_bridge.events.bus import NullEventBus
from llmos_bridge.events.router import EventRouter, topic_matches


@pytest.mark.unit
class TestTopicMatches:
    def test_exact_match(self) -> None:
        assert topic_matches("llmos.plans", "llmos.plans") is True
        assert topic_matches("llmos.plans", "llmos.actions") is False

    def test_single_wildcard(self) -> None:
        assert topic_matches("llmos.filesystem.*", "llmos.filesystem.changed") is True
        assert topic_matches("llmos.filesystem.*", "llmos.filesystem") is False
        assert topic_matches("llmos.filesystem.*", "llmos.filesystem.a.b") is False

    def test_multi_wildcard(self) -> None:
        assert topic_matches("llmos.iot.#", "llmos.iot") is True
        assert topic_matches("llmos.iot.#", "llmos.iot.temp") is True
        assert topic_matches("llmos.iot.#", "llmos.iot.sensors.temperature") is True

    def test_catch_all(self) -> None:
        assert topic_matches("#", "any.topic.ever") is True
        assert topic_matches("#", "llmos") is True

    def test_no_match(self) -> None:
        assert topic_matches("llmos.plans", "llmos.actions") is False
        assert topic_matches("llmos.plans.*", "other.plans.x") is False


@pytest.mark.unit
class TestEventRouter:
    def test_add_route_registered(self) -> None:
        router = EventRouter()
        handler = AsyncMock()
        router.add_route("llmos.plans", handler)
        assert router.route_count == 1

    def test_remove_route(self) -> None:
        router = EventRouter()
        handler = AsyncMock()
        router.add_route("llmos.plans", handler)
        router.remove_route("llmos.plans", handler)
        assert router.route_count == 0

    def test_remove_nonexistent_route_is_noop(self) -> None:
        router = EventRouter()
        handler = AsyncMock()
        router.remove_route("llmos.plans", handler)  # should not raise

    async def test_emit_routes_to_matching_handler(self) -> None:
        router = EventRouter()
        handler = AsyncMock()
        router.add_route("llmos.plans", handler)
        await router.emit("llmos.plans", {"event": "plan_submitted"})
        handler.assert_called_once()

    async def test_emit_does_not_route_to_non_matching(self) -> None:
        router = EventRouter()
        handler = AsyncMock()
        router.add_route("llmos.actions", handler)
        await router.emit("llmos.plans", {"event": "plan_submitted"})
        handler.assert_not_called()

    async def test_emit_wildcard_routing(self) -> None:
        router = EventRouter()
        handler = AsyncMock()
        router.add_route("llmos.filesystem.*", handler)
        await router.emit("llmos.filesystem.changed", {"path": "/tmp/test"})
        handler.assert_called_once()

    async def test_emit_routes_to_fallback_when_no_match(self) -> None:
        fallback = MagicMock()
        fallback.emit = AsyncMock()
        router = EventRouter(fallback=fallback)
        await router.emit("llmos.unrouted", {"event": "something"})
        fallback.emit.assert_called_once()

    async def test_emit_does_not_call_fallback_when_matched(self) -> None:
        fallback = MagicMock()
        fallback.emit = AsyncMock()
        router = EventRouter(fallback=fallback)
        handler = AsyncMock()
        router.add_route("llmos.plans", handler)
        await router.emit("llmos.plans", {"event": "plan_submitted"})
        fallback.emit.assert_not_called()

    async def test_handler_error_does_not_propagate(self) -> None:
        router = EventRouter()

        async def bad_handler(topic: str, event: dict) -> None:
            raise RuntimeError("boom")

        router.add_route("llmos.plans", bad_handler)
        # Should not raise
        await router.emit("llmos.plans", {"event": "test"})

    async def test_multiple_handlers_all_called(self) -> None:
        router = EventRouter()
        h1 = AsyncMock()
        h2 = AsyncMock()
        router.add_route("llmos.plans", h1)
        router.add_route("llmos.plans", h2)
        await router.emit("llmos.plans", {"event": "test"})
        h1.assert_called_once()
        h2.assert_called_once()

    async def test_sync_handler_also_works(self) -> None:
        router = EventRouter()
        calls: list[dict] = []

        def sync_handler(topic: str, event: dict) -> None:
            calls.append(event)

        router.add_route("llmos.plans", sync_handler)
        await router.emit("llmos.plans", {"event": "test"})
        assert len(calls) == 1

    async def test_stamp_adds_topic_and_timestamp(self) -> None:
        received: list[dict] = []

        async def handler(topic: str, event: dict) -> None:
            received.append(event)

        router = EventRouter()
        router.add_route("llmos.plans", handler)
        await router.emit("llmos.plans", {"event": "test"})

        assert received[0]["_topic"] == "llmos.plans"
        assert "_timestamp" in received[0]
