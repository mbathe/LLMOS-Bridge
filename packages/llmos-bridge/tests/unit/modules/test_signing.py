"""Tests for Module Spec v3 — Cryptographic signing and verification.

Covers:
  - KeyPair generation
  - ModuleSigner: sign_content, sign_module, compute_module_hash
  - SignatureVerifier: verify, add/remove trusted keys, load_trust_store
  - Round-trip: sign → verify succeeds
  - Wrong key, hash mismatch, untrusted key → verify fails
  - Deterministic hashing
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from llmos_bridge.modules.manifest import ModuleSignature
from llmos_bridge.modules.signing import (
    KeyPair,
    ModuleSigner,
    SignatureVerifier,
    SigningError,
)


# ---------------------------------------------------------------------------
# KeyPair generation
# ---------------------------------------------------------------------------

class TestKeyPairGeneration:
    def test_generate_key_pair(self):
        kp = ModuleSigner.generate_key_pair()
        assert isinstance(kp, KeyPair)
        assert len(kp.private_key_bytes) == 32
        assert len(kp.public_key_bytes) == 32
        assert len(kp.fingerprint) == 64  # SHA-256 hex

    def test_two_key_pairs_are_different(self):
        kp1 = ModuleSigner.generate_key_pair()
        kp2 = ModuleSigner.generate_key_pair()
        assert kp1.private_key_bytes != kp2.private_key_bytes
        assert kp1.fingerprint != kp2.fingerprint

    def test_save_and_load_key_pair(self, tmp_path):
        kp = ModuleSigner.generate_key_pair()
        key_path = tmp_path / "test"
        ModuleSigner.save_key_pair(kp, key_path)

        assert (tmp_path / "test.key").exists()
        assert (tmp_path / "test.pub").exists()
        assert (tmp_path / "test.key").read_bytes() == kp.private_key_bytes
        assert (tmp_path / "test.pub").read_bytes() == kp.public_key_bytes

    def test_load_private_key(self, tmp_path):
        kp = ModuleSigner.generate_key_pair()
        key_path = tmp_path / "test"
        ModuleSigner.save_key_pair(kp, key_path)

        loaded = ModuleSigner.load_private_key(tmp_path / "test.key")
        assert loaded == kp.private_key_bytes


# ---------------------------------------------------------------------------
# ModuleSigner
# ---------------------------------------------------------------------------

class TestModuleSigner:
    @pytest.fixture()
    def key_pair(self) -> KeyPair:
        return ModuleSigner.generate_key_pair()

    @pytest.fixture()
    def signer(self, key_pair: KeyPair) -> ModuleSigner:
        return ModuleSigner(key_pair.private_key_bytes)

    def test_signer_fingerprint(self, signer, key_pair):
        assert signer.fingerprint == key_pair.fingerprint

    def test_signer_public_key(self, signer, key_pair):
        assert signer.public_key_bytes == key_pair.public_key_bytes

    def test_sign_content(self, signer):
        sig = signer.sign_content("abc123")
        assert isinstance(sig, ModuleSignature)
        assert sig.public_key_fingerprint == signer.fingerprint
        assert sig.signed_hash == "abc123"
        assert len(sig.signature_hex) > 0
        assert sig.signed_at != ""

    def test_sign_different_content_produces_different_signatures(self, signer):
        sig1 = signer.sign_content("hash1")
        sig2 = signer.sign_content("hash2")
        assert sig1.signature_hex != sig2.signature_hex

    def test_compute_module_hash_deterministic(self, tmp_path):
        """Same directory content produces the same hash."""
        mod_dir = tmp_path / "my_module"
        mod_dir.mkdir()
        (mod_dir / "__init__.py").write_text("# init")
        (mod_dir / "module.py").write_text("class MyModule: pass")

        hash1 = ModuleSigner.compute_module_hash(mod_dir)
        hash2 = ModuleSigner.compute_module_hash(mod_dir)
        assert hash1 == hash2
        assert len(hash1) == 64  # SHA-256 hex

    def test_compute_module_hash_changes_with_content(self, tmp_path):
        mod_dir = tmp_path / "my_module"
        mod_dir.mkdir()
        (mod_dir / "module.py").write_text("v1")

        hash1 = ModuleSigner.compute_module_hash(mod_dir)

        (mod_dir / "module.py").write_text("v2")

        hash2 = ModuleSigner.compute_module_hash(mod_dir)
        assert hash1 != hash2

    def test_compute_module_hash_includes_config_files(self, tmp_path):
        mod_dir = tmp_path / "my_module"
        mod_dir.mkdir()
        (mod_dir / "module.py").write_text("code")

        hash_without = ModuleSigner.compute_module_hash(mod_dir)

        (mod_dir / "llmos-module.toml").write_text('[module]\nmodule_id="test"')

        hash_with = ModuleSigner.compute_module_hash(mod_dir)
        assert hash_without != hash_with

    def test_sign_module(self, signer, tmp_path):
        mod_dir = tmp_path / "my_module"
        mod_dir.mkdir()
        (mod_dir / "module.py").write_text("class MyModule: pass")

        sig = signer.sign_module(mod_dir)
        assert isinstance(sig, ModuleSignature)
        assert sig.signed_hash == ModuleSigner.compute_module_hash(mod_dir)


# ---------------------------------------------------------------------------
# SignatureVerifier
# ---------------------------------------------------------------------------

class TestSignatureVerifier:
    @pytest.fixture()
    def key_pair(self) -> KeyPair:
        return ModuleSigner.generate_key_pair()

    @pytest.fixture()
    def signer(self, key_pair: KeyPair) -> ModuleSigner:
        return ModuleSigner(key_pair.private_key_bytes)

    @pytest.fixture()
    def verifier(self, key_pair: KeyPair) -> SignatureVerifier:
        v = SignatureVerifier()
        v.add_trusted_key(key_pair.fingerprint, key_pair.public_key_bytes)
        return v

    def test_verify_valid_signature(self, signer, verifier):
        sig = signer.sign_content("test_hash")
        assert verifier.verify(sig, "test_hash") is True

    def test_verify_wrong_content_hash(self, signer, verifier):
        sig = signer.sign_content("hash_a")
        assert verifier.verify(sig, "hash_b") is False

    def test_verify_untrusted_key(self, signer):
        """Verifier without the key in trust store should fail."""
        verifier = SignatureVerifier()
        sig = signer.sign_content("test")
        assert verifier.verify(sig, "test") is False

    def test_verify_tampered_signature(self, signer, verifier):
        sig = signer.sign_content("test")
        # Tamper with the signature
        tampered = ModuleSignature(
            public_key_fingerprint=sig.public_key_fingerprint,
            signature_hex="00" * 64,  # Invalid signature
            signed_hash=sig.signed_hash,
            signed_at=sig.signed_at,
        )
        assert verifier.verify(tampered, "test") is False

    def test_verify_different_signer(self, verifier):
        """Signature from a different (untrusted) key should fail."""
        other_kp = ModuleSigner.generate_key_pair()
        other_signer = ModuleSigner(other_kp.private_key_bytes)
        sig = other_signer.sign_content("test")
        assert verifier.verify(sig, "test") is False

    def test_trusted_count(self, verifier, key_pair):
        assert verifier.trusted_count == 1

    def test_list_trusted_fingerprints(self, verifier, key_pair):
        fps = verifier.list_trusted_fingerprints()
        assert key_pair.fingerprint in fps

    def test_add_and_remove_trusted_key(self):
        v = SignatureVerifier()
        assert v.trusted_count == 0
        v.add_trusted_key("fp123", b"key_data")
        assert v.trusted_count == 1
        v.remove_trusted_key("fp123")
        assert v.trusted_count == 0

    def test_remove_nonexistent_key(self):
        v = SignatureVerifier()
        v.remove_trusted_key("nonexistent")  # Should not raise

    def test_load_trust_store(self, tmp_path):
        # Create trust store directory with .pub files.
        kp1 = ModuleSigner.generate_key_pair()
        kp2 = ModuleSigner.generate_key_pair()
        (tmp_path / "key1.pub").write_bytes(kp1.public_key_bytes)
        (tmp_path / "key2.pub").write_bytes(kp2.public_key_bytes)

        v = SignatureVerifier()
        loaded = v.load_trust_store(tmp_path)
        assert loaded == 2
        assert v.trusted_count == 2
        assert kp1.fingerprint in v.list_trusted_fingerprints()
        assert kp2.fingerprint in v.list_trusted_fingerprints()

    def test_load_trust_store_nonexistent_dir(self, tmp_path):
        v = SignatureVerifier()
        loaded = v.load_trust_store(tmp_path / "nonexistent")
        assert loaded == 0
        assert v.trusted_count == 0

    def test_load_trust_store_empty_dir(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        v = SignatureVerifier()
        loaded = v.load_trust_store(empty)
        assert loaded == 0


# ---------------------------------------------------------------------------
# Full round-trip: sign → verify
# ---------------------------------------------------------------------------

class TestSignVerifyRoundTrip:
    def test_full_module_sign_verify(self, tmp_path):
        """Sign a module directory, then verify the signature."""
        # Create a module directory.
        mod_dir = tmp_path / "my_module"
        mod_dir.mkdir()
        (mod_dir / "__init__.py").write_text("")
        (mod_dir / "module.py").write_text("class MyModule: pass")
        (mod_dir / "llmos-module.toml").write_text(
            '[module]\nmodule_id = "my_module"\nversion = "1.0.0"'
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

    def test_sign_verify_fails_after_modification(self, tmp_path):
        """Signing then modifying the module should fail verification."""
        mod_dir = tmp_path / "my_module"
        mod_dir.mkdir()
        (mod_dir / "module.py").write_text("original code")

        kp = ModuleSigner.generate_key_pair()
        signer = ModuleSigner(kp.private_key_bytes)
        signature = signer.sign_module(mod_dir)

        # Modify the module.
        (mod_dir / "module.py").write_text("modified code")

        # Verify should fail (hash mismatch).
        verifier = SignatureVerifier()
        verifier.add_trusted_key(kp.fingerprint, kp.public_key_bytes)
        content_hash = ModuleSigner.compute_module_hash(mod_dir)
        assert verifier.verify(signature, content_hash) is False

    def test_saved_keys_round_trip(self, tmp_path):
        """Save keys, load them, and use for signing/verification."""
        kp = ModuleSigner.generate_key_pair()
        key_path = tmp_path / "author"
        ModuleSigner.save_key_pair(kp, key_path)

        # Load private key and sign.
        private_bytes = ModuleSigner.load_private_key(tmp_path / "author.key")
        signer = ModuleSigner(private_bytes)
        sig = signer.sign_content("content_hash")

        # Load trust store and verify.
        trust_dir = tmp_path / "trust"
        trust_dir.mkdir()
        (trust_dir / "author.pub").write_bytes(kp.public_key_bytes)

        verifier = SignatureVerifier()
        verifier.load_trust_store(trust_dir)
        assert verifier.verify(sig, "content_hash") is True
