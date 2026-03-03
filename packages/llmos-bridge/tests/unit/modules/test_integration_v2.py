"""Integration tests for Module Spec v2 — lifecycle + registry + executor.

Tests that all v2 components work together:
  - LifecycleManager integrates with ModuleRegistry
  - Executor respects lifecycle states
  - ServiceBus integrates with real modules
  - ModuleManagerModule integrates with the full stack
  - ModuleContext wiring
  - Config has module_manager field
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llmos_bridge.config import ModuleManagerConfig, Settings
from llmos_bridge.events.bus import NullEventBus, TOPIC_MODULES
from llmos_bridge.exceptions import (
    ActionDisabledError,
    ActionExecutionError,
    ModuleLifecycleError,
    ServiceNotFoundError,
)
from llmos_bridge.modules.base import BaseModule, Platform
from llmos_bridge.modules.context import ModuleContext
from llmos_bridge.modules.lifecycle import ModuleLifecycleManager
from llmos_bridge.modules.manifest import ModuleManifest, ServiceDescriptor
from llmos_bridge.modules.module_manager import ModuleManagerModule
from llmos_bridge.modules.registry import ModuleRegistry
from llmos_bridge.modules.service_bus import ServiceBus
from llmos_bridge.modules.types import ModuleState, ModuleType, SYSTEM_MODULE_IDS


class _FileModule(BaseModule):
    MODULE_ID = "filesystem"
    VERSION = "1.0.0"
    MODULE_TYPE = "system"
    SUPPORTED_PLATFORMS = [Platform.ALL]

    def get_manifest(self) -> ModuleManifest:
        return ModuleManifest(
            module_id="filesystem", version="1.0.0",
            description="Filesystem", module_type="system",
        )

    def _check_dependencies(self) -> None:
        pass

    async def _action_read_file(self, params: dict) -> dict:
        return {"content": "hello"}


class _BrowserModule(BaseModule):
    MODULE_ID = "browser"
    VERSION = "2.0.0"
    MODULE_TYPE = "user"
    SUPPORTED_PLATFORMS = [Platform.ALL]

    def __init__(self) -> None:
        super().__init__()
        self.stopped = False

    async def on_stop(self) -> None:
        self.stopped = True

    def get_manifest(self) -> ModuleManifest:
        return ModuleManifest(
            module_id="browser", version="2.0.0",
            description="Browser", module_type="user",
        )

    def _check_dependencies(self) -> None:
        pass

    async def _action_navigate(self, params: dict) -> dict:
        return {"url": params.get("url", ""), "status": 200}


def _setup_full_stack():
    """Set up a full v2 stack: registry, lifecycle, service_bus, module_manager."""
    registry = ModuleRegistry()
    registry.register(_FileModule)
    registry.register(_BrowserModule)

    event_bus = NullEventBus()
    service_bus = ServiceBus()
    lifecycle = ModuleLifecycleManager(registry, event_bus, service_bus)
    registry.set_lifecycle_manager(lifecycle)

    # Classify modules.
    lifecycle.set_type("filesystem", ModuleType.SYSTEM)
    lifecycle.set_type("browser", ModuleType.USER)

    # Register ModuleManager.
    mgr = ModuleManagerModule()
    mgr.set_lifecycle_manager(lifecycle)
    mgr.set_service_bus(service_bus)
    registry.register_instance(mgr)
    lifecycle.set_type("module_manager", ModuleType.SYSTEM)

    return registry, lifecycle, service_bus, mgr


# ---------------------------------------------------------------------------
# Registry + Lifecycle integration
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRegistryLifecycleIntegration:
    @pytest.mark.asyncio
    async def test_start_all_activates_modules(self):
        registry, lifecycle, _, _ = _setup_full_stack()
        results = await lifecycle.start_all()
        for mod_id in registry.list_available():
            assert lifecycle.get_state(mod_id) == ModuleState.ACTIVE
        assert all(r == "ok" for r in results.values())

    def test_lifecycle_manager_attached_to_registry(self):
        registry, lifecycle, _, _ = _setup_full_stack()
        assert registry.lifecycle is lifecycle

    @pytest.mark.asyncio
    async def test_stop_all_calls_on_stop(self):
        registry, lifecycle, _, _ = _setup_full_stack()
        await lifecycle.start_all()
        browser = registry.get("browser")
        await lifecycle.stop_all()
        assert browser.stopped is True

    @pytest.mark.asyncio
    async def test_module_types_classified(self):
        _, lifecycle, _, _ = _setup_full_stack()
        assert lifecycle.get_type("filesystem") == ModuleType.SYSTEM
        assert lifecycle.get_type("browser") == ModuleType.USER
        assert lifecycle.get_type("module_manager") == ModuleType.SYSTEM


# ---------------------------------------------------------------------------
# ServiceBus integration with real modules
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestServiceBusIntegration:
    @pytest.mark.asyncio
    async def test_service_call_through_module(self):
        registry, _, service_bus, _ = _setup_full_stack()
        fs_mod = registry.get("filesystem")
        service_bus.register_service("filesystem", fs_mod, ["read_file"])
        result = await service_bus.call("filesystem", "read_file", {"path": "/tmp/test"})
        assert result == {"content": "hello"}

    @pytest.mark.asyncio
    async def test_service_not_found(self):
        _, _, service_bus, _ = _setup_full_stack()
        with pytest.raises(ServiceNotFoundError):
            await service_bus.call("nonexistent", "method")


# ---------------------------------------------------------------------------
# ModuleContext integration
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestModuleContextIntegration:
    @pytest.mark.asyncio
    async def test_context_call_service(self):
        registry, _, service_bus, _ = _setup_full_stack()
        fs_mod = registry.get("filesystem")
        service_bus.register_service("filesystem", fs_mod, ["read_file"])

        ctx = ModuleContext(
            module_id="browser",
            event_bus=NullEventBus(),
            service_bus=service_bus,
            settings=MagicMock(),
        )
        result = await ctx.call_service("filesystem", "read_file", {"path": "/x"})
        assert result == {"content": "hello"}

    def test_context_set_on_module(self):
        registry, _, service_bus, _ = _setup_full_stack()
        browser = registry.get("browser")
        ctx = ModuleContext(
            module_id="browser",
            event_bus=NullEventBus(),
            service_bus=service_bus,
            settings=MagicMock(),
        )
        browser.set_context(ctx)
        assert browser.ctx is ctx
        assert browser.ctx.module_id == "browser"


# ---------------------------------------------------------------------------
# Executor lifecycle state checks
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExecutorLifecycleChecks:
    @pytest.mark.asyncio
    async def test_paused_module_rejected(self):
        registry, lifecycle, _, _ = _setup_full_stack()
        await lifecycle.start_module("browser")
        await lifecycle.pause_module("browser")

        # Simulate executor _dispatch behavior.
        state = lifecycle.get_state("browser")
        assert state == ModuleState.PAUSED

    @pytest.mark.asyncio
    async def test_disabled_module_rejected(self):
        registry, lifecycle, _, _ = _setup_full_stack()
        await lifecycle.start_module("browser")
        await lifecycle.stop_module("browser")
        state = lifecycle.get_state("browser")
        assert state == ModuleState.DISABLED

    @pytest.mark.asyncio
    async def test_disabled_action_rejected(self):
        _, lifecycle, _, _ = _setup_full_stack()
        lifecycle.disable_action("browser", "navigate", "security concern")
        assert not lifecycle.is_action_enabled("browser", "navigate")

    @pytest.mark.asyncio
    async def test_enabled_action_passes(self):
        _, lifecycle, _, _ = _setup_full_stack()
        lifecycle.disable_action("browser", "navigate")
        lifecycle.enable_action("browser", "navigate")
        assert lifecycle.is_action_enabled("browser", "navigate")


# ---------------------------------------------------------------------------
# ModuleManager full stack integration
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestModuleManagerIntegration:
    @pytest.mark.asyncio
    async def test_list_modules_full_stack(self):
        _, _, _, mgr = _setup_full_stack()
        result = await mgr._action_list_modules({})
        ids = {m["module_id"] for m in result["modules"]}
        assert "filesystem" in ids
        assert "browser" in ids
        assert "module_manager" in ids

    @pytest.mark.asyncio
    async def test_system_module_protection(self):
        _, _, _, mgr = _setup_full_stack()
        result = await mgr._action_disable_module({"module_id": "filesystem"})
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_disable_enable_user_module(self):
        _, lifecycle, _, mgr = _setup_full_stack()
        await lifecycle.start_module("browser")
        result = await mgr._action_disable_module({"module_id": "browser"})
        assert result["success"] is True
        result = await mgr._action_enable_module({"module_id": "browser"})
        assert result["success"] is True
        assert result["state"] == "active"

    @pytest.mark.asyncio
    async def test_get_system_status_full_stack(self):
        _, lifecycle, _, mgr = _setup_full_stack()
        await lifecycle.start_all()
        result = await mgr._action_get_system_status({})
        assert result["total_modules"] == 3
        assert result["by_type"]["system"] == 2  # filesystem + module_manager
        assert result["by_type"]["user"] == 1  # browser

    @pytest.mark.asyncio
    async def test_action_toggle_through_manager(self):
        _, lifecycle, _, mgr = _setup_full_stack()
        result = await mgr._action_disable_action({
            "module_id": "browser", "action": "navigate", "reason": "test",
        })
        assert result["enabled"] is False
        assert not lifecycle.is_action_enabled("browser", "navigate")

        result = await mgr._action_enable_action({
            "module_id": "browser", "action": "navigate",
        })
        assert result["enabled"] is True
        assert lifecycle.is_action_enabled("browser", "navigate")

    @pytest.mark.asyncio
    async def test_list_services_through_manager(self):
        registry, _, service_bus, mgr = _setup_full_stack()
        fs_mod = registry.get("filesystem")
        service_bus.register_service("filesystem", fs_mod, ["read_file"])
        result = await mgr._action_list_services({})
        assert result["count"] == 1


# ---------------------------------------------------------------------------
# Config integration
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestConfigIntegration:
    def test_settings_has_module_manager_field(self):
        with patch.object(Path, "exists", return_value=False):
            settings = Settings.load()
        assert hasattr(settings, "module_manager")
        assert isinstance(settings.module_manager, ModuleManagerConfig)
        assert settings.module_manager.enabled is True

    def test_module_manager_config_defaults(self):
        cfg = ModuleManagerConfig()
        assert cfg.enabled is True
        assert cfg.allow_runtime_disable is True
        assert cfg.allow_action_disable is True


# ---------------------------------------------------------------------------
# Manifest v2 fields
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestManifestV2:
    def test_module_type_in_manifest(self):
        fs = _FileModule()
        manifest = fs.get_manifest()
        assert manifest.module_type == "system"

    def test_service_descriptor_in_manifest(self):
        manifest = ModuleManifest(
            module_id="test", version="1.0.0", description="Test",
            provides_services=[
                ServiceDescriptor(name="test_svc", methods=["a", "b"], description="A test"),
            ],
        )
        d = manifest.to_dict()
        assert "provides_services" in d
        assert d["provides_services"][0]["name"] == "test_svc"

    def test_compact_output_omits_defaults(self):
        manifest = ModuleManifest(
            module_id="test", version="1.0.0", description="Test",
        )
        d = manifest.to_dict()
        assert "module_type" not in d  # user is default, omitted
        assert "provides_services" not in d
        assert "consumes_services" not in d


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestV2Exceptions:
    def test_module_lifecycle_error(self):
        err = ModuleLifecycleError("browser", "loaded", "paused")
        assert "browser" in str(err)
        assert "loaded" in str(err)
        assert "paused" in str(err)

    def test_service_not_found_error(self):
        err = ServiceNotFoundError("vision")
        assert "vision" in str(err)

    def test_action_disabled_error(self):
        err = ActionDisabledError("browser", "navigate", "security")
        assert "browser" in str(err)
        assert "navigate" in str(err)

    def test_action_disabled_error_no_reason(self):
        err = ActionDisabledError("browser", "navigate")
        assert "disabled" in str(err)


# ---------------------------------------------------------------------------
# TOPIC_MODULES constant
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTopicModules:
    def test_topic_constant(self):
        assert TOPIC_MODULES == "llmos.modules"

    def test_importable_from_events(self):
        from llmos_bridge.events import TOPIC_MODULES as T
        assert T == "llmos.modules"
