"""Tests for modules.lifecycle — ModuleLifecycleManager."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from llmos_bridge.events.bus import NullEventBus
from llmos_bridge.exceptions import ModuleLifecycleError
from llmos_bridge.modules.lifecycle import ModuleLifecycleManager
from llmos_bridge.modules.types import ModuleState, ModuleType, SYSTEM_MODULE_IDS


def _make_module(module_id: str = "test_mod") -> MagicMock:
    """Create a mock BaseModule with lifecycle hooks."""
    mod = MagicMock()
    mod.MODULE_ID = module_id
    mod.VERSION = "1.0.0"
    mod.MODULE_TYPE = "user"
    mod.on_start = AsyncMock()
    mod.on_stop = AsyncMock()
    mod.on_pause = AsyncMock()
    mod.on_resume = AsyncMock()
    mod.on_config_update = AsyncMock()
    mod.health_check = AsyncMock(return_value={"status": "ok"})
    return mod


def _make_registry(*modules: MagicMock) -> MagicMock:
    """Create a mock ModuleRegistry containing the given modules."""
    registry = MagicMock()
    module_map = {m.MODULE_ID: m for m in modules}
    registry.get = MagicMock(side_effect=lambda mid: module_map[mid])
    registry.list_available = MagicMock(return_value=list(module_map.keys()))
    return registry


def _make_lifecycle(*modules: MagicMock) -> tuple[ModuleLifecycleManager, MagicMock]:
    """Create a LifecycleManager with mocked dependencies."""
    registry = _make_registry(*modules)
    event_bus = NullEventBus()
    lifecycle = ModuleLifecycleManager(registry, event_bus)
    return lifecycle, registry


@pytest.mark.unit
class TestLifecycleStateQueries:
    def test_initial_state_is_loaded(self):
        mod = _make_module()
        lifecycle, _ = _make_lifecycle(mod)
        assert lifecycle.get_state("test_mod") == ModuleState.LOADED

    def test_get_state_unknown_module(self):
        lifecycle, _ = _make_lifecycle()
        assert lifecycle.get_state("unknown") == ModuleState.LOADED

    def test_get_type_default(self):
        mod = _make_module()
        lifecycle, _ = _make_lifecycle(mod)
        assert lifecycle.get_type("test_mod") == ModuleType.USER

    def test_set_and_get_type(self):
        mod = _make_module()
        lifecycle, _ = _make_lifecycle(mod)
        lifecycle.set_type("test_mod", ModuleType.SYSTEM)
        assert lifecycle.get_type("test_mod") == ModuleType.SYSTEM

    def test_is_system_module_by_id(self):
        lifecycle, _ = _make_lifecycle()
        assert lifecycle.is_system_module("filesystem")
        assert lifecycle.is_system_module("os_exec")

    def test_is_system_module_by_type(self):
        mod = _make_module("custom")
        lifecycle, _ = _make_lifecycle(mod)
        lifecycle.set_type("custom", ModuleType.SYSTEM)
        assert lifecycle.is_system_module("custom")

    def test_not_system_module(self):
        mod = _make_module("browser")
        lifecycle, _ = _make_lifecycle(mod)
        assert not lifecycle.is_system_module("browser")


@pytest.mark.unit
class TestLifecycleStartModule:
    @pytest.mark.asyncio
    async def test_start_from_loaded(self):
        mod = _make_module()
        lifecycle, _ = _make_lifecycle(mod)
        await lifecycle.start_module("test_mod")
        assert lifecycle.get_state("test_mod") == ModuleState.ACTIVE
        mod.on_start.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_from_disabled(self):
        mod = _make_module()
        lifecycle, _ = _make_lifecycle(mod)
        # Simulate a stopped module.
        lifecycle._set_state("test_mod", ModuleState.DISABLED)
        await lifecycle.start_module("test_mod")
        assert lifecycle.get_state("test_mod") == ModuleState.ACTIVE

    @pytest.mark.asyncio
    async def test_start_from_error(self):
        mod = _make_module()
        lifecycle, _ = _make_lifecycle(mod)
        lifecycle._set_state("test_mod", ModuleState.ERROR)
        await lifecycle.start_module("test_mod")
        assert lifecycle.get_state("test_mod") == ModuleState.ACTIVE

    @pytest.mark.asyncio
    async def test_start_failure_transitions_to_error(self):
        mod = _make_module()
        mod.on_start = AsyncMock(side_effect=RuntimeError("model load failed"))
        lifecycle, _ = _make_lifecycle(mod)
        with pytest.raises(RuntimeError, match="model load failed"):
            await lifecycle.start_module("test_mod")
        assert lifecycle.get_state("test_mod") == ModuleState.ERROR

    @pytest.mark.asyncio
    async def test_start_from_active_raises(self):
        mod = _make_module()
        lifecycle, _ = _make_lifecycle(mod)
        await lifecycle.start_module("test_mod")
        with pytest.raises(ModuleLifecycleError):
            await lifecycle.start_module("test_mod")


@pytest.mark.unit
class TestLifecycleStopModule:
    @pytest.mark.asyncio
    async def test_stop_from_active(self):
        mod = _make_module()
        lifecycle, _ = _make_lifecycle(mod)
        await lifecycle.start_module("test_mod")
        await lifecycle.stop_module("test_mod")
        assert lifecycle.get_state("test_mod") == ModuleState.DISABLED
        mod.on_stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_already_disabled(self):
        mod = _make_module()
        lifecycle, _ = _make_lifecycle(mod)
        lifecycle._set_state("test_mod", ModuleState.DISABLED)
        await lifecycle.stop_module("test_mod")  # Should not raise.
        mod.on_stop.assert_not_called()

    @pytest.mark.asyncio
    async def test_stop_from_loaded(self):
        mod = _make_module()
        lifecycle, _ = _make_lifecycle(mod)
        await lifecycle.stop_module("test_mod")  # Never started, should be no-op.
        mod.on_stop.assert_not_called()

    @pytest.mark.asyncio
    async def test_stop_failure_transitions_to_error(self):
        mod = _make_module()
        mod.on_stop = AsyncMock(side_effect=RuntimeError("cleanup failed"))
        lifecycle, _ = _make_lifecycle(mod)
        await lifecycle.start_module("test_mod")
        with pytest.raises(RuntimeError, match="cleanup failed"):
            await lifecycle.stop_module("test_mod")
        assert lifecycle.get_state("test_mod") == ModuleState.ERROR


@pytest.mark.unit
class TestLifecyclePauseResume:
    @pytest.mark.asyncio
    async def test_pause_from_active(self):
        mod = _make_module()
        lifecycle, _ = _make_lifecycle(mod)
        await lifecycle.start_module("test_mod")
        await lifecycle.pause_module("test_mod")
        assert lifecycle.get_state("test_mod") == ModuleState.PAUSED
        mod.on_pause.assert_called_once()

    @pytest.mark.asyncio
    async def test_pause_from_loaded_raises(self):
        mod = _make_module()
        lifecycle, _ = _make_lifecycle(mod)
        with pytest.raises(ModuleLifecycleError):
            await lifecycle.pause_module("test_mod")

    @pytest.mark.asyncio
    async def test_resume_from_paused(self):
        mod = _make_module()
        lifecycle, _ = _make_lifecycle(mod)
        await lifecycle.start_module("test_mod")
        await lifecycle.pause_module("test_mod")
        await lifecycle.resume_module("test_mod")
        assert lifecycle.get_state("test_mod") == ModuleState.ACTIVE
        mod.on_resume.assert_called_once()

    @pytest.mark.asyncio
    async def test_pause_failure_transitions_to_error(self):
        mod = _make_module()
        mod.on_pause = AsyncMock(side_effect=RuntimeError("pause failed"))
        lifecycle, _ = _make_lifecycle(mod)
        await lifecycle.start_module("test_mod")
        with pytest.raises(RuntimeError, match="pause failed"):
            await lifecycle.pause_module("test_mod")
        assert lifecycle.get_state("test_mod") == ModuleState.ERROR


@pytest.mark.unit
class TestLifecycleRestart:
    @pytest.mark.asyncio
    async def test_restart_active_module(self):
        mod = _make_module()
        lifecycle, _ = _make_lifecycle(mod)
        await lifecycle.start_module("test_mod")
        await lifecycle.restart_module("test_mod")
        assert lifecycle.get_state("test_mod") == ModuleState.ACTIVE
        assert mod.on_stop.call_count == 1
        assert mod.on_start.call_count == 2

    @pytest.mark.asyncio
    async def test_restart_loaded_module(self):
        mod = _make_module()
        lifecycle, _ = _make_lifecycle(mod)
        await lifecycle.restart_module("test_mod")
        assert lifecycle.get_state("test_mod") == ModuleState.ACTIVE
        mod.on_stop.assert_not_called()  # Never was active.
        mod.on_start.assert_called_once()


@pytest.mark.unit
class TestLifecycleBatchOperations:
    @pytest.mark.asyncio
    async def test_start_all(self):
        mod_a = _make_module("mod_a")
        mod_b = _make_module("mod_b")
        lifecycle, _ = _make_lifecycle(mod_a, mod_b)
        results = await lifecycle.start_all()
        assert results["mod_a"] == "ok"
        assert results["mod_b"] == "ok"
        assert lifecycle.get_state("mod_a") == ModuleState.ACTIVE
        assert lifecycle.get_state("mod_b") == ModuleState.ACTIVE

    @pytest.mark.asyncio
    async def test_start_all_skips_active(self):
        mod_a = _make_module("mod_a")
        lifecycle, _ = _make_lifecycle(mod_a)
        await lifecycle.start_module("mod_a")
        results = await lifecycle.start_all()
        assert results["mod_a"] == "skipped"

    @pytest.mark.asyncio
    async def test_start_all_handles_failure(self):
        mod_a = _make_module("mod_a")
        mod_b = _make_module("mod_b")
        mod_a.on_start = AsyncMock(side_effect=RuntimeError("fail"))
        lifecycle, _ = _make_lifecycle(mod_a, mod_b)
        results = await lifecycle.start_all()
        assert "fail" in results["mod_a"]
        assert results["mod_b"] == "ok"

    @pytest.mark.asyncio
    async def test_stop_all(self):
        mod_a = _make_module("mod_a")
        mod_b = _make_module("mod_b")
        lifecycle, _ = _make_lifecycle(mod_a, mod_b)
        await lifecycle.start_all()
        await lifecycle.stop_all()
        assert lifecycle.get_state("mod_a") == ModuleState.DISABLED
        assert lifecycle.get_state("mod_b") == ModuleState.DISABLED

    @pytest.mark.asyncio
    async def test_stop_all_handles_failure(self):
        mod_a = _make_module("mod_a")
        mod_b = _make_module("mod_b")
        mod_a.on_stop = AsyncMock(side_effect=RuntimeError("cleanup fail"))
        lifecycle, _ = _make_lifecycle(mod_a, mod_b)
        await lifecycle.start_all()
        # Should not raise — logs warning and continues.
        await lifecycle.stop_all()
        assert lifecycle.get_state("mod_b") == ModuleState.DISABLED


@pytest.mark.unit
class TestLifecycleActionToggle:
    def test_action_enabled_by_default(self):
        lifecycle, _ = _make_lifecycle()
        assert lifecycle.is_action_enabled("any_module", "any_action")

    def test_disable_action(self):
        lifecycle, _ = _make_lifecycle()
        lifecycle.disable_action("browser", "navigate", "under maintenance")
        assert not lifecycle.is_action_enabled("browser", "navigate")

    def test_enable_action(self):
        lifecycle, _ = _make_lifecycle()
        lifecycle.disable_action("browser", "navigate")
        lifecycle.enable_action("browser", "navigate")
        assert lifecycle.is_action_enabled("browser", "navigate")

    def test_get_disabled_actions(self):
        lifecycle, _ = _make_lifecycle()
        lifecycle.disable_action("browser", "navigate", "maintenance")
        lifecycle.disable_action("browser", "screenshot", "security")
        disabled = lifecycle.get_disabled_actions("browser")
        assert disabled == {"navigate": "maintenance", "screenshot": "security"}

    def test_get_disabled_actions_empty(self):
        lifecycle, _ = _make_lifecycle()
        assert lifecycle.get_disabled_actions("browser") == {}

    def test_enable_cleans_up_empty_dict(self):
        lifecycle, _ = _make_lifecycle()
        lifecycle.disable_action("browser", "navigate")
        lifecycle.enable_action("browser", "navigate")
        assert "browser" not in lifecycle._disabled_actions


@pytest.mark.unit
class TestLifecycleConfigUpdate:
    @pytest.mark.asyncio
    async def test_update_config(self):
        mod = _make_module()
        lifecycle, _ = _make_lifecycle(mod)
        await lifecycle.update_config("test_mod", {"timeout": 60})
        mod.on_config_update.assert_called_once_with({"timeout": 60})


@pytest.mark.unit
class TestLifecycleReport:
    @pytest.mark.asyncio
    async def test_full_report(self):
        mod_a = _make_module("mod_a")
        mod_b = _make_module("mod_b")
        lifecycle, _ = _make_lifecycle(mod_a, mod_b)
        lifecycle.set_type("mod_a", ModuleType.SYSTEM)
        await lifecycle.start_module("mod_a")
        lifecycle.disable_action("mod_b", "action_x", "test")

        report = lifecycle.get_full_report()
        assert report["mod_a"]["state"] == "active"
        assert report["mod_a"]["type"] == "system"
        assert report["mod_b"]["state"] == "loaded"
        assert report["mod_b"]["type"] == "user"
        assert report["mod_b"]["disabled_actions"] == {"action_x": "test"}


@pytest.mark.unit
class TestLifecycleStopFromPaused:
    @pytest.mark.asyncio
    async def test_stop_paused_module(self):
        mod = _make_module()
        lifecycle, _ = _make_lifecycle(mod)
        await lifecycle.start_module("test_mod")
        await lifecycle.pause_module("test_mod")
        await lifecycle.stop_module("test_mod")
        assert lifecycle.get_state("test_mod") == ModuleState.DISABLED
        mod.on_stop.assert_called_once()
