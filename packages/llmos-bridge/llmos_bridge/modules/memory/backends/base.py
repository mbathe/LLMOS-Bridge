"""Base memory backend — abstract interface for all memory backends.

Any memory backend (built-in or custom) must implement this interface.
This enables users to create their own memory types (Redis, Postgres, S3, etc.)
and plug them into the memory module.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class MemoryEntry:
    """A single memory entry returned by backends."""
    key: str
    value: Any
    metadata: dict[str, Any] = field(default_factory=dict)
    score: float | None = None  # relevance score for search results
    backend: str = ""           # which backend produced this entry


class BaseMemoryBackend(ABC):
    """Abstract base for all memory backends.

    Every backend provides 6 core operations:
    - store: persist a key-value pair
    - recall: retrieve by exact key
    - search: semantic/fuzzy search (optional — return [] if not supported)
    - delete: remove a key
    - list_keys: enumerate stored keys
    - clear: remove all entries

    Lifecycle:
    - init(): called once at startup (connect, create tables, etc.)
    - close(): called at shutdown (close connections)

    Subclass this to create custom backends:
        class RedisMemoryBackend(BaseMemoryBackend):
            BACKEND_ID = "redis"
            ...
    """

    BACKEND_ID: str = ""  # Unique identifier (e.g., "kv", "vector", "file", "cognitive")
    DESCRIPTION: str = ""

    @abstractmethod
    async def init(self) -> None:
        """Initialize the backend (connect to DB, create schema, etc.)."""

    @abstractmethod
    async def close(self) -> None:
        """Clean shutdown (close connections, flush buffers)."""

    @abstractmethod
    async def store(self, key: str, value: Any, *, metadata: dict[str, Any] | None = None, ttl_seconds: float | None = None) -> MemoryEntry:
        """Store a value. Returns the stored entry."""

    @abstractmethod
    async def recall(self, key: str) -> MemoryEntry | None:
        """Recall a value by exact key. Returns None if not found."""

    async def search(self, query: str, *, top_k: int = 5, filters: dict[str, Any] | None = None) -> list[MemoryEntry]:
        """Semantic/fuzzy search. Override in backends that support it."""
        return []

    @abstractmethod
    async def delete(self, key: str) -> bool:
        """Delete by key. Returns True if found and deleted."""

    @abstractmethod
    async def list_keys(self, *, prefix: str | None = None, limit: int = 100) -> list[str]:
        """List stored keys, optionally filtered by prefix."""

    async def clear(self) -> int:
        """Remove all entries. Returns count of removed entries."""
        keys = await self.list_keys(limit=10_000)
        count = 0
        for k in keys:
            if await self.delete(k):
                count += 1
        return count

    async def health_check(self) -> dict[str, Any]:
        """Backend health status."""
        return {"backend": self.BACKEND_ID, "status": "ok"}

    def info(self) -> dict[str, Any]:
        """Return metadata about this backend."""
        return {
            "backend_id": self.BACKEND_ID,
            "description": self.DESCRIPTION,
            "supports_search": self.search.__func__ is not BaseMemoryBackend.search,
        }
