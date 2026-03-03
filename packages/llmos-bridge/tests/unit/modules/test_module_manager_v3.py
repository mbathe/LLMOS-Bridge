"""Tests for ModuleManager v3 actions (hub / package manager)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, PropertyMock

import pytest

from llmos_bridge.modules.module_manager.module import ModuleManagerModule


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def manager():
    mod = ModuleManagerModule()
    mod._lifecycle = MagicMock()
    mod._service_bus = MagicMock()
    return mod


@pytest.fixture()
def mock_installer():
    installer = MagicMock()
    installer.install_from_path = AsyncMock()
    installer.install_from_hub = AsyncMock()
    installer.uninstall = AsyncMock()
    installer.upgrade = AsyncMock()
    installer.verify_module = AsyncMock()
    return installer


@pytest.fixture()
def mock_hub_client():
    return MagicMock()


# ---------------------------------------------------------------------------
# install_module
# ---------------------------------------------------------------------------

class TestInstallModule:
    @pytest.mark.asyncio
    async def test_no_installer(self, manager):
        result = await manager._action_install_module({"source": "hub", "module_id": "x"})
        assert not result["success"]
        assert "not configured" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_install_from_local(self, manager, mock_installer, tmp_path):
        from llmos_bridge.hub.installer import InstallResult
        mock_installer.install_from_path.return_value = InstallResult(
            success=True, module_id="my_mod", version="1.0.0"
        )
        manager._installer = mock_installer
        result = await manager._action_install_module({
            "source": "local",
            "path": str(tmp_path),
        })
        assert result["success"]
        assert result["module_id"] == "my_mod"

    @pytest.mark.asyncio
    async def test_install_from_hub(self, manager, mock_installer, mock_hub_client):
        from llmos_bridge.hub.installer import InstallResult
        mock_installer.install_from_hub.return_value = InstallResult(
            success=True, module_id="hub_mod", version="2.0.0"
        )
        manager._installer = mock_installer
        manager._hub_client = mock_hub_client
        result = await manager._action_install_module({
            "source": "hub",
            "module_id": "hub_mod",
            "version": "latest",
        })
        assert result["success"]
        assert result["module_id"] == "hub_mod"

    @pytest.mark.asyncio
    async def test_install_missing_params(self, manager, mock_installer):
        manager._installer = mock_installer
        result = await manager._action_install_module({"source": "hub"})
        assert not result["success"]


# ---------------------------------------------------------------------------
# uninstall_module
# ---------------------------------------------------------------------------

class TestUninstallModule:
    @pytest.mark.asyncio
    async def test_uninstall(self, manager, mock_installer):
        from llmos_bridge.hub.installer import InstallResult
        mock_installer.uninstall.return_value = InstallResult(
            success=True, module_id="del_mod", version="1.0.0"
        )
        manager._installer = mock_installer
        result = await manager._action_uninstall_module({"module_id": "del_mod"})
        assert result["success"]

    @pytest.mark.asyncio
    async def test_uninstall_no_installer(self, manager):
        result = await manager._action_uninstall_module({"module_id": "x"})
        assert not result["success"]


# ---------------------------------------------------------------------------
# upgrade_module
# ---------------------------------------------------------------------------

class TestUpgradeModule:
    @pytest.mark.asyncio
    async def test_upgrade(self, manager, mock_installer, tmp_path):
        from llmos_bridge.hub.installer import InstallResult
        mock_installer.upgrade.return_value = InstallResult(
            success=True, module_id="upg_mod", version="2.0.0"
        )
        manager._installer = mock_installer
        result = await manager._action_upgrade_module({
            "module_id": "upg_mod",
            "path": str(tmp_path),
        })
        assert result["success"]
        assert result["version"] == "2.0.0"

    @pytest.mark.asyncio
    async def test_upgrade_no_path(self, manager, mock_installer):
        manager._installer = mock_installer
        result = await manager._action_upgrade_module({"module_id": "x"})
        assert not result["success"]


# ---------------------------------------------------------------------------
# search_hub
# ---------------------------------------------------------------------------

class TestSearchHub:
    @pytest.mark.asyncio
    async def test_search(self, manager):
        from llmos_bridge.hub.client import HubModuleInfo
        mock_client = AsyncMock()
        mock_client.search.return_value = [
            HubModuleInfo(module_id="found", version="1.0", description="Desc", author="Auth"),
        ]
        manager._hub_client = mock_client
        result = await manager._action_search_hub({"query": "sensor", "limit": 10})
        assert result["count"] == 1
        assert result["results"][0]["module_id"] == "found"

    @pytest.mark.asyncio
    async def test_search_no_client(self, manager):
        result = await manager._action_search_hub({"query": "test"})
        assert "not configured" in result["error"].lower()


# ---------------------------------------------------------------------------
# list_installed
# ---------------------------------------------------------------------------

class TestListInstalled:
    @pytest.mark.asyncio
    async def test_list(self, manager, mock_installer):
        from llmos_bridge.hub.index import InstalledModule
        mock_index = AsyncMock()
        mock_index.list_all.return_value = [
            InstalledModule(
                module_id="mod_a", version="1.0", install_path="/tmp/a",
                module_class_path="a:A",
            ),
        ]
        mock_installer._index = mock_index
        manager._installer = mock_installer
        result = await manager._action_list_installed({})
        assert result["count"] == 1
        assert result["modules"][0]["module_id"] == "mod_a"

    @pytest.mark.asyncio
    async def test_list_no_installer(self, manager):
        result = await manager._action_list_installed({})
        assert "not configured" in result["error"].lower()


# ---------------------------------------------------------------------------
# verify_module
# ---------------------------------------------------------------------------

class TestVerifyModule:
    @pytest.mark.asyncio
    async def test_verify(self, manager, mock_installer):
        mock_installer.verify_module.return_value = {"verified": True, "module_id": "x"}
        manager._installer = mock_installer
        result = await manager._action_verify_module({"module_id": "x"})
        assert result["verified"]

    @pytest.mark.asyncio
    async def test_verify_no_installer(self, manager):
        result = await manager._action_verify_module({"module_id": "x"})
        assert not result["verified"]


# ---------------------------------------------------------------------------
# describe_module
# ---------------------------------------------------------------------------

class TestDescribeModule:
    @pytest.mark.asyncio
    async def test_describe(self, manager):
        mock_mod = MagicMock()
        mock_mod.describe.return_value = {"module_id": "cool", "status": "ok"}
        mock_registry = MagicMock()
        mock_registry.is_available.return_value = True
        mock_registry.get.return_value = mock_mod
        manager._lifecycle._registry = mock_registry

        result = await manager._action_describe_module({"module_id": "cool"})
        assert result["module_id"] == "cool"
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_describe_not_available(self, manager):
        mock_registry = MagicMock()
        mock_registry.is_available.return_value = False
        manager._lifecycle._registry = mock_registry

        result = await manager._action_describe_module({"module_id": "missing"})
        assert "not available" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_describe_no_lifecycle(self):
        mod = ModuleManagerModule()
        result = await mod._action_describe_module({"module_id": "x"})
        assert "not configured" in result["error"].lower()


# ---------------------------------------------------------------------------
# Manifest includes v3 actions
# ---------------------------------------------------------------------------

class TestManifestV3Actions:
    def test_manifest_has_v3_actions(self, manager):
        manifest = manager.get_manifest()
        action_names = manifest.action_names()
        v3_actions = [
            "install_module", "uninstall_module", "upgrade_module",
            "search_hub", "list_installed", "verify_module", "describe_module",
        ]
        for action in v3_actions:
            assert action in action_names, f"Missing v3 action: {action}"

    def test_manifest_total_actions(self, manager):
        manifest = manager.get_manifest()
        # 15 v2 + 7 v3 = 22
        assert len(manifest.actions) == 22
