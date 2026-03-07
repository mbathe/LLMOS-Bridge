"""Tests for hub.trust_store — TrustStoreManager."""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llmos_bridge.hub.trust_store import TrustStoreManager, TrustedKey


@pytest.fixture()
def store_dir(tmp_path):
    return tmp_path / "trust_store"


class TestInit:
    @pytest.mark.asyncio
    async def test_init_creates_directory(self, store_dir):
        mgr = TrustStoreManager(store_dir, bootstrap=False)
        await mgr.init()
        assert store_dir.exists()

    @pytest.mark.asyncio
    async def test_init_loads_existing_keys(self, store_dir):
        store_dir.mkdir(parents=True)
        # Manually place a key.
        pub_bytes = b"\x01" * 32
        fp = hashlib.sha256(pub_bytes).hexdigest()
        (store_dir / f"{fp}.pub").write_bytes(pub_bytes)
        (store_dir / f"{fp}.toml").write_text(
            f'label = "Test Key"\nadded_at = {time.time()}\nsource = "manual"\n'
        )

        mgr = TrustStoreManager(store_dir, bootstrap=False)
        await mgr.init()
        assert mgr.key_count == 1
        assert mgr.get_key(fp) is not None
        assert mgr.get_key(fp).label == "Test Key"

    @pytest.mark.asyncio
    async def test_init_empty_with_bootstrap(self, store_dir):
        """When store is empty and bootstrap=True, a default key is generated."""
        mgr = TrustStoreManager(store_dir, bootstrap=True)
        with patch("llmos_bridge.modules.signing.ModuleSigner") as mock_signer_cls:
            mock_kp = MagicMock()
            mock_kp.fingerprint = "abc123"
            mock_kp.public_key_bytes = b"\x02" * 32
            mock_signer_cls.generate_key_pair.return_value = mock_kp
            mock_signer_cls.save_key_pair = MagicMock()
            await mgr.init()
        assert mgr.key_count == 1

    @pytest.mark.asyncio
    async def test_init_no_bootstrap_when_keys_exist(self, store_dir):
        store_dir.mkdir(parents=True)
        pub_bytes = b"\x03" * 32
        fp = hashlib.sha256(pub_bytes).hexdigest()
        (store_dir / f"{fp}.pub").write_bytes(pub_bytes)

        mgr = TrustStoreManager(store_dir, bootstrap=True)
        with patch("llmos_bridge.modules.signing.ModuleSigner") as mock_signer_cls:
            await mgr.init()
            mock_signer_cls.generate_key_pair.assert_not_called()

    @pytest.mark.asyncio
    async def test_init_no_bootstrap_when_disabled(self, store_dir):
        mgr = TrustStoreManager(store_dir, bootstrap=False)
        await mgr.init()
        assert mgr.key_count == 0


class TestKeyManagement:
    @pytest.mark.asyncio
    async def test_add_key(self, store_dir):
        mgr = TrustStoreManager(store_dir, bootstrap=False)
        await mgr.init()
        pub_bytes = b"\x04" * 32
        key = mgr.add_key("My Key", pub_bytes)
        assert key.label == "My Key"
        assert key.source == "manual"
        assert mgr.key_count == 1
        # Verify persisted to disk.
        assert (store_dir / f"{key.fingerprint}.pub").exists()
        assert (store_dir / f"{key.fingerprint}.toml").exists()

    @pytest.mark.asyncio
    async def test_remove_key(self, store_dir):
        mgr = TrustStoreManager(store_dir, bootstrap=False)
        await mgr.init()
        pub_bytes = b"\x05" * 32
        key = mgr.add_key("Remove Me", pub_bytes)
        assert mgr.remove_key(key.fingerprint)
        assert mgr.key_count == 0
        assert not (store_dir / f"{key.fingerprint}.pub").exists()

    @pytest.mark.asyncio
    async def test_remove_nonexistent_key(self, store_dir):
        mgr = TrustStoreManager(store_dir, bootstrap=False)
        await mgr.init()
        assert not mgr.remove_key("nonexistent")

    @pytest.mark.asyncio
    async def test_list_keys(self, store_dir):
        mgr = TrustStoreManager(store_dir, bootstrap=False)
        await mgr.init()
        mgr.add_key("Key A", b"\x06" * 32)
        mgr.add_key("Key B", b"\x07" * 32)
        keys = mgr.list_keys()
        assert len(keys) == 2
        labels = {k.label for k in keys}
        assert labels == {"Key A", "Key B"}

    @pytest.mark.asyncio
    async def test_get_key(self, store_dir):
        mgr = TrustStoreManager(store_dir, bootstrap=False)
        await mgr.init()
        key = mgr.add_key("Findable", b"\x08" * 32)
        found = mgr.get_key(key.fingerprint)
        assert found is not None
        assert found.label == "Findable"

    @pytest.mark.asyncio
    async def test_get_key_missing(self, store_dir):
        mgr = TrustStoreManager(store_dir, bootstrap=False)
        await mgr.init()
        assert mgr.get_key("nonexistent") is None


class TestVerification:
    @pytest.mark.asyncio
    async def test_verify_module_delegates(self, store_dir):
        mgr = TrustStoreManager(store_dir, bootstrap=False)
        await mgr.init()
        # Mock the verifier.
        mgr._verifier = MagicMock()
        mgr._verifier.verify.return_value = True
        sig = MagicMock()
        assert mgr.verify_module(sig, "abc123") is True
        mgr._verifier.verify.assert_called_once_with(sig, "abc123")

    @pytest.mark.asyncio
    async def test_verify_module_not_initialized(self, store_dir):
        mgr = TrustStoreManager(store_dir, bootstrap=False)
        sig = MagicMock()
        assert mgr.verify_module(sig, "abc123") is False


class TestPersistence:
    @pytest.mark.asyncio
    async def test_keys_survive_reload(self, store_dir):
        """Keys persisted to disk are re-loaded on a new init."""
        mgr1 = TrustStoreManager(store_dir, bootstrap=False)
        await mgr1.init()
        key = mgr1.add_key("Persistent", b"\x09" * 32)

        # Create a new manager and init from the same directory.
        mgr2 = TrustStoreManager(store_dir, bootstrap=False)
        await mgr2.init()
        assert mgr2.key_count == 1
        found = mgr2.get_key(key.fingerprint)
        assert found is not None
        assert found.label == "Persistent"

    @pytest.mark.asyncio
    async def test_sidecar_missing(self, store_dir):
        """Key loads even without a TOML sidecar (uses fallback label)."""
        store_dir.mkdir(parents=True)
        pub_bytes = b"\x0a" * 32
        fp = hashlib.sha256(pub_bytes).hexdigest()
        (store_dir / f"{fp}.pub").write_bytes(pub_bytes)
        # No .toml file.

        mgr = TrustStoreManager(store_dir, bootstrap=False)
        await mgr.init()
        assert mgr.key_count == 1
        key = mgr.get_key(fp)
        assert key is not None
        assert key.label == fp[:16]  # Fallback
