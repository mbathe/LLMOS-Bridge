"""Tests for Module Spec v3 — Event auto-subscription system.

Tests the callback-based event listener mechanism on EventBus and
the automatic subscription/unsubscription in ModuleLifecycleManager.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llmos_bridge.events.bus import (
    EventBus,
    FanoutEventBus,
    LogEventBus,
    NullEventBus,
    TOPIC_ACTIONS,
    TOPIC_MODULES,
    TOPIC_SECURITY,
)
from llmos_bridge.modules.base import BaseModule, Platform
from llmos_bridge.modules.manifest import ActionSpec, ModuleManifest


# ---------------------------------------------------------------------------
# Test module that subscribes to events
# ---------------------------------------------------------------------------

class EventAwareModule(BaseModule):
    MODULE_ID = "event_aware"
    VERSION = "1.0.0"
    SUPPORTED_PLATFORMS = [Platform.ALL]

    def __init__(self):
        super().__init__()
        self.received_events: list[tuple[str, dict]] = []

    async def on_event(self, topic: str, event: dict[str, Any]) -> None:
        self.received_events.append((topic, event))

    def get_manifest(self) -> ModuleManifest:
        return ModuleManifest(
            module_id=self.MODULE_ID,
            version=self.VERSION,
            description="Module that subscribes to events",
            subscribes_events=[TOPIC_SECURITY, TOPIC_ACTIONS],
            actions=[
                ActionSpec(name="noop", description="Does nothing"),
            ],
        )


class SilentModule(BaseModule):
    MODULE_ID = "silent"
    VERSION = "1.0.0"

    def get_manifest(self) -> ModuleManifest:
        return ModuleManifest(
            module_id=self.MODULE_ID,
            version=self.VERSION,
            description="Module with no event subscriptions",
        )


# ---------------------------------------------------------------------------
# EventBus listener tests
# ---------------------------------------------------------------------------


class TestEventBusListeners:
    @pytest.mark.asyncio
    async def test_register_listener_called_on_emit(self):
        bus = NullEventBus()
        received = []

        async def listener(topic: str, event: dict) -> None:
            received.append((topic, event))

        bus.register_listener(TOPIC_SECURITY, listener)
        await bus.emit(TOPIC_SECURITY, {"event": "test"})

        assert len(received) == 1
        assert received[0][0] == TOPIC_SECURITY
        assert received[0][1]["event"] == "test"

    @pytest.mark.asyncio
    async def test_unregister_listener(self):
        bus = NullEventBus()
        received = []

        async def listener(topic: str, event: dict) -> None:
            received.append(event)

        bus.register_listener(TOPIC_ACTIONS, listener)
        bus.unregister_listener(TOPIC_ACTIONS, listener)
        await bus.emit(TOPIC_ACTIONS, {"event": "invisible"})

        assert len(received) == 0

    @pytest.mark.asyncio
    async def test_unregister_all_listeners(self):
        bus = NullEventBus()
        received = []

        async def listener(topic: str, event: dict) -> None:
            received.append(event)

        bus.register_listener(TOPIC_ACTIONS, listener)
        bus.register_listener(TOPIC_SECURITY, listener)
        bus.unregister_all_listeners(listener)

        await bus.emit(TOPIC_ACTIONS, {"event": "1"})
        await bus.emit(TOPIC_SECURITY, {"event": "2"})

        assert len(received) == 0

    @pytest.mark.asyncio
    async def test_multiple_listeners_same_topic(self):
        bus = NullEventBus()
        results_a = []
        results_b = []

        async def listener_a(topic: str, event: dict) -> None:
            results_a.append(event)

        async def listener_b(topic: str, event: dict) -> None:
            results_b.append(event)

        bus.register_listener(TOPIC_ACTIONS, listener_a)
        bus.register_listener(TOPIC_ACTIONS, listener_b)
        await bus.emit(TOPIC_ACTIONS, {"event": "shared"})

        assert len(results_a) == 1
        assert len(results_b) == 1

    @pytest.mark.asyncio
    async def test_listener_on_wrong_topic_not_called(self):
        bus = NullEventBus()
        received = []

        async def listener(topic: str, event: dict) -> None:
            received.append(event)

        bus.register_listener(TOPIC_SECURITY, listener)
        await bus.emit(TOPIC_ACTIONS, {"event": "other_topic"})

        assert len(received) == 0

    @pytest.mark.asyncio
    async def test_listener_error_does_not_propagate(self):
        bus = NullEventBus()

        async def bad_listener(topic: str, event: dict) -> None:
            raise RuntimeError("Listener crash!")

        bus.register_listener(TOPIC_ACTIONS, bad_listener)
        # Should not raise.
        await bus.emit(TOPIC_ACTIONS, {"event": "test"})

    @pytest.mark.asyncio
    async def test_duplicate_register_ignored(self):
        bus = NullEventBus()
        count = 0

        async def listener(topic: str, event: dict) -> None:
            nonlocal count
            count += 1

        bus.register_listener(TOPIC_ACTIONS, listener)
        bus.register_listener(TOPIC_ACTIONS, listener)  # Duplicate
        await bus.emit(TOPIC_ACTIONS, {"event": "test"})

        assert count == 1  # Called only once

    @pytest.mark.asyncio
    async def test_unregister_nonexistent_no_error(self):
        bus = NullEventBus()

        async def listener(topic: str, event: dict) -> None:
            pass

        # Should not raise.
        bus.unregister_listener(TOPIC_ACTIONS, listener)
        bus.unregister_all_listeners(listener)

    @pytest.mark.asyncio
    async def test_log_event_bus_dispatches_to_listeners(self, tmp_path):
        bus = LogEventBus(tmp_path / "events.ndjson")
        received = []

        async def listener(topic: str, event: dict) -> None:
            received.append(event)

        bus.register_listener(TOPIC_ACTIONS, listener)
        await bus.emit(TOPIC_ACTIONS, {"event": "test"})

        assert len(received) == 1
        # Also verify the file was written.
        assert (tmp_path / "events.ndjson").exists()

    @pytest.mark.asyncio
    async def test_fanout_event_bus_dispatches_to_listeners(self):
        inner = NullEventBus()
        outer = FanoutEventBus([inner])
        received = []

        async def listener(topic: str, event: dict) -> None:
            received.append(event)

        outer.register_listener(TOPIC_ACTIONS, listener)
        await outer.emit(TOPIC_ACTIONS, {"event": "test"})

        assert len(received) == 1


# ---------------------------------------------------------------------------
# Lifecycle auto-subscription tests
# ---------------------------------------------------------------------------


class TestLifecycleEventSubscription:
    @pytest.mark.asyncio
    async def test_auto_subscribe_on_start(self):
        from llmos_bridge.modules.lifecycle import ModuleLifecycleManager
        from llmos_bridge.modules.registry import ModuleRegistry

        bus = NullEventBus()
        registry = ModuleRegistry()
        module = EventAwareModule()
        registry.register_instance(module)

        lifecycle = ModuleLifecycleManager(registry, bus)
        await lifecycle.start_module("event_aware")

        # Verify subscriptions were registered.
        assert "event_aware" in lifecycle._event_subscriptions
        assert len(lifecycle._event_subscriptions["event_aware"]) == 2  # security + actions

    @pytest.mark.asyncio
    async def test_events_reach_module_after_subscribe(self):
        from llmos_bridge.modules.lifecycle import ModuleLifecycleManager
        from llmos_bridge.modules.registry import ModuleRegistry

        bus = NullEventBus()
        registry = ModuleRegistry()
        module = EventAwareModule()
        registry.register_instance(module)

        lifecycle = ModuleLifecycleManager(registry, bus)
        await lifecycle.start_module("event_aware")

        # Emit events to subscribed topics.
        await bus.emit(TOPIC_SECURITY, {"event": "security_alert"})
        await bus.emit(TOPIC_ACTIONS, {"event": "action_started"})

        assert len(module.received_events) == 2
        assert module.received_events[0][0] == TOPIC_SECURITY
        assert module.received_events[1][0] == TOPIC_ACTIONS

    @pytest.mark.asyncio
    async def test_auto_unsubscribe_on_stop(self):
        from llmos_bridge.modules.lifecycle import ModuleLifecycleManager
        from llmos_bridge.modules.registry import ModuleRegistry

        bus = NullEventBus()
        registry = ModuleRegistry()
        module = EventAwareModule()
        registry.register_instance(module)

        lifecycle = ModuleLifecycleManager(registry, bus)
        await lifecycle.start_module("event_aware")
        await lifecycle.stop_module("event_aware")

        # Verify subscriptions were removed.
        assert "event_aware" not in lifecycle._event_subscriptions

        # Emit after unsubscribe — module should NOT receive.
        module.received_events.clear()
        await bus.emit(TOPIC_SECURITY, {"event": "after_stop"})
        assert len(module.received_events) == 0

    @pytest.mark.asyncio
    async def test_no_subscribe_for_module_without_events(self):
        from llmos_bridge.modules.lifecycle import ModuleLifecycleManager
        from llmos_bridge.modules.registry import ModuleRegistry

        bus = NullEventBus()
        registry = ModuleRegistry()
        module = SilentModule()
        registry.register_instance(module)

        lifecycle = ModuleLifecycleManager(registry, bus)
        await lifecycle.start_module("silent")

        assert "silent" not in lifecycle._event_subscriptions

    @pytest.mark.asyncio
    async def test_events_on_unsubscribed_topic_not_received(self):
        from llmos_bridge.modules.lifecycle import ModuleLifecycleManager
        from llmos_bridge.modules.registry import ModuleRegistry

        bus = NullEventBus()
        registry = ModuleRegistry()
        module = EventAwareModule()
        registry.register_instance(module)

        lifecycle = ModuleLifecycleManager(registry, bus)
        await lifecycle.start_module("event_aware")

        # Module subscribes to SECURITY and ACTIONS, not MODULES.
        await bus.emit(TOPIC_MODULES, {"event": "module_started"})
        assert len(module.received_events) == 0
