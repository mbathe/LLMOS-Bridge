"""Unit tests — KeyValueStore (SQLite-backed)."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import AsyncGenerator

import pytest
import pytest_asyncio

from llmos_bridge.memory.store import KeyValueStore


@pytest_asyncio.fixture
async def store(tmp_path: Path) -> AsyncGenerator[KeyValueStore, None]:
    kv = KeyValueStore(tmp_path / "test_kv.db")
    await kv.init()
    yield kv
    await kv.close()


@pytest.mark.unit
class TestKeyValueStore:
    async def test_set_and_get(self, store: KeyValueStore) -> None:
        await store.set("key1", {"data": [1, 2, 3]})
        result = await store.get("key1")
        assert result == {"data": [1, 2, 3]}

    async def test_get_nonexistent_returns_none(self, store: KeyValueStore) -> None:
        result = await store.get("nonexistent_key")
        assert result is None

    async def test_set_overwrites_existing(self, store: KeyValueStore) -> None:
        await store.set("key1", "original")
        await store.set("key1", "updated")
        result = await store.get("key1")
        assert result == "updated"

    async def test_set_scalar_values(self, store: KeyValueStore) -> None:
        await store.set("int_key", 42)
        await store.set("str_key", "hello")
        await store.set("float_key", 3.14)
        await store.set("bool_key", True)
        await store.set("none_key", None)

        assert await store.get("int_key") == 42
        assert await store.get("str_key") == "hello"
        assert abs((await store.get("float_key")) - 3.14) < 0.001
        assert await store.get("bool_key") is True
        assert await store.get("none_key") is None

    async def test_set_with_session_id(self, store: KeyValueStore) -> None:
        await store.set("key1", "val1", session_id="session_a")
        await store.set("key2", "val2", session_id="session_b")
        result = await store.get("key1")
        assert result == "val1"

    async def test_delete_removes_key(self, store: KeyValueStore) -> None:
        await store.set("to_delete", "value")
        await store.delete("to_delete")
        result = await store.get("to_delete")
        assert result is None

    async def test_delete_nonexistent_does_not_raise(self, store: KeyValueStore) -> None:
        await store.delete("ghost_key")  # Should not raise

    async def test_list_keys_empty(self, store: KeyValueStore) -> None:
        keys = await store.list_keys()
        assert keys == []

    async def test_list_keys_returns_all(self, store: KeyValueStore) -> None:
        await store.set("a", 1)
        await store.set("b", 2)
        await store.set("c", 3)
        keys = await store.list_keys()
        assert set(keys) == {"a", "b", "c"}

    async def test_list_keys_by_session_id(self, store: KeyValueStore) -> None:
        await store.set("k1", 1, session_id="sess1")
        await store.set("k2", 2, session_id="sess1")
        await store.set("k3", 3, session_id="sess2")
        keys = await store.list_keys(session_id="sess1")
        assert set(keys) == {"k1", "k2"}

    async def test_get_many(self, store: KeyValueStore) -> None:
        await store.set("x", 10)
        await store.set("y", 20)
        result = await store.get_many(["x", "y", "z"])
        assert result == {"x": 10, "y": 20}

    async def test_ttl_expired_returns_none(self, store: KeyValueStore) -> None:
        # Set with TTL of 0.01 seconds
        await store.set("expiring", "value", ttl_seconds=0.01)
        # Wait for expiry
        await asyncio.sleep(0.05)
        result = await store.get("expiring")
        assert result is None

    async def test_ttl_valid_returns_value(self, store: KeyValueStore) -> None:
        await store.set("fresh", "value", ttl_seconds=60)
        result = await store.get("fresh")
        assert result == "value"

    async def test_purge_expired(self, store: KeyValueStore) -> None:
        await store.set("exp1", "v1", ttl_seconds=0.01)
        await store.set("exp2", "v2", ttl_seconds=0.01)
        await store.set("valid", "v3", ttl_seconds=60)
        await asyncio.sleep(0.05)
        removed = await store.purge_expired()
        assert removed == 2
        keys = await store.list_keys()
        assert "valid" in keys
        assert "exp1" not in keys
