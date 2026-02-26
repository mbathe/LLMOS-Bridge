"""Memory layer — Vector store (ChromaDB).

Provides semantic memory for LLM context enrichment.
ChromaDB is an optional dependency — the store degrades gracefully
if not installed.

Phase 4 feature: This module is a stub in Phase 1.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from llmos_bridge.logging import get_logger

log = get_logger(__name__)


@dataclass
class MemoryEntry:
    id: str
    text: str
    metadata: dict[str, Any]
    distance: float | None = None


class VectorStore:
    """Semantic memory store backed by ChromaDB.

    Usage::

        store = VectorStore(path=Path("~/.llmos/vector"))
        await store.init()
        await store.add("result-123", "The file contained 42 rows of data.", {"plan_id": "p1"})
        results = await store.search("file row count", top_k=3)
    """

    def __init__(self, path: Any = None, collection: str = "llmos_memory") -> None:
        self._path = path
        self._collection_name = collection
        self._client: Any = None
        self._collection: Any = None
        self._available = self._check_available()

    @staticmethod
    def _check_available() -> bool:
        try:
            import chromadb  # noqa: F401

            return True
        except ImportError:
            log.warning("chromadb_not_installed", hint="pip install llmos-bridge[memory]")
            return False

    async def init(self) -> None:
        if not self._available:
            log.warning("vector_store_unavailable_skipping_init")
            return
        import chromadb

        if self._path:
            self._client = chromadb.PersistentClient(path=str(self._path))
        else:
            self._client = chromadb.EphemeralClient()

        self._collection = self._client.get_or_create_collection(
            name=self._collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        log.info("vector_store_ready", collection=self._collection_name)

    async def add(self, doc_id: str, text: str, metadata: dict[str, Any] | None = None) -> None:
        if not self._available or self._collection is None:
            return
        self._collection.add(
            ids=[doc_id],
            documents=[text],
            metadatas=[metadata or {}],
        )

    async def search(self, query: str, top_k: int = 3) -> list[MemoryEntry]:
        if not self._available or self._collection is None:
            return []
        results = self._collection.query(query_texts=[query], n_results=top_k)
        entries = []
        ids = results.get("ids", [[]])[0]
        docs = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[None] * len(ids)])[0]

        for i, doc_id in enumerate(ids):
            entries.append(
                MemoryEntry(
                    id=doc_id,
                    text=docs[i],
                    metadata=metas[i] or {},
                    distance=distances[i],
                )
            )
        return entries

    async def delete(self, doc_id: str) -> None:
        if self._collection:
            self._collection.delete(ids=[doc_id])

    async def count(self) -> int:
        if self._collection is None:
            return 0
        return self._collection.count()
