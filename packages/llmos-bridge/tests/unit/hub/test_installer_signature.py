"""Tests for installer signature verification (Phase 2)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llmos_bridge.hub.installer import InstallResult, ModuleInstaller
from llmos_bridge.hub.trust_store import TrustStoreManager
from llmos_bridge.modules.manifest import ModuleSignature


@pytest.fixture()
async def index(tmp_path):
    from llmos_bridge.hub.index import ModuleIndex

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


def _create_package(tmp_path: Path, module_id: str = "test_mod", version: str = "1.0.0") -> Path:
    pkg = tmp_path / "pkg"
    pkg.mkdir(exist_ok=True)
    (pkg / "llmos-module.toml").write_text(f"""\
[module]
module_id = "{module_id}"
version = "{version}"
description = "Test module"
module_class_path = "{module_id}.module:TestModule"
sandbox_level = "basic"
platforms = ["linux"]
""")
    (pkg / "__init__.py").write_text("")
    (pkg / "module.py").write_text("class TestModule: pass\n")
    (pkg / "README.md").write_text(
        "# Test Module\n\n## Overview\nA test.\n\n## Actions\nTest.\n\n"
        "## Quick Start\nRun.\n\n## Platform Support\nLinux\n"
    )
    return pkg


class TestInstallerWithTrustStore:
    @pytest.mark.asyncio
    async def test_unsigned_module_without_trust_store(self, index, registry, venv_manager, tmp_path):
        """Without a trust store, signature_status should be 'unsigned'."""
        pkg = _create_package(tmp_path)
        installer = ModuleInstaller(
            index=index, registry=registry, venv_manager=venv_manager,
            install_dir=tmp_path / "installed",
        )
        result = await installer.install_from_path(pkg)
        assert result.success is True
        mod = await index.get("test_mod")
        assert mod.signature_status == "unsigned"

    @pytest.mark.asyncio
    async def test_unsigned_module_with_trust_store(self, index, registry, venv_manager, tmp_path):
        """With trust store but no sig file, signature_status stays 'unsigned'."""
        pkg = _create_package(tmp_path)
        trust_store = MagicMock(spec=TrustStoreManager)
        installer = ModuleInstaller(
            index=index, registry=registry, venv_manager=venv_manager,
            install_dir=tmp_path / "installed",
            trust_store=trust_store,
        )
        result = await installer.install_from_path(pkg)
        assert result.success is True
        mod = await index.get("test_mod")
        assert mod.signature_status == "unsigned"

    @pytest.mark.asyncio
    async def test_signed_module_verified(self, index, registry, venv_manager, tmp_path):
        """A signed module with a valid signature gets 'verified' status."""
        pkg = _create_package(tmp_path)
        # Create a fake signature file.
        sig = ModuleSignature(
            public_key_fingerprint="abc123",
            signature_hex="deadbeef",
            signed_hash="fakehash",
            signed_at="2026-01-01T00:00:00Z",
        )
        sig.save(pkg / "llmos-module.sig")

        trust_store = MagicMock(spec=TrustStoreManager)
        trust_store.verify_module.return_value = True

        installer = ModuleInstaller(
            index=index, registry=registry, venv_manager=venv_manager,
            install_dir=tmp_path / "installed",
            trust_store=trust_store,
        )
        result = await installer.install_from_path(pkg)
        assert result.success is True

        # Signature was checked.
        trust_store.verify_module.assert_called_once()

        mod = await index.get("test_mod")
        assert mod.signature_status == "verified"
        # With signature verified AND scan passing, should be at least "verified" tier.
        assert mod.trust_tier in ("verified", "trusted")

    @pytest.mark.asyncio
    async def test_signed_module_invalid_signature(self, index, registry, venv_manager, tmp_path):
        """Invalid signature still installs (require_signatures=True is hub-only default).
        The status should be 'invalid' but install proceeds since this is a local install."""
        pkg = _create_package(tmp_path)
        sig = ModuleSignature(
            public_key_fingerprint="abc123",
            signature_hex="deadbeef",
            signed_hash="fakehash",
        )
        sig.save(pkg / "llmos-module.sig")

        trust_store = MagicMock(spec=TrustStoreManager)
        trust_store.verify_module.return_value = False

        installer = ModuleInstaller(
            index=index, registry=registry, venv_manager=venv_manager,
            install_dir=tmp_path / "installed",
            trust_store=trust_store,
        )
        result = await installer.install_from_path(pkg)
        assert result.success is True
        mod = await index.get("test_mod")
        assert mod.signature_status == "invalid"

    @pytest.mark.asyncio
    async def test_signature_error_handled_gracefully(self, index, registry, venv_manager, tmp_path):
        """If signature load fails, status is 'error' but install continues."""
        pkg = _create_package(tmp_path)
        # Create an invalid sig file (not valid JSON).
        (pkg / "llmos-module.sig").write_text("not json")

        trust_store = MagicMock(spec=TrustStoreManager)

        installer = ModuleInstaller(
            index=index, registry=registry, venv_manager=venv_manager,
            install_dir=tmp_path / "installed",
            trust_store=trust_store,
        )
        result = await installer.install_from_path(pkg)
        assert result.success is True
        mod = await index.get("test_mod")
        assert mod.signature_status == "error"


class TestVerifySignatureHelper:
    def test_no_trust_store(self, tmp_path):
        """Without a trust store, returns (False, 'unsigned')."""
        installer = ModuleInstaller(
            index=MagicMock(), registry=MagicMock(), venv_manager=MagicMock(),
        )
        verified, status = installer._verify_signature(tmp_path)
        assert verified is False
        assert status == "unsigned"

    def test_no_sig_file(self, tmp_path):
        """Without a sig file, returns (False, 'unsigned')."""
        trust_store = MagicMock(spec=TrustStoreManager)
        installer = ModuleInstaller(
            index=MagicMock(), registry=MagicMock(), venv_manager=MagicMock(),
            trust_store=trust_store,
        )
        verified, status = installer._verify_signature(tmp_path)
        assert verified is False
        assert status == "unsigned"

    def test_valid_signature(self, tmp_path):
        """Valid signature returns (True, 'verified')."""
        sig = ModuleSignature(
            public_key_fingerprint="fp", signature_hex="ab",
            signed_hash="hash", signed_at="now",
        )
        sig.save(tmp_path / "llmos-module.sig")
        (tmp_path / "llmos-module.toml").write_text("")  # Needed for compute_module_hash

        trust_store = MagicMock(spec=TrustStoreManager)
        trust_store.verify_module.return_value = True

        installer = ModuleInstaller(
            index=MagicMock(), registry=MagicMock(), venv_manager=MagicMock(),
            trust_store=trust_store,
        )
        verified, status = installer._verify_signature(tmp_path)
        assert verified is True
        assert status == "verified"

    def test_invalid_signature(self, tmp_path):
        sig = ModuleSignature(
            public_key_fingerprint="fp", signature_hex="ab",
            signed_hash="hash",
        )
        sig.save(tmp_path / "llmos-module.sig")

        trust_store = MagicMock(spec=TrustStoreManager)
        trust_store.verify_module.return_value = False

        installer = ModuleInstaller(
            index=MagicMock(), registry=MagicMock(), venv_manager=MagicMock(),
            trust_store=trust_store,
        )
        verified, status = installer._verify_signature(tmp_path)
        assert verified is False
        assert status == "invalid"


class TestModuleSignatureSaveLoad:
    def test_save_load_roundtrip(self, tmp_path):
        sig = ModuleSignature(
            public_key_fingerprint="abc123def456",
            signature_hex="cafe",
            signed_hash="deadbeef",
            signed_at="2026-03-01T12:00:00Z",
        )
        path = tmp_path / "test.sig"
        sig.save(path)

        loaded = ModuleSignature.load(path)
        assert loaded.public_key_fingerprint == sig.public_key_fingerprint
        assert loaded.signature_hex == sig.signature_hex
        assert loaded.signed_hash == sig.signed_hash
        assert loaded.signed_at == sig.signed_at

    def test_load_missing_signed_at(self, tmp_path):
        path = tmp_path / "test.sig"
        path.write_text(json.dumps({
            "public_key_fingerprint": "fp",
            "signature_hex": "ab",
            "signed_hash": "hash",
        }))
        loaded = ModuleSignature.load(path)
        assert loaded.signed_at == ""
