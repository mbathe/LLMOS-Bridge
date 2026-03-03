"""Tests for modules.context — ModuleContext."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from llmos_bridge.modules.context import ModuleContext


def _make_context(module_id: str = "test_mod") -> ModuleContext:
    """Create a ModuleContext with mocked dependencies."""
    event_bus = MagicMock()
    event_bus.emit = AsyncMock()
    service_bus = MagicMock()
    service_bus.call = AsyncMock(return_value={"result": "ok"})
    service_bus.register_service = MagicMock()
    settings = MagicMock()
    return ModuleContext(
        module_id=module_id,
        event_bus=event_bus,
        service_bus=service_bus,
        settings=settings,
    )


@pytest.mark.unit
class TestModuleContextCreation:
    def test_basic_creation(self):
        ctx = _make_context()
        assert ctx.module_id == "test_mod"
        assert ctx.event_bus is not None
        assert ctx.service_bus is not None
        assert ctx.settings is not None

    def test_logger_auto_created(self):
        ctx = _make_context()
        assert ctx.logger is not None

    def test_custom_logger(self):
        custom_logger = MagicMock()
        ctx = ModuleContext(
            module_id="test",
            event_bus=MagicMock(),
            service_bus=MagicMock(),
            settings=MagicMock(),
            logger=custom_logger,
        )
        assert ctx.logger is custom_logger

    def test_kv_store_none_by_default(self):
        ctx = _make_context()
        assert ctx.kv_store is None

    def test_security_manager_none_by_default(self):
        ctx = _make_context()
        assert ctx.security_manager is None

    def test_kv_store_injection(self):
        kv = MagicMock()
        ctx = ModuleContext(
            module_id="test",
            event_bus=MagicMock(),
            service_bus=MagicMock(),
            settings=MagicMock(),
            kv_store=kv,
        )
        assert ctx.kv_store is kv

    def test_security_manager_injection(self):
        sm = MagicMock()
        ctx = ModuleContext(
            module_id="test",
            event_bus=MagicMock(),
            service_bus=MagicMock(),
            settings=MagicMock(),
            security_manager=sm,
        )
        assert ctx.security_manager is sm


@pytest.mark.unit
class TestModuleContextCallService:
    @pytest.mark.asyncio
    async def test_call_service(self):
        ctx = _make_context()
        result = await ctx.call_service("vision", "parse_screen", {"mode": "full"})
        ctx.service_bus.call.assert_called_once_with("vision", "parse_screen", {"mode": "full"})
        assert result == {"result": "ok"}

    @pytest.mark.asyncio
    async def test_call_service_no_params(self):
        ctx = _make_context()
        await ctx.call_service("vision", "parse_screen")
        ctx.service_bus.call.assert_called_once_with("vision", "parse_screen", {})

    @pytest.mark.asyncio
    async def test_call_service_propagates_error(self):
        from llmos_bridge.exceptions import ServiceNotFoundError
        ctx = _make_context()
        ctx.service_bus.call = AsyncMock(side_effect=ServiceNotFoundError("unknown"))
        with pytest.raises(ServiceNotFoundError):
            await ctx.call_service("unknown", "method")


@pytest.mark.unit
class TestModuleContextEmitEvent:
    @pytest.mark.asyncio
    async def test_emit_event(self):
        ctx = _make_context("my_mod")
        await ctx.emit_event("llmos.test", {"event": "something"})
        ctx.event_bus.emit.assert_called_once()
        call_args = ctx.event_bus.emit.call_args
        assert call_args[0][0] == "llmos.test"
        assert call_args[0][1]["module_id"] == "my_mod"
        assert call_args[0][1]["event"] == "something"

    @pytest.mark.asyncio
    async def test_emit_event_does_not_overwrite_module_id(self):
        ctx = _make_context("my_mod")
        await ctx.emit_event("llmos.test", {"module_id": "custom", "event": "x"})
        call_args = ctx.event_bus.emit.call_args
        # setdefault should keep the existing module_id
        assert call_args[0][1]["module_id"] == "custom"


@pytest.mark.unit
class TestModuleContextRegisterService:
    def test_register_service(self):
        ctx = _make_context()
        handler = MagicMock()
        ctx.register_service("my_service", handler, ["method_a", "method_b"], "A service")
        ctx.service_bus.register_service.assert_called_once_with(
            name="my_service",
            provider=handler,
            methods=["method_a", "method_b"],
            description="A service",
        )

    def test_register_service_default_methods(self):
        ctx = _make_context()
        handler = MagicMock()
        ctx.register_service("svc", handler)
        ctx.service_bus.register_service.assert_called_once_with(
            name="svc",
            provider=handler,
            methods=[],
            description="",
        )
