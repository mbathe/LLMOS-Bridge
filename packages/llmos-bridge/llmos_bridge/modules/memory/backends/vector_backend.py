"""Vector memory backend — ChromaDB-backed semantic search.

Wraps the existing VectorStore for episodic/semantic memory.
Suitable for: long-term knowledge, episode recall, pattern matching.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from llmos_bridge.modules.memory.backends.base import BaseMemoryBackend, MemoryEntry


class VectorMemoryBackend(BaseMemoryBackend):
    """ChromaDB vector memory backend.

    Semantic search over stored entries. Each entry is embedded and
    searchable by natural language query.
    """

    BACKEND_ID = "vector"
    DESCRIPTION = "ChromaDB vector store — semantic search over long-term knowledge and episodes"

    def __init__(self, db_path: Path | None = None, collection: str = "llmos_memory"):
        self._db_path = db_path
        self._collection_name = collection
        self._store: Any = None  # VectorStore instance

    async def init(self) -> None:
        from llmos_bridge.memory.vector import VectorStore
        self._store = VectorStore(path=self._db_path, collection=self._collection_name)
        await self._store.init()

    async def close(self) -> None:
        self._store = None

    async def store(self, key: str, value: Any, *, metadata: dict[str, Any] | None = None, ttl_seconds: float | None = None) -> MemoryEntry:
        text = str(value)
        doc_id = key or f"mem-{uuid.uuid4().hex[:8]}"
        meta = metadata or {}
        meta["key"] = key
        await self._store.add(doc_id, text, meta)
        return MemoryEntry(key=doc_id, value=text, metadata=meta, backend=self.BACKEND_ID)

    async def recall(self, key: str) -> MemoryEntry | None:
        # Vector stores don't support exact key lookup natively;
        # use search with the key as query and filter by key metadata
        results = await self.search(key, top_k=1)
        for r in results:
            if r.metadata.get("key") == key or r.key == key:
                return r
        return None

    async def search(self, query: str, *, top_k: int = 5, filters: dict[str, Any] | None = None) -> list[MemoryEntry]:
        if self._store is None:
            return []
        entries = await self._store.search(query, top_k=top_k)
        return [
            MemoryEntry(
                key=e.id,
                value=e.text,
                metadata=e.metadata,
                score=1.0 - (e.distance or 0),  # convert distance to similarity
                backend=self.BACKEND_ID,
            )
            for e in entries
        ]

    async def delete(self, key: str) -> bool:
        if self._store is None:
            return False
        try:
            await self._store.delete(key)
            return True
        except Exception:
            return False

    async def list_keys(self, *, prefix: str | None = None, limit: int = 100) -> list[str]:
        # ChromaDB doesn't have a native list_keys; return empty
        return []

    async def health_check(self) -> dict[str, Any]:
        if self._store is None:
            return {"backend": self.BACKEND_ID, "status": "not_initialized"}
        count = await self._store.count()
        return {"backend": self.BACKEND_ID, "status": "ok", "entries": count}

    def set_store(self, store: Any) -> None:
        """Inject an existing VectorStore."""
        self._store = store
