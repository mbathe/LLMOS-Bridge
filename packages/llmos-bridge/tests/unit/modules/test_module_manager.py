"""Tests for modules.module_manager — ModuleManagerModule (15 actions)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from llmos_bridge.events.bus import NullEventBus
from llmos_bridge.modules.base import BaseModule, Platform
from llmos_bridge.modules.lifecycle import ModuleLifecycleManager
from llmos_bridge.modules.manifest import ModuleManifest
from llmos_bridge.modules.module_manager.module import ModuleManagerModule
from llmos_bridge.modules.registry import ModuleRegistry
from llmos_bridge.modules.service_bus import ServiceBus
from llmos_bridge.modules.types import ModuleState, ModuleType


class _DummyModule(BaseModule):
    MODULE_ID = "dummy"
    VERSION = "1.0.0"
    SUPPORTED_PLATFORMS = [Platform.ALL]

    def get_manifest(self) -> ModuleManifest:
        return ModuleManifest(
            module_id="dummy", version="1.0.0", description="Dummy",
            actions=[],
        )

    def _check_dependencies(self) -> None:
        pass


class _BrowserModule(BaseModule):
    MODULE_ID = "browser"
    VERSION = "2.0.0"
    MODULE_TYPE = "user"
    SUPPORTED_PLATFORMS = [Platform.ALL]

    def get_manifest(self) -> ModuleManifest:
        return ModuleManifest(
            module_id="browser", version="2.0.0", description="Browser module",
            actions=[],
        )

    def _check_dependencies(self) -> None:
        pass


def _setup() -> tuple[ModuleManagerModule, ModuleLifecycleManager, ModuleRegistry, ServiceBus]:
    """Set up a ModuleManager with a full lifecycle stack."""
    registry = ModuleRegistry()
    registry.register(_DummyModule)
    registry.register(_BrowserModule)

    event_bus = NullEventBus()
    service_bus = ServiceBus()
    lifecycle = ModuleLifecycleManager(registry, event_bus, service_bus)
    lifecycle.set_type("dummy", ModuleType.SYSTEM)
    lifecycle.set_type("browser", ModuleType.USER)

    mgr = ModuleManagerModule()
    mgr.set_lifecycle_manager(lifecycle)
    mgr.set_service_bus(service_bus)

    return mgr, lifecycle, registry, service_bus


# ---------------------------------------------------------------------------
# Module listing and inspection
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListModules:
    @pytest.mark.asyncio
    async def test_list_all(self):
        mgr, _, _, _ = _setup()
        result = await mgr._action_list_modules({})
        assert result["count"] == 2
        ids = {m["module_id"] for m in result["modules"]}
        assert ids == {"dummy", "browser"}

    @pytest.mark.asyncio
    async def test_filter_by_type(self):
        mgr, _, _, _ = _setup()
        result = await mgr._action_list_modules({"module_type": "system"})
        assert result["count"] == 1
        assert result["modules"][0]["module_id"] == "dummy"

    @pytest.mark.asyncio
    async def test_filter_by_state(self):
        mgr, lifecycle, _, _ = _setup()
        await lifecycle.start_module("dummy")
        result = await mgr._action_list_modules({"state": "active"})
        assert result["count"] == 1
        assert result["modules"][0]["module_id"] == "dummy"

    @pytest.mark.asyncio
    async def test_include_health(self):
        mgr, lifecycle, _, _ = _setup()
        await lifecycle.start_module("dummy")
        result = await mgr._action_list_modules({"include_health": True})
        active_mods = [m for m in result["modules"] if m["state"] == "active"]
        assert len(active_mods) == 1
        assert active_mods[0]["health"]["status"] == "ok"

    @pytest.mark.asyncio
    async def test_no_lifecycle(self):
        mgr = ModuleManagerModule()
        result = await mgr._action_list_modules({})
        assert "error" in result


@pytest.mark.unit
class TestGetModuleInfo:
    @pytest.mark.asyncio
    async def test_basic_info(self):
        mgr, _, _, _ = _setup()
        result = await mgr._action_get_module_info({"module_id": "dummy"})
        assert result["module_id"] == "dummy"
        assert result["version"] == "1.0.0"
        assert result["type"] == "system"
        assert result["state"] == "loaded"

    @pytest.mark.asyncio
    async def test_include_health(self):
        mgr, lifecycle, _, _ = _setup()
        await lifecycle.start_module("browser")
        result = await mgr._action_get_module_info({
            "module_id": "browser",
            "include_health": True,
        })
        assert result["health"]["status"] == "ok"

    @pytest.mark.asyncio
    async def test_include_metrics(self):
        mgr, _, _, _ = _setup()
        result = await mgr._action_get_module_info({
            "module_id": "dummy",
            "include_metrics": True,
        })
        assert "metrics" in result

    @pytest.mark.asyncio
    async def test_unavailable_module(self):
        mgr, _, _, _ = _setup()
        result = await mgr._action_get_module_info({"module_id": "nonexistent"})
        assert "error" in result


# ---------------------------------------------------------------------------
# Lifecycle management
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEnableModule:
    @pytest.mark.asyncio
    async def test_enable(self):
        mgr, _, _, _ = _setup()
        result = await mgr._action_enable_module({"module_id": "browser"})
        assert result["success"] is True
        assert result["state"] == "active"

    @pytest.mark.asyncio
    async def test_enable_already_active(self):
        mgr, lifecycle, _, _ = _setup()
        await lifecycle.start_module("browser")
        result = await mgr._action_enable_module({"module_id": "browser"})
        assert result["success"] is False
        assert "error" in result


@pytest.mark.unit
class TestDisableModule:
    @pytest.mark.asyncio
    async def test_disable_user_module(self):
        mgr, lifecycle, _, _ = _setup()
        await lifecycle.start_module("browser")
        result = await mgr._action_disable_module({"module_id": "browser"})
        assert result["success"] is True
        assert result["state"] == "disabled"

    @pytest.mark.asyncio
    async def test_disable_system_module_rejected(self):
        mgr, _, _, _ = _setup()
        result = await mgr._action_disable_module({"module_id": "dummy"})
        assert result["success"] is False
        assert "system module" in result["error"]

    @pytest.mark.asyncio
    async def test_disable_by_system_id(self):
        """Modules in SYSTEM_MODULE_IDS are protected regardless of set_type."""
        mgr, _, _, _ = _setup()
        result = await mgr._action_disable_module({"module_id": "filesystem"})
        assert result["success"] is False


@pytest.mark.unit
class TestPauseModule:
    @pytest.mark.asyncio
    async def test_pause(self):
        mgr, lifecycle, _, _ = _setup()
        await lifecycle.start_module("browser")
        result = await mgr._action_pause_module({"module_id": "browser"})
        assert result["success"] is True
        assert result["state"] == "paused"

    @pytest.mark.asyncio
    async def test_pause_not_active(self):
        mgr, _, _, _ = _setup()
        result = await mgr._action_pause_module({"module_id": "browser"})
        assert result["success"] is False


@pytest.mark.unit
class TestResumeModule:
    @pytest.mark.asyncio
    async def test_resume(self):
        mgr, lifecycle, _, _ = _setup()
        await lifecycle.start_module("browser")
        await lifecycle.pause_module("browser")
        result = await mgr._action_resume_module({"module_id": "browser"})
        assert result["success"] is True
        assert result["state"] == "active"


@pytest.mark.unit
class TestRestartModule:
    @pytest.mark.asyncio
    async def test_restart(self):
        mgr, lifecycle, _, _ = _setup()
        await lifecycle.start_module("browser")
        result = await mgr._action_restart_module({"module_id": "browser"})
        assert result["success"] is True
        assert result["state"] == "active"

    @pytest.mark.asyncio
    async def test_restart_from_loaded(self):
        mgr, _, _, _ = _setup()
        result = await mgr._action_restart_module({"module_id": "browser"})
        assert result["success"] is True


# ---------------------------------------------------------------------------
# Action toggles
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestActionToggle:
    @pytest.mark.asyncio
    async def test_disable_action(self):
        mgr, _, _, _ = _setup()
        result = await mgr._action_disable_action({
            "module_id": "browser", "action": "navigate", "reason": "security",
        })
        assert result["enabled"] is False
        assert result["reason"] == "security"

    @pytest.mark.asyncio
    async def test_enable_action(self):
        mgr, lifecycle, _, _ = _setup()
        lifecycle.disable_action("browser", "navigate")
        result = await mgr._action_enable_action({
            "module_id": "browser", "action": "navigate",
        })
        assert result["enabled"] is True


# ---------------------------------------------------------------------------
# Introspection
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetModuleHealth:
    @pytest.mark.asyncio
    async def test_health(self):
        mgr, _, _, _ = _setup()
        result = await mgr._action_get_module_health({"module_id": "dummy"})
        assert result["status"] == "ok"
        assert result["module_id"] == "dummy"


@pytest.mark.unit
class TestGetModuleMetrics:
    @pytest.mark.asyncio
    async def test_metrics(self):
        mgr, _, _, _ = _setup()
        result = await mgr._action_get_module_metrics({"module_id": "dummy"})
        assert result["module_id"] == "dummy"
        assert "metrics" in result


@pytest.mark.unit
class TestGetModuleState:
    @pytest.mark.asyncio
    async def test_state_snapshot(self):
        mgr, _, _, _ = _setup()
        result = await mgr._action_get_module_state({"module_id": "dummy"})
        assert result["module_id"] == "dummy"
        assert "state_snapshot" in result


@pytest.mark.unit
class TestListServices:
    @pytest.mark.asyncio
    async def test_empty(self):
        mgr, _, _, _ = _setup()
        result = await mgr._action_list_services({})
        assert result["count"] == 0

    @pytest.mark.asyncio
    async def test_with_services(self):
        mgr, _, registry, service_bus = _setup()
        provider = registry.get("dummy")
        service_bus.register_service("dummy_svc", provider, ["action_a"])
        result = await mgr._action_list_services({})
        assert result["count"] == 1
        assert result["services"][0]["name"] == "dummy_svc"

    @pytest.mark.asyncio
    async def test_no_service_bus(self):
        mgr = ModuleManagerModule()
        result = await mgr._action_list_services({})
        assert "error" in result


@pytest.mark.unit
class TestGetSystemStatus:
    @pytest.mark.asyncio
    async def test_basic(self):
        mgr, lifecycle, _, _ = _setup()
        await lifecycle.start_module("dummy")
        result = await mgr._action_get_system_status({})
        assert result["total_modules"] == 2
        assert result["by_state"]["active"] == 1
        assert result["by_state"]["loaded"] == 1
        assert result["by_type"]["system"] == 1
        assert result["by_type"]["user"] == 1

    @pytest.mark.asyncio
    async def test_with_health(self):
        mgr, lifecycle, _, _ = _setup()
        await lifecycle.start_module("dummy")
        result = await mgr._action_get_system_status({"include_health": True})
        assert "health" in result
        assert result["health"]["dummy"]["status"] == "ok"

    @pytest.mark.asyncio
    async def test_includes_service_count(self):
        mgr, _, _, _ = _setup()
        result = await mgr._action_get_system_status({})
        assert result["service_count"] == 0


@pytest.mark.unit
class TestUpdateModuleConfig:
    @pytest.mark.asyncio
    async def test_update(self):
        mgr, _, _, _ = _setup()
        result = await mgr._action_update_module_config({
            "module_id": "dummy", "config": {"timeout": 60},
        })
        assert result["success"] is True


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestModuleManagerManifest:
    def test_manifest(self):
        mgr = ModuleManagerModule()
        manifest = mgr.get_manifest()
        assert manifest.module_id == "module_manager"
        assert manifest.module_type == "system"
        assert len(manifest.actions) == 22  # 15 v2 + 7 v3

    def test_all_action_names(self):
        mgr = ModuleManagerModule()
        manifest = mgr.get_manifest()
        names = manifest.action_names()
        expected = [
            "list_modules", "get_module_info", "enable_module", "disable_module",
            "pause_module", "resume_module", "restart_module",
            "enable_action", "disable_action",
            "get_module_health", "get_module_metrics", "get_module_state",
            "list_services", "get_system_status", "update_module_config",
            # v3 Hub actions
            "install_module", "uninstall_module", "upgrade_module",
            "search_hub", "list_installed", "verify_module", "describe_module",
        ]
        assert names == expected

    def test_module_type_is_system(self):
        mgr = ModuleManagerModule()
        assert mgr.MODULE_TYPE == "system"

    def test_module_id(self):
        mgr = ModuleManagerModule()
        assert mgr.MODULE_ID == "module_manager"
