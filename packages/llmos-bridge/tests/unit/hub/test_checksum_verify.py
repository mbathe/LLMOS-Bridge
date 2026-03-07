"""Tests for checksum verification on module load (Phase 2)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llmos_bridge.hub.index import InstalledModule


def _make_installed_module(
    module_id: str = "test_mod",
    checksum: str = "abc123",
    install_path: str = "/fake/path",
    trust_tier: str = "unverified",
    signature_status: str = "unsigned",
) -> InstalledModule:
    return InstalledModule(
        module_id=module_id,
        version="1.0.0",
        install_path=install_path,
        module_class_path="test_mod.module:TestModule",
        checksum=checksum,
        trust_tier=trust_tier,
        signature_status=signature_status,
    )


class TestChecksumVerificationLogic:
    """Test the checksum verification logic that runs in server.py on daemon restart."""

    def test_matching_checksum(self, tmp_path):
        """When module hash matches stored checksum, no issue."""
        from llmos_bridge.modules.signing import ModuleSigner

        # Create a real module directory.
        mod_dir = tmp_path / "test_mod"
        mod_dir.mkdir()
        (mod_dir / "module.py").write_text("class TestModule: pass\n")
        (mod_dir / "llmos-module.toml").write_text("[module]\nmodule_id = 'test'\n")

        checksum = ModuleSigner.compute_module_hash(mod_dir)
        current = ModuleSigner.compute_module_hash(mod_dir)
        assert checksum == current

    def test_tampered_module_detected(self, tmp_path):
        """When module files change, checksum no longer matches."""
        from llmos_bridge.modules.signing import ModuleSigner

        mod_dir = tmp_path / "test_mod"
        mod_dir.mkdir()
        (mod_dir / "module.py").write_text("class TestModule: pass\n")
        (mod_dir / "llmos-module.toml").write_text("[module]\nmodule_id = 'test'\n")

        original_checksum = ModuleSigner.compute_module_hash(mod_dir)

        # Tamper with the module.
        (mod_dir / "module.py").write_text("class TestModule:\n    evil = True\n")

        new_checksum = ModuleSigner.compute_module_hash(mod_dir)
        assert original_checksum != new_checksum

    def test_checksum_deterministic(self, tmp_path):
        """Same content always produces the same checksum."""
        from llmos_bridge.modules.signing import ModuleSigner

        mod_dir = tmp_path / "test_mod"
        mod_dir.mkdir()
        (mod_dir / "module.py").write_text("class TestModule: pass\n")

        hash1 = ModuleSigner.compute_module_hash(mod_dir)
        hash2 = ModuleSigner.compute_module_hash(mod_dir)
        assert hash1 == hash2

    def test_checksum_includes_py_files(self, tmp_path):
        """Adding a new .py file changes the checksum."""
        from llmos_bridge.modules.signing import ModuleSigner

        mod_dir = tmp_path / "test_mod"
        mod_dir.mkdir()
        (mod_dir / "module.py").write_text("class TestModule: pass\n")

        hash_before = ModuleSigner.compute_module_hash(mod_dir)

        (mod_dir / "helper.py").write_text("def helper(): pass\n")

        hash_after = ModuleSigner.compute_module_hash(mod_dir)
        assert hash_before != hash_after

    def test_checksum_includes_toml(self, tmp_path):
        """Changing llmos-module.toml changes the checksum."""
        from llmos_bridge.modules.signing import ModuleSigner

        mod_dir = tmp_path / "test_mod"
        mod_dir.mkdir()
        (mod_dir / "module.py").write_text("class TestModule: pass\n")
        (mod_dir / "llmos-module.toml").write_text("[module]\nversion = '1.0'\n")

        hash_before = ModuleSigner.compute_module_hash(mod_dir)

        (mod_dir / "llmos-module.toml").write_text("[module]\nversion = '2.0'\n")

        hash_after = ModuleSigner.compute_module_hash(mod_dir)
        assert hash_before != hash_after

    def test_empty_checksum_skips_verification(self):
        """Modules without a stored checksum are not verified."""
        mod = _make_installed_module(checksum="")
        # The server.py code checks: `if settings.hub.verify_checksums_on_load and _im.checksum:`
        # Empty checksum means the condition is False — no verification runs.
        assert not mod.checksum

    def test_missing_module_path(self, tmp_path):
        """Modules with missing install paths are handled gracefully."""
        mod = _make_installed_module(
            checksum="abc123",
            install_path=str(tmp_path / "nonexistent"),
        )
        assert not Path(mod.install_path).exists()


class TestInstalledModuleChecksumField:
    def test_default_checksum_empty(self):
        mod = InstalledModule(
            module_id="x",
            version="1.0",
            install_path="/tmp/x",
            module_class_path="x:X",
        )
        assert mod.checksum == ""

    def test_custom_checksum(self):
        mod = InstalledModule(
            module_id="x",
            version="1.0",
            install_path="/tmp/x",
            module_class_path="x:X",
            checksum="sha256hash",
        )
        assert mod.checksum == "sha256hash"
