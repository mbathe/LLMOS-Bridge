"""Trust store manager — persistent key storage for module signature verification.

Wraps ``SignatureVerifier`` from ``modules.signing`` with file-system
persistence.  Each trusted public key is stored as a ``.pub`` file with a
TOML sidecar for metadata (label, added_at, source).

Bootstrap behaviour:  On first init (empty store), a default LLMOS key pair
is generated so that locally-built modules can be self-signed immediately.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from llmos_bridge.logging import get_logger
from llmos_bridge.modules.manifest import ModuleSignature

log = get_logger(__name__)


@dataclass
class TrustedKey:
    """A public key stored in the trust store."""

    fingerprint: str
    public_key_bytes: bytes
    label: str
    added_at: float
    source: str  # "manual" | "bootstrap" | "hub"


class TrustStoreManager:
    """Manages a persistent directory of trusted Ed25519 public keys.

    Keys are stored at ``{store_dir}/{fingerprint}.pub`` (raw 32 bytes)
    with a sidecar ``{fingerprint}.toml`` containing metadata.

    Usage::

        store = TrustStoreManager(Path("~/.llmos/trust_store"))
        await store.init()
        ok = store.verify_module(signature, content_hash)
    """

    def __init__(self, store_dir: Path, *, bootstrap: bool = True) -> None:
        self._store_dir = store_dir.expanduser()
        self._bootstrap = bootstrap
        self._keys: dict[str, TrustedKey] = {}
        self._verifier: Any = None  # Lazy — avoids import at module level

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def init(self) -> None:
        """Load keys from disk and optionally bootstrap a default key pair."""
        from llmos_bridge.modules.signing import SignatureVerifier

        self._verifier = SignatureVerifier()
        self._store_dir.mkdir(parents=True, exist_ok=True)

        # Load existing keys.
        loaded = 0
        for pub_file in sorted(self._store_dir.glob("*.pub")):
            try:
                key = self._load_key_file(pub_file)
                self._keys[key.fingerprint] = key
                self._verifier.add_trusted_key(key.fingerprint, key.public_key_bytes)
                loaded += 1
            except Exception as exc:
                log.warning("trust_store_key_load_failed", file=str(pub_file), error=str(exc))

        # Bootstrap if store is empty.
        if loaded == 0 and self._bootstrap:
            self._do_bootstrap()

        log.info("trust_store_initialized", keys=len(self._keys), path=str(self._store_dir))

    def _load_key_file(self, pub_file: Path) -> TrustedKey:
        """Load a single key + its TOML sidecar from disk."""
        public_bytes = pub_file.read_bytes()
        fingerprint = hashlib.sha256(public_bytes).hexdigest()

        # Read metadata sidecar.
        toml_path = pub_file.with_suffix(".toml")
        label = ""
        added_at = 0.0
        source = "manual"
        if toml_path.exists():
            meta = self._parse_toml(toml_path.read_text())
            label = meta.get("label", "")
            added_at = float(meta.get("added_at", 0.0))
            source = meta.get("source", "manual")

        return TrustedKey(
            fingerprint=fingerprint,
            public_key_bytes=public_bytes,
            label=label or fingerprint[:16],
            added_at=added_at or pub_file.stat().st_mtime,
            source=source,
        )

    def _do_bootstrap(self) -> None:
        """Generate a default LLMOS key pair for self-signing local modules."""
        try:
            from llmos_bridge.modules.signing import ModuleSigner

            key_pair = ModuleSigner.generate_key_pair()

            # Save public key to trust store.
            pub_path = self._store_dir / f"{key_pair.fingerprint}.pub"
            pub_path.write_bytes(key_pair.public_key_bytes)
            self._write_sidecar(key_pair.fingerprint, "LLMOS Default Key", "bootstrap")

            # Save private key for self-signing.
            signing_key_dir = self._store_dir.parent / "signing_key"
            signing_key_dir.mkdir(parents=True, exist_ok=True)
            ModuleSigner.save_key_pair(key_pair, signing_key_dir / "default")

            # Register in memory.
            trusted = TrustedKey(
                fingerprint=key_pair.fingerprint,
                public_key_bytes=key_pair.public_key_bytes,
                label="LLMOS Default Key",
                added_at=time.time(),
                source="bootstrap",
            )
            self._keys[trusted.fingerprint] = trusted
            self._verifier.add_trusted_key(trusted.fingerprint, trusted.public_key_bytes)

            log.info(
                "trust_store_bootstrapped",
                fingerprint=key_pair.fingerprint[:16],
                signing_key=str(signing_key_dir / "default.key"),
            )
        except Exception as exc:
            log.warning("trust_store_bootstrap_failed", error=str(exc))

    # ------------------------------------------------------------------
    # Key management
    # ------------------------------------------------------------------

    def add_key(self, label: str, public_key_bytes: bytes) -> TrustedKey:
        """Add a public key to the trust store (persists to disk)."""
        fingerprint = hashlib.sha256(public_key_bytes).hexdigest()

        # Persist.
        pub_path = self._store_dir / f"{fingerprint}.pub"
        pub_path.write_bytes(public_key_bytes)
        self._write_sidecar(fingerprint, label, "manual")

        # Register.
        key = TrustedKey(
            fingerprint=fingerprint,
            public_key_bytes=public_key_bytes,
            label=label,
            added_at=time.time(),
            source="manual",
        )
        self._keys[fingerprint] = key
        if self._verifier is not None:
            self._verifier.add_trusted_key(fingerprint, public_key_bytes)

        log.info("trust_store_key_added", fingerprint=fingerprint[:16], label=label)
        return key

    def remove_key(self, fingerprint: str) -> bool:
        """Remove a key from the trust store (deletes from disk)."""
        if fingerprint not in self._keys:
            return False

        # Remove files.
        pub_path = self._store_dir / f"{fingerprint}.pub"
        toml_path = self._store_dir / f"{fingerprint}.toml"
        if pub_path.exists():
            pub_path.unlink()
        if toml_path.exists():
            toml_path.unlink()

        # Unregister.
        del self._keys[fingerprint]
        if self._verifier is not None:
            self._verifier.remove_trusted_key(fingerprint)

        log.info("trust_store_key_removed", fingerprint=fingerprint[:16])
        return True

    def list_keys(self) -> list[TrustedKey]:
        """Return all trusted keys sorted by added_at."""
        return sorted(self._keys.values(), key=lambda k: k.added_at)

    def get_key(self, fingerprint: str) -> TrustedKey | None:
        """Retrieve a key by fingerprint."""
        return self._keys.get(fingerprint)

    @property
    def key_count(self) -> int:
        return len(self._keys)

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------

    def verify_module(self, signature: ModuleSignature, content_hash: str) -> bool:
        """Verify a module signature against the trust store.

        Delegates to ``SignatureVerifier.verify()``.
        """
        if self._verifier is None:
            log.warning("trust_store_not_initialized")
            return False
        return self._verifier.verify(signature, content_hash)

    @property
    def verifier(self) -> Any:
        """Return the underlying ``SignatureVerifier``."""
        return self._verifier

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _write_sidecar(self, fingerprint: str, label: str, source: str) -> None:
        """Write the TOML metadata sidecar for a key."""
        toml_path = self._store_dir / f"{fingerprint}.toml"
        toml_path.write_text(
            f'label = "{label}"\n'
            f"added_at = {time.time()}\n"
            f'source = "{source}"\n'
        )

    @staticmethod
    def _parse_toml(text: str) -> dict[str, str]:
        """Minimal TOML parser — handles simple key = value pairs."""
        result: dict[str, str] = {}
        for line in text.strip().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"')
                result[key] = value
        return result
