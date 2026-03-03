"""Tests for hub.installer — source scanning integration."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llmos_bridge.hub.index import InstalledModule, ModuleIndex
from llmos_bridge.hub.installer import InstallResult, ModuleInstaller
from llmos_bridge.hub.source_scanner import SourceCodeScanner, SourceScanResult, SourceScanFinding
from llmos_bridge.security.scanners.base import ScanVerdict


@pytest.fixture()
async def index(tmp_path):
    idx = ModuleIndex(tmp_path / "test.db")
    await idx.init()
    yield idx
    await idx.close()


@pytest.fixture()
def registry():
    r = MagicMock()
    r.is_available.return_value = False
    r.register_isolated = MagicMock()
    r.get = MagicMock()
    r.get.return_value = AsyncMock()
    r.get.return_value.start = AsyncMock()
    return r


@pytest.fixture()
def venv_manager():
    vm = MagicMock()
    vm.ensure_venv = AsyncMock()
    vm.remove_venv = AsyncMock()
    return vm


def _create_package(tmp_path: Path, module_id: str = "test_mod", version: str = "1.0.0", extra_py: str = "") -> Path:
    """Create a minimal valid module package directory that passes ModuleValidator."""
    pkg = tmp_path / "pkg"
    pkg.mkdir(exist_ok=True)
    (pkg / "llmos-module.toml").write_text(f"""
[module]
module_id = "{module_id}"
version = "{version}"
description = "Test module"
module_class_path = "{module_id}.module:TestModule"
sandbox_level = "basic"
platforms = ["linux"]
""")
    (pkg / "__init__.py").write_text("")
    py_content = """
class TestModule:
    pass
"""
    if extra_py:
        py_content += extra_py
    (pkg / "module.py").write_text(py_content)
    # README.md with required sections (validator requires this)
    (pkg / "README.md").write_text(
        "# Test Module\n\n"
        "## Overview\nA test module.\n\n"
        "## Actions\nTest actions.\n\n"
        "## Quick Start\nRun a test.\n\n"
        "## Platform Support\nLinux\n"
    )
    return pkg


class TestInstallWithCleanModule:
    @pytest.mark.asyncio
    async def test_clean_module_install_succeeds(self, index, registry, venv_manager, tmp_path):
        pkg = _create_package(tmp_path)
        installer = ModuleInstaller(
            index=index,
            registry=registry,
            venv_manager=venv_manager,
            install_dir=tmp_path / "installed",
        )
        result = await installer.install_from_path(pkg)
        assert result.success is True
        assert result.scan_score >= 70
        assert result.trust_tier != ""
        assert result.scan_findings_count == 0

    @pytest.mark.asyncio
    async def test_clean_module_stored_in_index(self, index, registry, venv_manager, tmp_path):
        pkg = _create_package(tmp_path)
        installer = ModuleInstaller(
            index=index,
            registry=registry,
            venv_manager=venv_manager,
            install_dir=tmp_path / "installed",
        )
        await installer.install_from_path(pkg)
        mod = await index.get("test_mod")
        assert mod is not None
        assert mod.trust_tier != ""
        assert mod.scan_score >= 0
        assert mod.checksum != ""


class TestInstallWithDangerousModule:
    @pytest.mark.asyncio
    async def test_dangerous_module_rejected(self, index, registry, venv_manager, tmp_path):
        extra = """
import os
os.system("rm -rf /")
eval(user_input)
exec(base64.b64decode(payload))
subprocess.call("cmd", shell=True)
marshal.loads(data)
pickle.loads(data)
ctypes.CDLL("libevil.so")
os.popen("whoami")
"""
        pkg = _create_package(tmp_path, extra_py=extra)
        installer = ModuleInstaller(
            index=index,
            registry=registry,
            venv_manager=venv_manager,
            install_dir=tmp_path / "installed",
        )
        result = await installer.install_from_path(pkg)
        assert result.success is False
        assert "REJECTED" in result.error
        assert result.scan_score < 30
        assert result.scan_findings_count > 0

    @pytest.mark.asyncio
    async def test_rejected_module_not_in_index(self, index, registry, venv_manager, tmp_path):
        extra = """
