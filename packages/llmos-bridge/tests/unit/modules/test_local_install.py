"""Unit tests — Local module installation pipeline.

Tests the full install_from_path() flow in ModuleInstaller, covering:
  - Validation (blocking on issues, passing with warnings)
  - Python dependency venv creation (eager, fail-fast)
  - Module-to-module dependency checks
  - Registry registration with source_path / PYTHONPATH injection
  - Lifecycle hook call
  - Already-installed guard
  - Uninstall + upgrade happy paths
  - REST endpoints (install / installed / uninstall / upgrade)
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llmos_bridge.hub.installer import InstallResult, ModuleInstaller
from llmos_bridge.hub.validator import ValidationResult


# ---------------------------------------------------------------------------
# Helpers — build a minimal valid package directory
# ---------------------------------------------------------------------------

MINIMAL_TOML = """\
[module]
module_id = "test_mod"
version = "1.0.0"
description = "Test module"
module_class_path = "test_mod.module:TestMod"
"""

TOML_WITH_REQUIREMENTS = """\
[module]
module_id = "test_mod"
version = "1.0.0"
description = "Test module"
module_class_path = "test_mod.module:TestMod"
requirements = ["requests>=2.0"]
"""

TOML_WITH_MODULE_DEPS = """\
[module]
module_id = "test_mod"
version = "1.0.0"
description = "Test module"
module_class_path = "test_mod.module:TestMod"

