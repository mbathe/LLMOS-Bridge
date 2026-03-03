"""Integration test — end-to-end hub flow.

Tests the full install → verify → execute → upgrade → uninstall lifecycle
using real (in-memory) SQLite index and mocked registry.
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from llmos_bridge.hub.index import InstalledModule, ModuleIndex
from llmos_bridge.hub.installer import ModuleInstaller
from llmos_bridge.hub.package import ModulePackage, ModulePackageConfig
from llmos_bridge.hub.resolver import DependencyResolver, ResolutionResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_package(tmp_path: Path, module_id: str, version: str = "1.0.0") -> Path:
    pkg = tmp_path / f"{module_id}_v{version}"
    pkg.mkdir(parents=True)
    (pkg / "llmos-module.toml").write_text(
        f'[module]\n'
        f'module_id = "{module_id}"\n'
        f'version = "{version}"\n'
        f'description = "Integration test module"\n'
        f'module_class_path = "{module_id}.module:{module_id.title()}Module"\n'
    )
    mod_dir = pkg / module_id
    mod_dir.mkdir()
    (mod_dir / "__init__.py").write_text("")
    (mod_dir / "module.py").write_text(f"class {module_id.title()}Module: pass")
    return pkg


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
async def index(tmp_path):
    idx = ModuleIndex(tmp_path / "integration_test.db")
    await idx.init()
    yield idx
    await idx.close()


@pytest.fixture()
def registry():
    reg = MagicMock()
    reg.register_isolated = MagicMock()
    reg.unregister = MagicMock()
    reg.is_available.return_value = True
    return reg


@pytest.fixture()
def installer(index, registry, tmp_path):
    venv = MagicMock()
    return ModuleInstaller(
        index=index,
        registry=registry,
        venv_manager=venv,
        verifier=None,
        require_signatures=False,
        install_dir=tmp_path / "modules",
    )


# ---------------------------------------------------------------------------
# Full lifecycle: install → verify → upgrade → uninstall
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestHubFlowIntegration:
    @pytest.mark.asyncio
    async def test_install_verify_uninstall(self, installer, index, tmp_path):
        """Install a module, verify it's in the index, then uninstall."""
        pkg = _create_package(tmp_path, "sensor")
        result = await installer.install_from_path(pkg)
        assert result.success
        assert result.module_id == "sensor"

        # Verify it's in the index.
        stored = await index.get("sensor")
        assert stored is not None
        assert stored.version == "1.0.0"
        assert stored.enabled is True

        # List all — should contain the module.
        all_mods = await index.list_all()
        assert len(all_mods) == 1

        # Uninstall.
        uninstall = await installer.uninstall("sensor")
        assert uninstall.success

        # Verify it's gone.
        assert await index.get("sensor") is None
        all_mods = await index.list_all()
        assert len(all_mods) == 0

    @pytest.mark.asyncio
    async def test_install_upgrade_flow(self, installer, index, tmp_path):
        """Install v1, upgrade to v2, verify version updated."""
        pkg_v1 = _create_package(tmp_path, "analytics", "1.0.0")
        result_v1 = await installer.install_from_path(pkg_v1)
        assert result_v1.success

        # Create v2.
        pkg_v2 = _create_package(tmp_path, "analytics", "2.0.0")
        result_v2 = await installer.upgrade("analytics", pkg_v2)
        assert result_v2.success
        assert result_v2.version == "2.0.0"

        # Verify index has v2.
        stored = await index.get("analytics")
        assert stored.version == "2.0.0"

    @pytest.mark.asyncio
    async def test_duplicate_install_rejected(self, installer, tmp_path):
        """Installing same module twice should fail."""
        pkg = _create_package(tmp_path, "unique")
        r1 = await installer.install_from_path(pkg)
        assert r1.success
        r2 = await installer.install_from_path(pkg)
        assert not r2.success
        assert "already installed" in r2.error.lower()

    @pytest.mark.asyncio
    async def test_uninstall_nonexistent(self, installer):
        """Uninstalling non-installed module should fail gracefully."""
        r = await installer.uninstall("ghost")
        assert not r.success

    @pytest.mark.asyncio
    async def test_upgrade_without_install(self, installer, tmp_path):
        """Upgrading non-installed module should fail."""
        pkg = _create_package(tmp_path, "nope")
        r = await installer.upgrade("nope", pkg)
        assert not r.success

    @pytest.mark.asyncio
    async def test_enable_disable_via_index(self, installer, index, tmp_path):
        """Test enabling/disabling modules via the index."""
        pkg = _create_package(tmp_path, "toggleable")
        await installer.install_from_path(pkg)

        await index.set_enabled("toggleable", False)
        stored = await index.get("toggleable")
        assert stored.enabled is False

        enabled = await index.list_enabled()
        assert len(enabled) == 0

        await index.set_enabled("toggleable", True)
        enabled = await index.list_enabled()
        assert len(enabled) == 1