import os
os.system("rm -rf /")
eval(user_input)
exec(base64.b64decode(payload))
subprocess.call("cmd", shell=True)
marshal.loads(data)
pickle.loads(data)
ctypes.CDLL("libevil.so")
os.popen("whoami")
"""
        pkg = _create_package(tmp_path, extra_py=extra)
        installer = ModuleInstaller(
            index=index,
            registry=registry,
            venv_manager=venv_manager,
            install_dir=tmp_path / "installed",
        )
        await installer.install_from_path(pkg)
        assert await index.get("test_mod") is None


class TestInstallWithWarnings:
    @pytest.mark.asyncio
    async def test_warning_module_installs_with_findings(self, index, registry, venv_manager, tmp_path):
        extra = '\nimport requests\nrequests.get("http://example.com")\n'
        pkg = _create_package(tmp_path, extra_py=extra)
        installer = ModuleInstaller(
            index=index,
            registry=registry,
            venv_manager=venv_manager,
            install_dir=tmp_path / "installed",
        )
        result = await installer.install_from_path(pkg)
        # Low-severity finding (requests.get = 0.3) should still allow install
        assert result.success is True
        assert result.scan_score > 0


class TestScanDisabled:
    @pytest.mark.asyncio
    async def test_scanning_disabled(self, index, registry, venv_manager, tmp_path):
        extra = '\nos.system("rm -rf /")\n'
        pkg = _create_package(tmp_path, extra_py=extra)
        installer = ModuleInstaller(
            index=index,
            registry=registry,
            venv_manager=venv_manager,
            install_dir=tmp_path / "installed",
            source_scan_enabled=False,
        )
        result = await installer.install_from_path(pkg)
        # Should succeed even with dangerous code because scanning is disabled
        assert result.success is True
        assert result.scan_score == -1.0


class TestCustomScanner:
    @pytest.mark.asyncio
    async def test_custom_scanner_injected(self, index, registry, venv_manager, tmp_path):
        pkg = _create_package(tmp_path)
        mock_scanner = AsyncMock(spec=SourceCodeScanner)
        mock_scanner.scan_directory = AsyncMock(return_value=SourceScanResult(
            verdict=ScanVerdict.ALLOW,
            score=100.0,
            findings=[],
            files_scanned=2,
            scan_duration_ms=1.0,
        ))
        installer = ModuleInstaller(
            index=index,
            registry=registry,
            venv_manager=venv_manager,
            install_dir=tmp_path / "installed",
            source_scanner=mock_scanner,
        )
        result = await installer.install_from_path(pkg)
        assert result.success is True
        mock_scanner.scan_directory.assert_called_once()


class TestUpgradeScanning:
    @pytest.mark.asyncio
    async def test_upgrade_runs_scan(self, index, registry, venv_manager, tmp_path):
        # First install
        pkg = _create_package(tmp_path)
        installer = ModuleInstaller(
            index=index,
            registry=registry,
            venv_manager=venv_manager,
            install_dir=tmp_path / "installed",
        )
        result = await installer.install_from_path(pkg)
        assert result.success is True

        # Now upgrade with a new version
        pkg2 = tmp_path / "pkg2"
        pkg2.mkdir()
        (pkg2 / "llmos-module.toml").write_text("""
[module]
module_id = "test_mod"
version = "2.0.0"
description = "Test module v2"
module_class_path = "test_mod.module:TestModule"
sandbox_level = "basic"
platforms = ["linux"]
""")
        (pkg2 / "__init__.py").write_text("")
        (pkg2 / "module.py").write_text("class TestModule: pass\n")
        (pkg2 / "README.md").write_text(
            "# Test Module\n\n## Overview\nV2.\n\n## Actions\nTest.\n\n"
            "## Quick Start\nRun.\n\n## Platform Support\nLinux\n"
        )

        # Mock unregister for upgrade
        registry.unregister = MagicMock()
        registry._instances = {}

        result2 = await installer.upgrade("test_mod", pkg2)
        assert result2.success is True
        assert result2.scan_score >= 0

    @pytest.mark.asyncio
    async def test_upgrade_rejects_dangerous(self, index, registry, venv_manager, tmp_path):
        # First install clean
        pkg = _create_package(tmp_path)
        installer = ModuleInstaller(
            index=index,
            registry=registry,
            venv_manager=venv_manager,
            install_dir=tmp_path / "installed",
        )
        await installer.install_from_path(pkg)

        # Upgrade with dangerous code
        pkg2 = tmp_path / "pkg2"
        pkg2.mkdir()
        (pkg2 / "llmos-module.toml").write_text("""
[module]
module_id = "test_mod"
version = "2.0.0"
description = "Evil module"
module_class_path = "test_mod.module:TestModule"
sandbox_level = "basic"
platforms = ["linux"]
""")
        (pkg2 / "__init__.py").write_text("")
        (pkg2 / "module.py").write_text("""
import os
os.system("rm -rf /")
eval(user_input)
exec(base64.b64decode(payload))
subprocess.call("cmd", shell=True)
marshal.loads(data)
pickle.loads(data)
ctypes.CDLL("libevil.so")
os.popen("whoami")
""")
        (pkg2 / "README.md").write_text(
            "# Test Module\n\n## Overview\nEvil.\n\n## Actions\nBad.\n\n"
            "## Quick Start\nDon't.\n\n## Platform Support\nLinux\n"
        )

        registry.unregister = MagicMock()
        registry._instances = {}

        result2 = await installer.upgrade("test_mod", pkg2)
        assert result2.success is False
        assert "REJECTED" in result2.error


class TestInstallResultFields:
    def test_install_result_has_scan_fields(self):
        r = InstallResult(
            success=True,
            module_id="test",
            version="1.0.0",
            scan_score=85.0,
            trust_tier="verified",
            scan_findings_count=2,
        )
        assert r.scan_score == 85.0
        assert r.trust_tier == "verified"
        assert r.scan_findings_count == 2

    def test_install_result_defaults(self):
        r = InstallResult(success=True, module_id="test")
        assert r.scan_score == -1.0
        assert r.trust_tier == ""
        assert r.scan_findings_count == 0
