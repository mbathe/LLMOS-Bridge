"""Unit tests — VectorStore (ChromaDB-backed, graceful degradation)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from llmos_bridge.memory.vector import MemoryEntry, VectorStore


# ---------------------------------------------------------------------------
# VectorStore — when ChromaDB is NOT installed (graceful degradation)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestVectorStoreNoChromeDB:
    @pytest.fixture
    def store_unavailable(self) -> VectorStore:
        with patch.object(VectorStore, "_check_available", return_value=False):
            store = VectorStore()
        return store

    async def test_init_does_not_raise_when_unavailable(
        self, store_unavailable: VectorStore
    ) -> None:
        await store_unavailable.init()  # Should not raise

    async def test_add_does_nothing_when_unavailable(
        self, store_unavailable: VectorStore
    ) -> None:
        await store_unavailable.add("doc1", "some text")  # Should not raise

    async def test_search_returns_empty_when_unavailable(
        self, store_unavailable: VectorStore
    ) -> None:
        results = await store_unavailable.search("query")
        assert results == []

    async def test_count_returns_zero_when_no_collection(
        self, store_unavailable: VectorStore
    ) -> None:
        count = await store_unavailable.count()
        assert count == 0


# ---------------------------------------------------------------------------
# VectorStore — with mocked ChromaDB
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestVectorStoreWithMockedChroma:
    @pytest.fixture
    def chroma_mock(self) -> MagicMock:
        collection = MagicMock()
        collection.query.return_value = {
            "ids": [["doc1", "doc2"]],
            "documents": [["Text of doc1", "Text of doc2"]],
            "metadatas": [[{"plan_id": "p1"}, {"plan_id": "p2"}]],
            "distances": [[0.1, 0.3]],
        }
        collection.count.return_value = 2

        client = MagicMock()
        client.get_or_create_collection.return_value = collection

        chromadb_mock = MagicMock()
        chromadb_mock.EphemeralClient.return_value = client
        chromadb_mock.PersistentClient.return_value = client
        return chromadb_mock, client, collection

    async def test_init_creates_ephemeral_client_when_no_path(
        self, chroma_mock: tuple
    ) -> None:
        chromadb, client, collection = chroma_mock
        with patch.dict("sys.modules", {"chromadb": chromadb}):
            store = VectorStore()
            store._available = True
            await store.init()
            chromadb.EphemeralClient.assert_called_once()

    async def test_init_creates_persistent_client_with_path(
        self, tmp_path: Path, chroma_mock: tuple
    ) -> None:
        chromadb, client, collection = chroma_mock
        with patch.dict("sys.modules", {"chromadb": chromadb}):
            store = VectorStore(path=tmp_path / "vectors")
            store._available = True
            await store.init()
            chromadb.PersistentClient.assert_called_once()

    async def test_add_calls_collection_add(
        self, chroma_mock: tuple
    ) -> None:
        chromadb, client, collection = chroma_mock
        with patch.dict("sys.modules", {"chromadb": chromadb}):
            store = VectorStore()
            store._available = True
            store._collection = collection
            await store.add("doc1", "text content", {"key": "value"})
            collection.add.assert_called_once_with(
                ids=["doc1"],
                documents=["text content"],
                metadatas=[{"key": "value"}],
            )

    async def test_search_returns_memory_entries(
        self, chroma_mock: tuple
    ) -> None:
        chromadb, client, collection = chroma_mock
        store = VectorStore()
        store._available = True
        store._collection = collection

        results = await store.search("some query", top_k=2)
        assert len(results) == 2
        assert isinstance(results[0], MemoryEntry)
        assert results[0].id == "doc1"
        assert results[0].text == "Text of doc1"
        assert results[0].distance == 0.1

    async def test_search_calls_query_with_correct_params(
        self, chroma_mock: tuple
    ) -> None:
        chromadb, client, collection = chroma_mock
        store = VectorStore()
        store._available = True
        store._collection = collection

        await store.search("my query", top_k=5)
        collection.query.assert_called_once_with(
            query_texts=["my query"], n_results=5
        )

    async def test_delete_calls_collection_delete(
        self, chroma_mock: tuple
    ) -> None:
        chromadb, client, collection = chroma_mock
        store = VectorStore()
        store._available = True
        store._collection = collection

        await store.delete("doc1")
        collection.delete.assert_called_once_with(ids=["doc1"])

    async def test_count_returns_collection_count(
        self, chroma_mock: tuple
    ) -> None:
        chromadb, client, collection = chroma_mock
        store = VectorStore()
        store._available = True
        store._collection = collection

        count = await store.count()
        assert count == 2