[module.module_dependencies]
filesystem = ">=1.0.0"
"""


def _make_valid_package(tmp_path: Path, toml: str = MINIMAL_TOML) -> Path:
    """Create a minimal valid module directory that passes ModuleValidator."""
    pkg = tmp_path / "my_module"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "llmos-module.toml").write_text(toml)
    (pkg / "module.py").write_text("class TestMod: pass\n")
    (pkg / "params.py").write_text("")
    readme = (
        "# Test Module\n\n"
        "## Overview\nA test module.\n\n"
        "## Actions\nNone.\n\n"
        "## Quick Start\nImport and use.\n\n"
        "## Platform Support\nAll platforms.\n"
    )
    (pkg / "README.md").write_text(readme)
    (pkg / "CHANGELOG.md").write_text("## [1.0.0]\n- Initial release\n")
    docs = pkg / "docs"
    docs.mkdir()
    (docs / "actions.md").write_text("# Actions\n")
    (docs / "integration.md").write_text("# Integration\n")
    return pkg


def _make_installer(
    tmp_path: Path,
    *,
    registry=None,
    venv_manager=None,
    lifecycle=None,
    require_signatures: bool = False,
) -> tuple[ModuleInstaller, MagicMock, MagicMock, AsyncMock]:
    """Return (installer, mock_index, mock_registry, mock_venv_manager).

    When registry / venv_manager are provided they are used as-is (attributes
    are NOT overridden), allowing callers to configure specific behaviours.
    """
    mock_index = MagicMock()
    mock_index.get = AsyncMock(return_value=None)
    mock_index.add = AsyncMock()
    mock_index.remove = AsyncMock()
    mock_index.update_version = AsyncMock()

    if registry is None:
        mock_registry = MagicMock()
        mock_registry.is_available = MagicMock(return_value=True)
        mock_registry.register_isolated = MagicMock()
        mock_registry.unregister = MagicMock()
    else:
        mock_registry = registry

    if venv_manager is None:
        mock_venv = AsyncMock()
        mock_venv.ensure_venv = AsyncMock(return_value="/fake/venv/bin/python")
    else:
        mock_venv = venv_manager

    installer = ModuleInstaller(
        index=mock_index,
        registry=mock_registry,
        venv_manager=mock_venv,
        verifier=None,
        require_signatures=require_signatures,
        install_dir=tmp_path / "install",
        lifecycle_manager=lifecycle,
    )
    return installer, mock_index, mock_registry, mock_venv


# ---------------------------------------------------------------------------
# install_from_path — validation
# ---------------------------------------------------------------------------


class TestInstallValidation:
    @pytest.mark.asyncio
    async def test_missing_toml_fails(self, tmp_path):
        """Directory without llmos-module.toml returns error immediately."""
        empty = tmp_path / "bad_mod"
        empty.mkdir()
        installer, _, _, _ = _make_installer(tmp_path)
        result = await installer.install_from_path(empty)
        assert not result.success
        assert "invalid package" in result.error.lower()

    @pytest.mark.asyncio
    async def test_validation_issues_block_install(self, tmp_path):
        """A module missing module.py is rejected with a clear error."""
        pkg = tmp_path / "no_module"
        pkg.mkdir()
        # Only toml — no module.py, no README
        (pkg / "llmos-module.toml").write_text(MINIMAL_TOML)

        installer, mock_index, _, _ = _make_installer(tmp_path)
        result = await installer.install_from_path(pkg)
        assert not result.success
        assert "validation failed" in result.error.lower()
        # Index must NOT have been written
        mock_index.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_valid_module_installs(self, tmp_path):
        """A fully valid module directory succeeds."""
        pkg = _make_valid_package(tmp_path)
        installer, mock_index, mock_registry, _ = _make_installer(tmp_path)

        result = await installer.install_from_path(pkg)

        assert result.success
        assert result.module_id == "test_mod"
        assert result.version == "1.0.0"
        mock_index.add.assert_called_once()
        mock_registry.register_isolated.assert_called_once()

    @pytest.mark.asyncio
    async def test_warnings_do_not_block_install(self, tmp_path):
        """Warnings (e.g. missing docs) do not prevent installation."""
        pkg = _make_valid_package(tmp_path)
        # Remove docs to generate warnings
        import shutil
        shutil.rmtree(pkg / "docs")

        installer, _, _, _ = _make_installer(tmp_path)
        result = await installer.install_from_path(pkg)

        assert result.success
        assert len(result.validation_warnings) > 0  # warnings present

    @pytest.mark.asyncio
    async def test_already_installed_rejected(self, tmp_path):
        """Installing a module that is already in the index returns an error."""
        from llmos_bridge.hub.index import InstalledModule

        pkg = _make_valid_package(tmp_path)

        existing = InstalledModule(
            module_id="test_mod",
            version="0.9.0",
            install_path=str(pkg),
            module_class_path="test_mod.module:TestMod",
            requirements=[],
            installed_at=time.time(),
            updated_at=time.time(),
        )

        installer, mock_index, _, _ = _make_installer(tmp_path)
        mock_index.get = AsyncMock(return_value=existing)

        result = await installer.install_from_path(pkg)
        assert not result.success
        assert "already installed" in result.error.lower()
        assert "upgrade" in result.error.lower()


# ---------------------------------------------------------------------------
# install_from_path — venv (eager)
# ---------------------------------------------------------------------------


class TestInstallVenv:
    @pytest.mark.asyncio
    async def test_venv_created_eagerly_when_requirements(self, tmp_path):
        """ensure_venv is called during install (not deferred to first execute)."""
        pkg = _make_valid_package(tmp_path, toml=TOML_WITH_REQUIREMENTS)
        installer, _, _, mock_venv = _make_installer(tmp_path)

        result = await installer.install_from_path(pkg)

        assert result.success
        mock_venv.ensure_venv.assert_called_once_with("test_mod", ["requests>=2.0"])

    @pytest.mark.asyncio
    async def test_venv_not_called_when_no_requirements(self, tmp_path):
        """No venv is created if requirements = []."""
        pkg = _make_valid_package(tmp_path)  # no requirements in MINIMAL_TOML
        installer, _, _, mock_venv = _make_installer(tmp_path)

        result = await installer.install_from_path(pkg)

        assert result.success
        mock_venv.ensure_venv.assert_not_called()

    @pytest.mark.asyncio
    async def test_venv_failure_aborts_install(self, tmp_path):
        """If pip install fails, install is aborted and index is not written."""
        pkg = _make_valid_package(tmp_path, toml=TOML_WITH_REQUIREMENTS)
        mock_venv = AsyncMock()
        mock_venv.ensure_venv = AsyncMock(side_effect=RuntimeError("pip failed"))
        installer, mock_index, _, _ = _make_installer(tmp_path, venv_manager=mock_venv)

        result = await installer.install_from_path(pkg)

        assert not result.success
        assert "Python dependencies" in result.error
        mock_index.add.assert_not_called()


# ---------------------------------------------------------------------------
# install_from_path — source_path / PYTHONPATH injection
# ---------------------------------------------------------------------------


class TestInstallSourcePath:
    @pytest.mark.asyncio
    async def test_register_isolated_called_with_source_path(self, tmp_path):
        """register_isolated receives the package directory as source_path."""
        pkg = _make_valid_package(tmp_path)
        installer, _, mock_registry, _ = _make_installer(tmp_path)

        await installer.install_from_path(pkg)

        call_kwargs = mock_registry.register_isolated.call_args.kwargs
        assert call_kwargs["source_path"] == pkg

    @pytest.mark.asyncio
    async def test_registry_builds_pythonpath_from_source_path(self, tmp_path):
        """ModuleRegistry.register_isolated sets PYTHONPATH = source_path in env_vars."""
        from llmos_bridge.modules.registry import ModuleRegistry

        real_registry = MagicMock(spec=ModuleRegistry)
        captured: dict = {}

        def fake_register_isolated(**kwargs):
            captured.update(kwargs)

        real_registry.register_isolated = fake_register_isolated
        real_registry.is_available = MagicMock(return_value=True)
        real_registry.register_instance = MagicMock()

        # Test the registry itself (unit)
        import os
        from pathlib import Path as P

        src = P(tmp_path) / "some_module"
        src.mkdir()

        # Use real registry to check PYTHONPATH logic
        real_reg = ModuleRegistry()
        with patch("llmos_bridge.isolation.proxy.IsolatedModuleProxy") as MockProxy:
            MockProxy.return_value = MagicMock()
            MockProxy.return_value.MODULE_ID = "x"
            MockProxy.return_value.VERSION = "1.0"
            real_reg.register_isolated(
                module_id="x",
                module_class_path="x.module:X",
                venv_manager=MagicMock(),
                source_path=src,
            )
            init_kwargs = MockProxy.call_args.kwargs
            env = init_kwargs["env_vars"]
            assert env is not None
            assert str(src) in env["PYTHONPATH"]


# ---------------------------------------------------------------------------
# install_from_path — module-to-module dependencies
# ---------------------------------------------------------------------------


class TestInstallModuleDeps:
    @pytest.mark.asyncio
    async def test_missing_module_dep_blocks_install(self, tmp_path):
        """Install fails if a required module is not in the registry."""
        pkg = _make_valid_package(tmp_path, toml=TOML_WITH_MODULE_DEPS)
        mock_reg = MagicMock()
        mock_reg.is_available = MagicMock(return_value=False)  # filesystem missing
        mock_reg.register_isolated = MagicMock()

        installer, mock_index, _, _ = _make_installer(tmp_path, registry=mock_reg)
        result = await installer.install_from_path(pkg)

        assert not result.success
        assert "filesystem" in result.error
        mock_index.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_satisfied_module_dep_passes(self, tmp_path):
        """Install succeeds when all module dependencies are available."""
        pkg = _make_valid_package(tmp_path, toml=TOML_WITH_MODULE_DEPS)
        mock_reg = MagicMock()
        mock_reg.is_available = MagicMock(return_value=True)  # filesystem present
        mock_reg.register_isolated = MagicMock()
        mock_reg.register_instance = MagicMock()

        installer, _, _, _ = _make_installer(tmp_path, registry=mock_reg)
        result = await installer.install_from_path(pkg)

        assert result.success


# ---------------------------------------------------------------------------
# install_from_path — lifecycle hook
# ---------------------------------------------------------------------------


class TestInstallLifecycleHook:
    @pytest.mark.asyncio
    async def test_lifecycle_install_hook_called(self, tmp_path):
        """on_install() lifecycle hook is called after successful registration."""
        pkg = _make_valid_package(tmp_path)
        mock_lifecycle = MagicMock()
        mock_lifecycle.install_module = AsyncMock()

        installer, _, _, _ = _make_installer(tmp_path, lifecycle=mock_lifecycle)
        result = await installer.install_from_path(pkg)

        assert result.success
        mock_lifecycle.install_module.assert_called_once_with("test_mod")

    @pytest.mark.asyncio
    async def test_lifecycle_hook_failure_does_not_abort(self, tmp_path):
        """A failing on_install() hook does not roll back the installation."""
        pkg = _make_valid_package(tmp_path)
        mock_lifecycle = MagicMock()
        mock_lifecycle.install_module = AsyncMock(side_effect=RuntimeError("hook error"))

        installer, mock_index, _, _ = _make_installer(tmp_path, lifecycle=mock_lifecycle)
        result = await installer.install_from_path(pkg)

        assert result.success  # install completed despite hook failure
        mock_index.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_lifecycle_no_crash(self, tmp_path):
        """When lifecycle_manager=None, install still succeeds."""
        pkg = _make_valid_package(tmp_path)
        installer, _, _, _ = _make_installer(tmp_path, lifecycle=None)
        result = await installer.install_from_path(pkg)
        assert result.success


# ---------------------------------------------------------------------------
# uninstall
# ---------------------------------------------------------------------------


class TestUninstall:
    @pytest.mark.asyncio
    async def test_uninstall_success(self, tmp_path):
        from llmos_bridge.hub.index import InstalledModule

        existing = InstalledModule(
            module_id="test_mod",
            version="1.0.0",
            install_path="/some/path",
            module_class_path="test_mod.module:TestMod",
            requirements=[],
            installed_at=time.time(),
            updated_at=time.time(),
        )
        installer, mock_index, mock_registry, _ = _make_installer(tmp_path)
        mock_index.get = AsyncMock(return_value=existing)

        result = await installer.uninstall("test_mod")

        assert result.success
        assert result.module_id == "test_mod"
        mock_registry.unregister.assert_called_once_with("test_mod")
        mock_index.remove.assert_called_once_with("test_mod")

    @pytest.mark.asyncio
    async def test_uninstall_not_found(self, tmp_path):
        installer, mock_index, _, _ = _make_installer(tmp_path)
        mock_index.get = AsyncMock(return_value=None)

        result = await installer.uninstall("nonexistent")

        assert not result.success
        assert "not installed" in result.error.lower()

    @pytest.mark.asyncio
    async def test_uninstall_registry_error_ignored(self, tmp_path):
        """Even if unregister() raises, uninstall removes index entry."""
        from llmos_bridge.hub.index import InstalledModule

        existing = InstalledModule(
            module_id="test_mod",
            version="1.0.0",
            install_path="/some/path",
            module_class_path="test_mod.module:TestMod",
            requirements=[],
            installed_at=time.time(),
            updated_at=time.time(),
        )
        installer, mock_index, mock_registry, _ = _make_installer(tmp_path)
        mock_index.get = AsyncMock(return_value=existing)
        mock_registry.unregister = MagicMock(side_effect=RuntimeError("already gone"))

        result = await installer.uninstall("test_mod")
        assert result.success
        mock_index.remove.assert_called_once()


# ---------------------------------------------------------------------------
# upgrade
# ---------------------------------------------------------------------------


class TestUpgrade:
    @pytest.mark.asyncio
    async def test_upgrade_success(self, tmp_path):
        from llmos_bridge.hub.index import InstalledModule

        old = InstalledModule(
            module_id="test_mod",
            version="1.0.0",
            install_path="/old/path",
            module_class_path="test_mod.module:TestMod",
            requirements=[],
            installed_at=time.time(),
            updated_at=time.time(),
        )

        pkg_v2 = _make_valid_package(tmp_path / "v2")
        # Patch toml to version 2.0.0
        toml_v2 = MINIMAL_TOML.replace("1.0.0", "2.0.0")
        (pkg_v2 / "llmos-module.toml").write_text(toml_v2)

        installer, mock_index, mock_registry, _ = _make_installer(tmp_path)
        mock_index.get = AsyncMock(return_value=old)

        result = await installer.upgrade("test_mod", pkg_v2)

        assert result.success
        assert result.version == "2.0.0"
        mock_index.update_version.assert_called_once()
        mock_registry.unregister.assert_called_once_with("test_mod")
        mock_registry.register_isolated.assert_called_once()

    @pytest.mark.asyncio
    async def test_upgrade_not_installed(self, tmp_path):
        """Upgrading a module that doesn't exist returns an error."""
        pkg = _make_valid_package(tmp_path)
        installer, mock_index, _, _ = _make_installer(tmp_path)
        mock_index.get = AsyncMock(return_value=None)

        result = await installer.upgrade("ghost_mod", pkg)
        assert not result.success
        assert "not installed" in result.error.lower()

    @pytest.mark.asyncio
    async def test_upgrade_invalid_package(self, tmp_path):
        """Upgrading to a directory without llmos-module.toml fails."""
        from llmos_bridge.hub.index import InstalledModule

        existing = InstalledModule(
            module_id="test_mod",
            version="1.0.0",
            install_path="/old/path",
            module_class_path="test_mod.module:TestMod",
            requirements=[],
            installed_at=time.time(),
            updated_at=time.time(),
        )
        empty = tmp_path / "empty_v2"
        empty.mkdir()
        installer, mock_index, _, _ = _make_installer(tmp_path)
        mock_index.get = AsyncMock(return_value=existing)

        result = await installer.upgrade("test_mod", empty)
        assert not result.success
        assert "invalid package" in result.error.lower()


# ---------------------------------------------------------------------------
# set_lifecycle_manager injection
# ---------------------------------------------------------------------------


class TestLifecycleInjection:
    def test_set_lifecycle_manager(self, tmp_path):
        installer, _, _, _ = _make_installer(tmp_path)
        assert installer._lifecycle is None
        lm = MagicMock()
        installer.set_lifecycle_manager(lm)
        assert installer._lifecycle is lm
