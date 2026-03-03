"""Tests for modules.service_bus — ServiceBus."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from llmos_bridge.exceptions import ServiceNotFoundError
from llmos_bridge.modules.service_bus import ServiceBus


def _make_provider(module_id: str = "vision", actions: list[str] | None = None) -> MagicMock:
    """Create a mock BaseModule that responds to execute()."""
    provider = MagicMock()
    provider.MODULE_ID = module_id
    provider.execute = AsyncMock(return_value={"status": "ok"})
    # Add _action_* methods for auto-discovery.
    if actions:
        for action in actions:
            setattr(provider, f"_action_{action}", MagicMock())
    return provider


@pytest.mark.unit
class TestServiceBusRegistration:
    def test_register_service(self):
        bus = ServiceBus()
        provider = _make_provider("vision")
        bus.register_service("vision", provider, ["parse_screen", "find_element"])
        assert bus.is_available("vision")

    def test_register_multiple_services(self):
        bus = ServiceBus()
        bus.register_service("vision", _make_provider("vision"), ["parse_screen"])
        bus.register_service("gui", _make_provider("gui"), ["click", "type_text"])
        assert bus.service_count == 2

    def test_register_replaces_existing(self):
        bus = ServiceBus()
        old_provider = _make_provider("vision_v1")
        new_provider = _make_provider("vision_v2")
        bus.register_service("vision", old_provider, ["parse_screen"])
        bus.register_service("vision", new_provider, ["parse_screen"])
        assert bus.service_count == 1
        assert bus.get_provider("vision") is new_provider

    def test_auto_discover_methods(self):
        bus = ServiceBus()
        provider = _make_provider("vision", actions=["parse_screen", "find_element"])
        bus.register_service("vision", provider)
        services = bus.list_services()
        assert len(services) == 1
        assert "parse_screen" in services[0]["methods"]
        assert "find_element" in services[0]["methods"]

    def test_unregister_service(self):
        bus = ServiceBus()
        bus.register_service("vision", _make_provider("vision"), ["parse"])
        assert bus.is_available("vision")
        bus.unregister_service("vision")
        assert not bus.is_available("vision")

    def test_unregister_nonexistent(self):
        bus = ServiceBus()
        bus.unregister_service("nonexistent")  # Should not raise.

    def test_is_available_false(self):
        bus = ServiceBus()
        assert not bus.is_available("nonexistent")


@pytest.mark.unit
class TestServiceBusCall:
    @pytest.mark.asyncio
    async def test_call_routes_through_execute(self):
        bus = ServiceBus()
        provider = _make_provider("vision")
        bus.register_service("vision", provider, ["parse_screen"])

        result = await bus.call("vision", "parse_screen", {"mode": "full"})
        provider.execute.assert_called_once_with("parse_screen", {"mode": "full"})
        assert result == {"status": "ok"}

    @pytest.mark.asyncio
    async def test_call_no_params(self):
        bus = ServiceBus()
        provider = _make_provider("gui")
        bus.register_service("gui", provider, ["click"])

        await bus.call("gui", "click")
        provider.execute.assert_called_once_with("click", {})

    @pytest.mark.asyncio
    async def test_call_nonexistent_service(self):
        bus = ServiceBus()
        with pytest.raises(ServiceNotFoundError) as exc_info:
            await bus.call("nonexistent", "method")
        assert "nonexistent" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_call_propagates_provider_error(self):
        bus = ServiceBus()
        provider = _make_provider("vision")
        provider.execute = AsyncMock(side_effect=RuntimeError("model not loaded"))
        bus.register_service("vision", provider, ["parse_screen"])

        with pytest.raises(RuntimeError, match="model not loaded"):
            await bus.call("vision", "parse_screen")

    @pytest.mark.asyncio
    async def test_call_returns_complex_result(self):
        bus = ServiceBus()
        provider = _make_provider("vision")
        provider.execute = AsyncMock(return_value={
            "elements": [{"id": 1, "type": "button"}],
            "screenshot_hash": "abc123",
        })
        bus.register_service("vision", provider, ["parse_screen"])

        result = await bus.call("vision", "parse_screen", {})
        assert len(result["elements"]) == 1


@pytest.mark.unit
class TestServiceBusListServices:
    def test_list_empty(self):
        bus = ServiceBus()
        assert bus.list_services() == []

    def test_list_with_services(self):
        bus = ServiceBus()
        bus.register_service("vision", _make_provider("vision"), ["parse_screen"], "Vision service")
        bus.register_service("gui", _make_provider("gui"), ["click", "type_text"], "GUI service")

        services = bus.list_services()
        assert len(services) == 2
        names = {s["name"] for s in services}
        assert names == {"vision", "gui"}

    def test_list_includes_description(self):
        bus = ServiceBus()
        bus.register_service("vision", _make_provider(), ["parse"], "Screen parsing")
        services = bus.list_services()
        assert services[0]["description"] == "Screen parsing"


@pytest.mark.unit
class TestServiceBusGetProvider:
    def test_get_existing(self):
        bus = ServiceBus()
        provider = _make_provider("vision")
        bus.register_service("vision", provider, ["parse"])
        assert bus.get_provider("vision") is provider

    def test_get_nonexistent(self):
        bus = ServiceBus()
        assert bus.get_provider("nonexistent") is None


@pytest.mark.unit
class TestServiceBusCount:
    def test_empty(self):
        bus = ServiceBus()
        assert bus.service_count == 0

    def test_after_registrations(self):
        bus = ServiceBus()
        bus.register_service("a", _make_provider("a"), [])
        bus.register_service("b", _make_provider("b"), [])
        assert bus.service_count == 2

    def test_after_unregister(self):
        bus = ServiceBus()
        bus.register_service("a", _make_provider("a"), [])
        bus.unregister_service("a")
        assert bus.service_count == 0
