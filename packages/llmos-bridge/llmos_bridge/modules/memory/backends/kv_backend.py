"""KV memory backend — SQLite-backed key-value storage.

Wraps the existing KeyValueStore for fast, persistent memory.
Suitable for: working memory, conversation memory, user preferences.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from llmos_bridge.modules.memory.backends.base import BaseMemoryBackend, MemoryEntry


class KVMemoryBackend(BaseMemoryBackend):
    """SQLite key-value memory backend.

    Uses the daemon's existing KeyValueStore infrastructure.
    Fast reads/writes, persistent across sessions.
    """

    BACKEND_ID = "kv"
    DESCRIPTION = "SQLite key-value store — fast persistent memory for facts, preferences, and state"

    def __init__(self, db_path: Path | None = None, namespace: str = "memory"):
        self._db_path = db_path or Path("~/.llmos/memory_kv.db")
        self._namespace = namespace
        self._store: Any = None  # KeyValueStore instance

    def _prefixed(self, key: str) -> str:
        return f"{self._namespace}:{key}"

    def _unprefixed(self, key: str) -> str:
        prefix = f"{self._namespace}:"
        return key[len(prefix):] if key.startswith(prefix) else key

    async def init(self) -> None:
        from llmos_bridge.memory.store import KeyValueStore
        self._store = KeyValueStore(self._db_path)
        await self._store.init()

    async def close(self) -> None:
        if self._store:
            await self._store.close()
            self._store = None

    async def store(self, key: str, value: Any, *, metadata: dict[str, Any] | None = None, ttl_seconds: float | None = None) -> MemoryEntry:
        full_key = self._prefixed(key)
        # Store value + metadata together
        payload = {"value": value, "metadata": metadata or {}}
        await self._store.set(full_key, payload, ttl_seconds=ttl_seconds)
        return MemoryEntry(key=key, value=value, metadata=metadata or {}, backend=self.BACKEND_ID)

    async def recall(self, key: str) -> MemoryEntry | None:
        raw = await self._store.get(self._prefixed(key))
        if raw is None:
            return None
        if isinstance(raw, dict) and "value" in raw:
            return MemoryEntry(key=key, value=raw["value"], metadata=raw.get("metadata", {}), backend=self.BACKEND_ID)
        # Legacy: plain value stored directly
        return MemoryEntry(key=key, value=raw, metadata={}, backend=self.BACKEND_ID)

    async def delete(self, key: str) -> bool:
        existing = await self._store.get(self._prefixed(key))
        if existing is None:
            return False
        await self._store.delete(self._prefixed(key))
        return True

    async def list_keys(self, *, prefix: str | None = None, limit: int = 100) -> list[str]:
        all_keys = await self._store.list_keys()
        ns_prefix = self._namespace + ":"
        keys = [self._unprefixed(k) for k in all_keys if k.startswith(ns_prefix)]
        if prefix:
            keys = [k for k in keys if k.startswith(prefix)]
        return keys[:limit]

    async def clear(self) -> int:
        keys = await self.list_keys(limit=10_000)
        for k in keys:
            await self._store.delete(self._prefixed(k))
        return len(keys)

    def set_store(self, store: Any) -> None:
        """Inject an existing KeyValueStore (used when daemon already has one)."""
        self._store = store
