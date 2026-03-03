"""Tests for modules.base — Module Spec v2 additions.

Tests the new lifecycle hooks, MODULE_TYPE, context injection, and
introspection methods added to BaseModule.  Verifies that all new
features have default no-op implementations (backwards compatibility).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from llmos_bridge.modules.base import BaseModule, Platform
from llmos_bridge.modules.manifest import ModuleManifest


class _DummyModule(BaseModule):
    MODULE_ID = "dummy"
    VERSION = "1.0.0"
    SUPPORTED_PLATFORMS = [Platform.ALL]

    def get_manifest(self) -> ModuleManifest:
        return ModuleManifest(module_id="dummy", version="1.0.0", description="Test")

    def _check_dependencies(self) -> None:
        pass


class _SystemModule(BaseModule):
    MODULE_ID = "sys_mod"
    VERSION = "1.0.0"
    MODULE_TYPE = "system"
    SUPPORTED_PLATFORMS = [Platform.ALL]

    def get_manifest(self) -> ModuleManifest:
        return ModuleManifest(module_id="sys_mod", version="1.0.0", description="System")

    def _check_dependencies(self) -> None:
        pass


@pytest.mark.unit
class TestModuleTypeAttribute:
    def test_default_is_user(self):
        mod = _DummyModule()
        assert mod.MODULE_TYPE == "user"

    def test_system_type(self):
        mod = _SystemModule()
        assert mod.MODULE_TYPE == "system"


@pytest.mark.unit
class TestContextInjection:
    def test_ctx_none_by_default(self):
        mod = _DummyModule()
        assert mod.ctx is None

    def test_set_context(self):
        mod = _DummyModule()
        ctx = MagicMock()
        mod.set_context(ctx)
        assert mod.ctx is ctx

    def test_set_context_replaces(self):
        mod = _DummyModule()
        ctx1 = MagicMock()
        ctx2 = MagicMock()
        mod.set_context(ctx1)
        mod.set_context(ctx2)
        assert mod.ctx is ctx2


@pytest.mark.unit
class TestLifecycleHooksDefaults:
    @pytest.mark.asyncio
    async def test_on_start_default(self):
        mod = _DummyModule()
        result = await mod.on_start()
        assert result is None

    @pytest.mark.asyncio
    async def test_on_stop_default(self):
        mod = _DummyModule()
        result = await mod.on_stop()
        assert result is None

    @pytest.mark.asyncio
    async def test_on_pause_default(self):
        mod = _DummyModule()
        result = await mod.on_pause()
        assert result is None

    @pytest.mark.asyncio
    async def test_on_resume_default(self):
        mod = _DummyModule()
        result = await mod.on_resume()
        assert result is None

    @pytest.mark.asyncio
    async def test_on_config_update_default(self):
        mod = _DummyModule()
        result = await mod.on_config_update({"key": "value"})
        assert result is None


@pytest.mark.unit
class TestIntrospectionDefaults:
    @pytest.mark.asyncio
    async def test_health_check_default(self):
        mod = _DummyModule()
        result = await mod.health_check()
        assert result["status"] == "ok"
        assert result["module_id"] == "dummy"
        assert result["version"] == "1.0.0"

    def test_metrics_default(self):
        mod = _DummyModule()
        assert mod.metrics() == {}

    def test_state_snapshot_default(self):
        mod = _DummyModule()
        assert mod.state_snapshot() == {}


@pytest.mark.unit
class TestRegisterServicesDefault:
    def test_returns_empty_list(self):
        mod = _DummyModule()
        assert mod.register_services() == []


@pytest.mark.unit
class TestBackwardsCompatibility:
    def test_existing_execute_still_works(self):
        """The execute method should still dispatch to _action_* methods."""
        mod = _DummyModule()
        # Ensure no new attributes break existing dispatch.
        handler = mod._get_handler  # Should still work.
        assert callable(handler)

    def test_security_injection_still_works(self):
        mod = _DummyModule()
        sec = MagicMock()
        mod.set_security(sec)
        assert mod._security is sec

    def test_ctx_and_security_coexist(self):
        mod = _DummyModule()
        ctx = MagicMock()
        sec = MagicMock()
        mod.set_context(ctx)
        mod.set_security(sec)
        assert mod.ctx is ctx
        assert mod._security is sec
