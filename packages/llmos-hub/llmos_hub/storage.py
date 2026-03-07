"""File-based package storage for .tar.gz module archives."""

from __future__ import annotations

import hashlib
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)


class PackageStorage:
    """Stores module packages at ``{root}/{module_id}/{version}/{module_id}-{version}.tar.gz``."""

    def __init__(self, root: Path) -> None:
        self._root = root

    async def save(self, module_id: str, version: str, data: bytes) -> tuple[str, str]:
        """Save package bytes to disk.

        Returns ``(relative_path, sha256_hex)``.
        """
        rel = f"{module_id}/{version}/{module_id}-{version}.tar.gz"
        dest = self._root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        checksum = hashlib.sha256(data).hexdigest()
        log.info("package_stored", module_id=module_id, version=version, size=len(data))
        return rel, checksum

    async def load(self, relative_path: str) -> bytes:
        """Load package bytes from disk.

        Raises ``FileNotFoundError`` if missing.
        """
        path = self._root / relative_path
        if not path.exists():
            raise FileNotFoundError(f"Package not found: {relative_path}")
        return path.read_bytes()

    async def delete(self, module_id: str) -> None:
        """Delete all versions of a module from storage."""
        mod_dir = self._root / module_id
        if mod_dir.exists():
            import shutil
            shutil.rmtree(mod_dir)
            log.info("package_deleted", module_id=module_id)
