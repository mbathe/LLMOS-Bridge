"""Package cache — file-based cache for downloaded module tarballs.

Caches at ``{cache_dir}/{module_id}/{version}.tar.gz`` to avoid
re-downloading known versions.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from llmos_bridge.logging import get_logger

log = get_logger(__name__)


class PackageCache:
    """Caches downloaded .tar.gz packages to avoid redundant hub downloads."""

    def __init__(self, cache_dir: Path, *, max_size_mb: int = 500) -> None:
        self._cache_dir = cache_dir.expanduser()
        self._max_size_bytes = max_size_mb * 1024 * 1024

    def get(self, module_id: str, version: str) -> Path | None:
        """Return cached tarball path, or None on miss."""
        path = self._path_for(module_id, version)
        if path.exists():
            log.debug("cache_hit", module_id=module_id, version=version)
            return path
        return None

    async def store(self, module_id: str, version: str, data: bytes) -> Path:
        """Write tarball data to cache and return the path."""
        path = self._path_for(module_id, version)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        log.info("cache_stored", module_id=module_id, version=version, size=len(data))
        await self.evict_if_over_limit()
        return path

    async def evict_if_over_limit(self) -> int:
        """Remove oldest cached files until total size is under the limit.

        Returns the number of files removed.
        """
        if not self._cache_dir.exists():
            return 0

        # Collect all cached files with their mtime.
        files: list[tuple[Path, float, int]] = []
        for f in self._cache_dir.rglob("*.tar.gz"):
            try:
                stat = f.stat()
                files.append((f, stat.st_mtime, stat.st_size))
            except OSError:
                continue

        total = sum(s for _, _, s in files)
        if total <= self._max_size_bytes:
            return 0

        # Sort oldest first.
        files.sort(key=lambda x: x[1])
        removed = 0
        for path, _, size in files:
            if total <= self._max_size_bytes:
                break
            try:
                path.unlink()
                total -= size
                removed += 1
                # Remove empty parent directories.
                parent = path.parent
                if parent != self._cache_dir and not any(parent.iterdir()):
                    parent.rmdir()
            except OSError:
                continue

        if removed:
            log.info("cache_evicted", removed=removed, remaining_bytes=total)
        return removed

    def _path_for(self, module_id: str, version: str) -> Path:
        return self._cache_dir / module_id / f"{version}.tar.gz"