# ---------------------------------------------------------------------------
# Package format
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestPackageFormat:
    def test_load_package_from_directory(self, tmp_path):
        pkg_path = _create_package(tmp_path, "weather")
        package = ModulePackage.from_directory(pkg_path)
        assert package.config.module_id == "weather"
        assert package.config.version == "1.0.0"

    def test_toml_with_dependencies(self, tmp_path):
        pkg = tmp_path / "complex"
        pkg.mkdir()
        (pkg / "llmos-module.toml").write_text(
            '[module]\n'
            'module_id = "complex"\n'
            'version = "1.0.0"\n'
            'module_class_path = "complex:Complex"\n'
            'requirements = ["numpy>=1.20", "scipy"]\n'
            'platforms = ["linux"]\n'
            '\n'
            '[module.module_dependencies]\n'
            'sensor = ">=1.0.0"\n'
        )
        config = ModulePackageConfig.from_toml(pkg / "llmos-module.toml")
        assert config.requirements == ["numpy>=1.20", "scipy"]
        assert config.module_dependencies == {"sensor": ">=1.0.0"}
        assert config.platforms == ["linux"]


# ---------------------------------------------------------------------------
# Dependency resolution
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestDependencyResolution:
    def test_multi_module_install_order(self):
        """Resolve install order for a chain: app→api→core."""
        from dataclasses import dataclass, field

        @dataclass
        class Cfg:
            module_dependencies: dict[str, str] = field(default_factory=dict)
            requirements: list[str] = field(default_factory=list)

        resolver = DependencyResolver()
        result = resolver.resolve(
            ["app", "api", "core"],
            {
                "app": Cfg(module_dependencies={"api": ">=1.0"}),
                "api": Cfg(module_dependencies={"core": ">=1.0"}),
                "core": Cfg(),
            },
        )
        assert not result.has_conflicts
        order = result.install_order
        assert order.index("core") < order.index("api")
        assert order.index("api") < order.index("app")

    def test_conflict_detection(self):
        from dataclasses import dataclass, field

        @dataclass
        class Cfg:
            module_dependencies: dict[str, str] = field(default_factory=dict)
            requirements: list[str] = field(default_factory=list)

        resolver = DependencyResolver(
            installed_versions={"old_lib": "0.5.0"}
        )
        result = resolver.resolve(
            ["new_app"],
            {"new_app": Cfg(module_dependencies={"old_lib": ">=1.0.0"})},
        )
        assert result.has_conflicts
        assert any("old_lib" in c for c in result.conflicts)


# ---------------------------------------------------------------------------
# Signing round-trip (if cryptography is available)
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestSigningIntegration:
    def test_sign_and_verify_module(self, tmp_path):
        """Full sign → verify round trip on a real module directory."""
        try:
            from llmos_bridge.modules.signing import ModuleSigner, SignatureVerifier
        except Exception:
            pytest.skip("cryptography not available")

        # Create a module directory.
        mod_dir = tmp_path / "signed_module"
        mod_dir.mkdir()
        (mod_dir / "__init__.py").write_text("")
        (mod_dir / "module.py").write_text("class SignedModule: pass")
        (mod_dir / "llmos-module.toml").write_text(
            '[module]\nmodule_id = "signed_module"\nversion = "1.0.0"\n'
            'module_class_path = "signed_module.module:SignedModule"\n'
        )

        # Generate key pair and sign.
        kp = ModuleSigner.generate_key_pair()
        signer = ModuleSigner(kp.private_key_bytes)
        signature = signer.sign_module(mod_dir)

        # Verify.
        verifier = SignatureVerifier()
        verifier.add_trusted_key(kp.fingerprint, kp.public_key_bytes)
        content_hash = ModuleSigner.compute_module_hash(mod_dir)
        assert verifier.verify(signature, content_hash) is True

        # Tamper with a file → hash changes → verification should fail.
        (mod_dir / "module.py").write_text("class TamperedModule: pass")
        new_hash = ModuleSigner.compute_module_hash(mod_dir)
        assert verifier.verify(signature, new_hash) is False
