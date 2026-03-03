"""Tests for hub.installer — ModuleInstaller (mocked registry + index)."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llmos_bridge.hub.index import InstalledModule, ModuleIndex
from llmos_bridge.hub.installer import InstallResult, ModuleInstaller


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_package(tmp_path: Path, module_id: str = "test_mod", version: str = "1.0.0") -> Path:
    """Create a module package directory that passes ModuleValidator.

    Includes all required files: llmos-module.toml, module.py, README.md (with
    required sections), CHANGELOG.md, params.py, and docs/.
    """
    pkg = tmp_path / module_id
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "llmos-module.toml").write_text(
        f'[module]\n'
        f'module_id = "{module_id}"\n'
        f'version = "{version}"\n'
        f'module_class_path = "{module_id}.module:TestMod"\n'
    )
    (pkg / "module.py").write_text("class TestMod: pass\n")
    (pkg / "params.py").write_text("")
    (pkg / "README.md").write_text(
        f"# {module_id}\n\n"
        "## Overview\nTest module.\n\n"
        "## Actions\nNone.\n\n"
        "## Quick Start\nInstall and import.\n\n"
        "## Platform Support\nAll platforms.\n"
    )
    (pkg / "CHANGELOG.md").write_text(f"## [{version}]\n- Initial release\n")
    docs = pkg / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "actions.md").write_text("# Actions\n")
    (docs / "integration.md").write_text("# Integration\n")
    return pkg


@pytest.fixture()
async def index(tmp_path):
    idx = ModuleIndex(tmp_path / "installer_test.db")
    await idx.init()
    yield idx
    await idx.close()


@pytest.fixture()
def registry():
    reg = MagicMock()
    reg.register_isolated = MagicMock()
    reg.unregister = MagicMock()
    return reg


@pytest.fixture()
def venv_manager():
    return MagicMock()


@pytest.fixture()
def installer(index, registry, venv_manager, tmp_path):
    return ModuleInstaller(
        index=index,
        registry=registry,
        venv_manager=venv_manager,
        verifier=None,
        require_signatures=False,
        install_dir=tmp_path / "install",
    )


# ---------------------------------------------------------------------------
# InstallResult
# ---------------------------------------------------------------------------

class TestInstallResult:
    def test_success_result(self):
        r = InstallResult(success=True, module_id="x", version="1.0.0")
        assert r.success
        assert r.error == ""

    def test_failure_result(self):
        r = InstallResult(success=False, module_id="x", error="Something went wrong")
        assert not r.success
        assert "Something went wrong" in r.error


# ---------------------------------------------------------------------------
# install_from_path
# ---------------------------------------------------------------------------

class TestInstallFromPath:
    @pytest.mark.asyncio
    async def test_install_success(self, installer, tmp_path, index):
        pkg = _create_package(tmp_path, "new_mod")
        result = await installer.install_from_path(pkg)
        assert result.success
        assert result.module_id == "new_mod"
        assert result.version == "1.0.0"

        # Verify it's in the index.
        stored = await index.get("new_mod")
        assert stored is not None
        assert stored.version == "1.0.0"

    @pytest.mark.asyncio
    async def test_install_already_exists(self, installer, tmp_path, index):
        pkg = _create_package(tmp_path, "dup_mod")
        await installer.install_from_path(pkg)
        result = await installer.install_from_path(pkg)
        assert not result.success
        assert "already installed" in result.error.lower()

    @pytest.mark.asyncio
    async def test_install_invalid_package(self, installer, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        result = await installer.install_from_path(empty)
        assert not result.success
        assert "invalid package" in result.error.lower()

    @pytest.mark.asyncio
    async def test_install_registry_failure_rollback(self, installer, tmp_path, index, registry):
        pkg = _create_package(tmp_path, "fail_mod")
        registry.register_isolated.side_effect = RuntimeError("Registration boom")
        result = await installer.install_from_path(pkg)
        assert not result.success
        assert "registration failed" in result.error.lower()
        # Index should have been rolled back.
        assert await index.get("fail_mod") is None


# ---------------------------------------------------------------------------
# uninstall
# ---------------------------------------------------------------------------

class TestUninstall:
    @pytest.mark.asyncio
    async def test_uninstall_success(self, installer, tmp_path, index):
        pkg = _create_package(tmp_path, "del_mod")
        await installer.install_from_path(pkg)
        result = await installer.uninstall("del_mod")
        assert result.success
        assert await index.get("del_mod") is None

    @pytest.mark.asyncio
    async def test_uninstall_not_installed(self, installer):
        result = await installer.uninstall("nonexistent")
        assert not result.success
        assert "not installed" in result.error.lower()


# ---------------------------------------------------------------------------
# upgrade
# ---------------------------------------------------------------------------

class TestUpgrade:
    @pytest.mark.asyncio
    async def test_upgrade_success(self, installer, tmp_path, index):
        pkg_v1 = _create_package(tmp_path, "upg_mod", "1.0.0")
        await installer.install_from_path(pkg_v1)

        # Create valid v2 package.
        pkg_v2 = _create_package(tmp_path / "v2", "upg_mod", "2.0.0")
        result = await installer.upgrade("upg_mod", pkg_v2)
        assert result.success
        assert result.version == "2.0.0"

        stored = await index.get("upg_mod")
        assert stored.version == "2.0.0"

    @pytest.mark.asyncio
    async def test_upgrade_not_installed(self, installer, tmp_path):
        pkg = _create_package(tmp_path, "nope")
        result = await installer.upgrade("nope", pkg)
        assert not result.success
        assert "not installed" in result.error.lower()


# ---------------------------------------------------------------------------
# verify_module
# ---------------------------------------------------------------------------

class TestVerifyModule:
    @pytest.mark.asyncio
    async def test_verify_not_installed(self, installer):
        result = await installer.verify_module("nonexistent")
        assert not result["verified"]

    @pytest.mark.asyncio
    async def test_verify_no_verifier(self, installer, tmp_path, index):
        pkg = _create_package(tmp_path, "ver_mod")
        await installer.install_from_path(pkg)
        result = await installer.verify_module("ver_mod")
        assert not result["verified"]
        assert "verifier" in result["error"].lower()
