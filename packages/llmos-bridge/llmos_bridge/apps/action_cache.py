"""Intra-session action cache for the agent runtime.

Eliminates redundant identical tool calls within a single agent session.

Design
------
- Only *read* actions are cached (list_directory, read_file, …).
- *Write* actions automatically invalidate cache entries whose paths
  overlap with the write target (parent / child / exact match).
- Cache is per-AgentRuntime instance → scoped to one execution session.
- No external dependency: pure Python, no TTL thread.

Usage (inside agent_runtime.py)
---------------------------------
    cache = ActionSessionCache()

    # Before executing a tool call:
    hit = cache.get(module_id, action_name, params)
    if hit is not None:
        return hit  # JSON string, same as real result

    # Invalidate on writes:
    cache.invalidate_for_write(module_id, action_name, params)

    # Cache successful read results:
    cache.put(module_id, action_name, params, json_result)
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Action classification
# ---------------------------------------------------------------------------

# Pure read actions — safe to cache (identical params → identical result
# unless a write has touched the same resource in between).
_READ_ACTIONS: frozenset[tuple[str, str]] = frozenset(
    {
        ("filesystem", "list_directory"),
        ("filesystem", "read_file"),
        ("filesystem", "get_file_info"),
        ("filesystem", "search_files"),
        ("filesystem", "compute_checksum"),
        ("os_exec", "get_system_info"),
        ("os_exec", "get_env_var"),
        ("os_exec", "list_processes"),
        ("database", "list_tables"),
        ("database", "get_table_schema"),
        ("database", "fetch_results"),
        ("db_gateway", "introspect"),
        ("db_gateway", "count"),
        ("db_gateway", "find"),
        ("db_gateway", "find_one"),
    }
)

# Write actions that touch the filesystem → invalidate path-related reads.
_FILESYSTEM_WRITE_ACTIONS: frozenset[tuple[str, str]] = frozenset(
    {
        ("filesystem", "write_file"),
        ("filesystem", "append_file"),
        ("filesystem", "delete_file"),
        ("filesystem", "create_directory"),
        ("filesystem", "move_file"),
        ("filesystem", "copy_file"),
        ("filesystem", "extract_archive"),
        ("filesystem", "create_archive"),
    }
)


# ---------------------------------------------------------------------------
# Cache entry
# ---------------------------------------------------------------------------

class _CacheEntry:
    __slots__ = ("result", "timestamp", "paths")

    def __init__(self, result: str, paths: list[str]) -> None:
        self.result = result
        self.timestamp = time.monotonic()
        self.paths = paths  # resolved paths touched by this read


# ---------------------------------------------------------------------------
# ActionSessionCache
# ---------------------------------------------------------------------------

class ActionSessionCache:
    """In-session cache for read-only agent tool calls.

    Thread-safety: not thread-safe. The agent runtime is single-threaded
    (asyncio) so no locking is needed.
    """

    def __init__(self, enabled: bool = True) -> None:
        self._enabled = enabled
        self._cache: dict[tuple, _CacheEntry] = {}
        # path → set of cache keys that involve that path (for fast invalidation)
        self._path_index: dict[str, set[tuple]] = {}
        self.hits = 0
        self.misses = 0
        self.invalidations = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(
        self, module_id: str, action_name: str, params: dict[str, Any]
    ) -> str | None:
        """Return cached JSON result or None on cache miss."""
        if not self._enabled:
            return None
        if (module_id, action_name) not in _READ_ACTIONS:
            return None
        key = self._make_key(module_id, action_name, params)
        entry = self._cache.get(key)
        if entry is not None:
            self.hits += 1
            logger.debug(
                "action_cache_hit module=%s action=%s age=%.1fs",
                module_id, action_name, time.monotonic() - entry.timestamp,
            )
            return entry.result
        self.misses += 1
        return None

    def put(
        self,
        module_id: str,
        action_name: str,
        params: dict[str, Any],
        result: str,
    ) -> None:
        """Store a successful read result in the cache."""
        if not self._enabled:
            return
        if (module_id, action_name) not in _READ_ACTIONS:
            return
        key = self._make_key(module_id, action_name, params)
        paths = self._extract_paths(params)
        entry = _CacheEntry(result=result, paths=paths)
        self._cache[key] = entry
        for p in paths:
            self._path_index.setdefault(p, set()).add(key)

    def invalidate_for_write(
        self, module_id: str, action_name: str, params: dict[str, Any]
    ) -> int:
        """Invalidate cache entries whose paths overlap with a write target.

        Returns the number of entries removed.
        """
        if not self._enabled:
            return 0
        if (module_id, action_name) not in _FILESYSTEM_WRITE_ACTIONS:
            return 0

        write_paths = self._extract_paths(params)
        # For move_file, also consider the destination
        dest = params.get("destination") or params.get("dest") or params.get("dst")
        if dest:
            write_paths.extend(self._resolve_paths([str(dest)]))

        if not write_paths:
            return 0

        keys_to_remove: set[tuple] = set()
        for write_path in write_paths:
            for cached_path, keys in list(self._path_index.items()):
                if _paths_overlap(cached_path, write_path):
                    keys_to_remove.update(keys)

        for key in keys_to_remove:
            entry = self._cache.pop(key, None)
            if entry:
                for p in entry.paths:
                    s = self._path_index.get(p)
                    if s:
                        s.discard(key)
                        if not s:
                            del self._path_index[p]

        count = len(keys_to_remove)
        if count:
            self.invalidations += count
            logger.debug(
                "action_cache_invalidate module=%s action=%s paths=%s removed=%d",
                module_id, action_name, write_paths, count,
            )
        return count

    def stats(self) -> dict[str, int]:
        return {
            "cached": len(self._cache),
            "hits": self.hits,
            "misses": self.misses,
            "invalidations": self.invalidations,
        }

    def clear(self) -> None:
        self._cache.clear()
        self._path_index.clear()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_key(
        module_id: str, action_name: str, params: dict[str, Any]
    ) -> tuple:
        """Build a hashable cache key from (module, action, params).

        Normalises path parameters to absolute resolved paths so that
        ``path="."`` and ``path="/home/paul/codes/..."`` hash identically
        when they refer to the same directory.
        """
        normalized: dict[str, Any] = {}
        for k, v in params.items():
            if k in ("path", "source", "directory") and isinstance(v, str):
                try:
                    normalized[k] = str(Path(v).expanduser().resolve())
                except Exception:
                    normalized[k] = v
            else:
                normalized[k] = v
        return (module_id, action_name, tuple(sorted(normalized.items())))

    @staticmethod
    def _extract_paths(params: dict[str, Any]) -> list[str]:
        """Extract and resolve all path-like values from params."""
        candidates = []
        for key in ("path", "source", "directory", "file_path"):
            v = params.get(key)
            if v and isinstance(v, str):
                candidates.append(v)
        return ActionSessionCache._resolve_paths(candidates)

    @staticmethod
    def _resolve_paths(raw: list[str]) -> list[str]:
        resolved = []
        for p in raw:
            try:
                resolved.append(str(Path(p).expanduser().resolve()))
            except Exception:
                resolved.append(p)
        return resolved


def _paths_overlap(a: str, b: str) -> bool:
    """Return True if path a and path b are related (parent, child, or equal)."""
    # Ensure trailing slash for prefix comparison
    a_s = a if a.endswith("/") else a + "/"
    b_s = b if b.endswith("/") else b + "/"
    return a == b or a_s.startswith(b_s) or b_s.startswith(a_s)
