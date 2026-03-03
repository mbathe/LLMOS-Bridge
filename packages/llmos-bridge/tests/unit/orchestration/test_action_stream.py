"""Unit tests — ActionStream progress streaming.

Tests the ActionStream dataclass that module developers use to emit
progress updates from within @streams_progress-decorated actions.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from llmos_bridge.events.bus import (
    NullEventBus,
    TOPIC_ACTION_PROGRESS,
)
from llmos_bridge.orchestration.stream import ActionStream, _STREAM_KEY


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def bus() -> NullEventBus:
    """NullEventBus with listener support."""
    return NullEventBus()


@pytest_asyncio.fixture
async def stream(bus: NullEventBus) -> ActionStream:
    return ActionStream(
        plan_id="plan-1",
        action_id="a1",
        module_id="api_http",
        action_name="download_file",
        _bus=bus,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestActionStreamEmitProgress:
    async def test_emits_to_correct_topic(
        self, stream: ActionStream, bus: NullEventBus
    ) -> None:
        events: list[dict] = []

        async def _capture(topic: str, event: dict) -> None:
            events.append({"topic": topic, **event})

        bus.register_listener(TOPIC_ACTION_PROGRESS, _capture)
        await stream.emit_progress(42.5, "Downloading chunk 5")

        assert len(events) == 1
        assert events[0]["topic"] == TOPIC_ACTION_PROGRESS
        assert events[0]["event"] == "action_progress"

    async def test_includes_required_fields(
        self, stream: ActionStream, bus: NullEventBus
    ) -> None:
        events: list[dict] = []

        async def _capture(topic: str, event: dict) -> None:
            events.append(event)

        bus.register_listener(TOPIC_ACTION_PROGRESS, _capture)
        await stream.emit_progress(75.0, "Almost there")

        ev = events[0]
        assert ev["plan_id"] == "plan-1"
        assert ev["action_id"] == "a1"
        assert ev["module_id"] == "api_http"
        assert ev["action"] == "download_file"
        assert ev["percent"] == 75.0
        assert ev["message"] == "Almost there"

    async def test_clamps_percent_to_zero(
        self, stream: ActionStream, bus: NullEventBus
    ) -> None:
        events: list[dict] = []

        async def _capture(topic: str, event: dict) -> None:
            events.append(event)

        bus.register_listener(TOPIC_ACTION_PROGRESS, _capture)
        await stream.emit_progress(-10.0)

        assert events[0]["percent"] == 0.0

    async def test_clamps_percent_to_hundred(
        self, stream: ActionStream, bus: NullEventBus
    ) -> None:
        events: list[dict] = []

        async def _capture(topic: str, event: dict) -> None:
            events.append(event)

        bus.register_listener(TOPIC_ACTION_PROGRESS, _capture)
        await stream.emit_progress(150.0)

        assert events[0]["percent"] == 100.0

    async def test_default_message_is_empty(
        self, stream: ActionStream, bus: NullEventBus
    ) -> None:
        events: list[dict] = []

        async def _capture(topic: str, event: dict) -> None:
            events.append(event)

        bus.register_listener(TOPIC_ACTION_PROGRESS, _capture)
        await stream.emit_progress(50.0)

        assert events[0]["message"] == ""


@pytest.mark.unit
class TestActionStreamEmitIntermediate:
    async def test_emits_intermediate_data(
        self, stream: ActionStream, bus: NullEventBus
    ) -> None:
        events: list[dict] = []

        async def _capture(topic: str, event: dict) -> None:
            events.append(event)

        bus.register_listener(TOPIC_ACTION_PROGRESS, _capture)
        await stream.emit_intermediate({"rows_processed": 100, "errors": 0})

        ev = events[0]
        assert ev["event"] == "action_intermediate"
        assert ev["plan_id"] == "plan-1"
        assert ev["data"] == {"rows_processed": 100, "errors": 0}

    async def test_includes_module_and_action(
        self, stream: ActionStream, bus: NullEventBus
    ) -> None:
        events: list[dict] = []

        async def _capture(topic: str, event: dict) -> None:
            events.append(event)

        bus.register_listener(TOPIC_ACTION_PROGRESS, _capture)
        await stream.emit_intermediate({"partial": True})

        assert events[0]["module_id"] == "api_http"
        assert events[0]["action"] == "download_file"


@pytest.mark.unit
class TestActionStreamEmitStatus:
    async def test_emits_status_string(
        self, stream: ActionStream, bus: NullEventBus
    ) -> None:
        events: list[dict] = []

        async def _capture(topic: str, event: dict) -> None:
            events.append(event)

        bus.register_listener(TOPIC_ACTION_PROGRESS, _capture)
        await stream.emit_status("connecting")

        ev = events[0]
        assert ev["event"] == "action_status"
        assert ev["status"] == "connecting"
        assert ev["plan_id"] == "plan-1"
        assert ev["action_id"] == "a1"


@pytest.mark.unit
class TestStreamKey:
    def test_stream_key_value(self) -> None:
        assert _STREAM_KEY == "_stream"
