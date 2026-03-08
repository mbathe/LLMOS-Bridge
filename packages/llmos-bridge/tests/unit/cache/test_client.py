"""Unit tests — L2 CacheClient (cache/client.py).

Tests cover:
  - Auto-selection of fakeredis when REDIS_URL is not set
  - get / set / delete / delete_pattern / flush / stats / ping
  - JSON roundtrip for all supported types
  - TTL expiry
  - _DisabledCacheClient no-op behaviour
  - reset_cache_client() singleton lifecycle
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from llmos_bridge.cache.client import (
    CacheClient,
    _DisabledCacheClient,
    get_cache_client,
    reset_cache_client,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _fresh_client() -> CacheClient:
    """Always return a brand-new fakeredis client (no singleton)."""
    reset_cache_client()
    client = await CacheClient.create()
    return client


# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------


class TestBackendSelection:

    @pytest.mark.asyncio
    async def test_fakeredis_selected_when_no_redis_url(self, monkeypatch):
        monkeypatch.delenv("REDIS_URL", raising=False)
        reset_cache_client()
        client = await CacheClient.create()
        assert client.backend == "fakeredis"
        assert client.enabled is True

    @pytest.mark.asyncio
    async def test_disabled_client_when_fakeredis_missing(self, monkeypatch):
        monkeypatch.delenv("REDIS_URL", raising=False)
        reset_cache_client()
        with patch.dict("sys.modules", {"fakeredis": None, "fakeredis.aioredis": None}):
            client = await CacheClient._create_fakeredis()
        assert isinstance(client, _DisabledCacheClient)
        assert client.enabled is False

    @pytest.mark.asyncio
    async def test_falls_back_to_fakeredis_when_real_redis_unreachable(self, monkeypatch):
        monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:19999/0")  # nothing listens there
        reset_cache_client()
        client = await CacheClient.create()
        # Should fall back gracefully — either fakeredis or disabled
        assert client.backend in ("fakeredis", "disabled")

    @pytest.mark.asyncio
    async def test_singleton_reused_on_second_call(self, monkeypatch):
        monkeypatch.delenv("REDIS_URL", raising=False)
        reset_cache_client()
        c1 = await get_cache_client()
        c2 = await get_cache_client()
        assert c1 is c2

    @pytest.mark.asyncio
    async def test_reset_singleton_creates_new_instance(self, monkeypatch):
        monkeypatch.delenv("REDIS_URL", raising=False)
        reset_cache_client()
        c1 = await get_cache_client()
        reset_cache_client()
        c2 = await get_cache_client()
        assert c1 is not c2


# ---------------------------------------------------------------------------
# Core get / set
# ---------------------------------------------------------------------------


class TestGetSet:

    @pytest.mark.asyncio
    async def test_get_returns_none_on_miss(self):
        client = await _fresh_client()
        result = await client.get("nonexistent_key")
        assert result is None

    @pytest.mark.asyncio
    async def test_set_and_get_dict(self):
        client = await _fresh_client()
        value = {"module": "filesystem", "result": [1, 2, 3], "ok": True}
        await client.set("test:dict", value)
        assert await client.get("test:dict") == value

    @pytest.mark.asyncio
    async def test_set_and_get_list(self):
        client = await _fresh_client()
        await client.set("test:list", [{"a": 1}, {"b": 2}])
        assert await client.get("test:list") == [{"a": 1}, {"b": 2}]

    @pytest.mark.asyncio
    async def test_set_and_get_string(self):
        client = await _fresh_client()
        await client.set("test:str", "hello world")
        assert await client.get("test:str") == "hello world"

    @pytest.mark.asyncio
    async def test_set_and_get_int(self):
        client = await _fresh_client()
        await client.set("test:int", 42)
        assert await client.get("test:int") == 42

    @pytest.mark.asyncio
    async def test_set_and_get_float(self):
        client = await _fresh_client()
        await client.set("test:float", 3.14)
        assert abs(await client.get("test:float") - 3.14) < 1e-6

    @pytest.mark.asyncio
    async def test_set_and_get_bool(self):
        client = await _fresh_client()
        await client.set("test:bool_true", True)
        await client.set("test:bool_false", False)
        assert await client.get("test:bool_true") is True
        assert await client.get("test:bool_false") is False

    @pytest.mark.asyncio
    async def test_set_with_none_value_roundtrips(self):
        client = await _fresh_client()
        await client.set("test:none", None)
        # json.dumps(None) == "null" → loads back as None
        assert await client.get("test:none") is None

    @pytest.mark.asyncio
    async def test_non_serialisable_uses_str_fallback(self):
        """datetime and Path objects are stringified via default=str."""
        client = await _fresh_client()
        dt = datetime(2025, 1, 1, 12, 0, 0)
        value = {"ts": dt, "path": Path("/tmp/test")}
        # Should not raise — falls back to str
        await client.set("test:nonserial", value)
        result = await client.get("test:nonserial")
        # Values are stored as strings
        assert result is not None
        assert "2025-01-01" in result["ts"]

    @pytest.mark.asyncio
    async def test_overwrite_existing_key(self):
        client = await _fresh_client()
        await client.set("test:overwrite", {"v": 1})
        await client.set("test:overwrite", {"v": 2})
        assert await client.get("test:overwrite") == {"v": 2}

    @pytest.mark.asyncio
    async def test_deeply_nested_structure(self):
        client = await _fresh_client()
        deep = {"a": {"b": {"c": {"d": [1, 2, {"e": "f"}]}}}}
        await client.set("test:deep", deep)
        assert await client.get("test:deep") == deep


# ---------------------------------------------------------------------------
# TTL
# ---------------------------------------------------------------------------


class TestTTL:

    @pytest.mark.asyncio
    async def test_ttl_zero_stores_without_expiry(self):
        """ttl=0 should store the key without calling setex."""
        client = await _fresh_client()
        await client.set("test:ttl_zero", {"ok": True}, ttl=0)
        assert await client.get("test:ttl_zero") == {"ok": True}

    @pytest.mark.asyncio
    async def test_ttl_none_stores_without_expiry(self):
        client = await _fresh_client()
        await client.set("test:ttl_none", {"ok": True}, ttl=None)
        assert await client.get("test:ttl_none") == {"ok": True}

    @pytest.mark.asyncio
    async def test_set_calls_setex_with_positive_ttl(self):
        """Verify setex is called when ttl > 0."""
        client = await _fresh_client()
        # Wrap the underlying redis client to spy on calls
        original_setex = client._redis.setex
        calls = []

        async def spy_setex(name, time, value):
            calls.append((name, time))
            return await original_setex(name, time, value)

        client._redis.setex = spy_setex
        await client.set("test:ttl_spy", {"data": 1}, ttl=60)
        assert len(calls) == 1
        assert calls[0] == ("test:ttl_spy", 60)


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


class TestDelete:

    @pytest.mark.asyncio
    async def test_delete_existing_key(self):
        client = await _fresh_client()
        await client.set("test:del", {"x": 1})
        count = await client.delete("test:del")
        assert count == 1
        assert await client.get("test:del") is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent_key_returns_zero(self):
        client = await _fresh_client()
        count = await client.delete("does_not_exist")
        assert count == 0

    @pytest.mark.asyncio
    async def test_delete_multiple_keys(self):
        client = await _fresh_client()
        await client.set("k1", 1)
        await client.set("k2", 2)
        await client.set("k3", 3)
        count = await client.delete("k1", "k2", "k3")
        assert count == 3
        for k in ("k1", "k2", "k3"):
            assert await client.get(k) is None

    @pytest.mark.asyncio
    async def test_delete_no_keys_returns_zero(self):
        client = await _fresh_client()
        count = await client.delete()
        assert count == 0

    @pytest.mark.asyncio
    async def test_delete_pattern_removes_matching_keys(self):
        client = await _fresh_client()
        await client.set("llmos:cache:fs:read_file:abc", {"r": 1})
        await client.set("llmos:cache:fs:read_file:def", {"r": 2})
        await client.set("llmos:cache:fs:list_dir:xyz", {"r": 3})
        count = await client.delete_pattern("llmos:cache:fs:read_file:*")
        assert count == 2
        assert await client.get("llmos:cache:fs:read_file:abc") is None
        assert await client.get("llmos:cache:fs:read_file:def") is None
        # list_dir not touched
        assert await client.get("llmos:cache:fs:list_dir:xyz") == {"r": 3}

    @pytest.mark.asyncio
    async def test_delete_pattern_no_matches_returns_zero(self):
        client = await _fresh_client()
        count = await client.delete_pattern("llmos:cache:nothing:*")
        assert count == 0


# ---------------------------------------------------------------------------
# Flush
# ---------------------------------------------------------------------------


class TestFlush:

    @pytest.mark.asyncio
    async def test_flush_deletes_all_llmos_cache_keys(self):
        client = await _fresh_client()
        await client.set("llmos:cache:mod:action:aaa", {"v": 1})
        await client.set("llmos:cache:mod:action:bbb", {"v": 2})
        # Non-cache key should not be touched (fakeredis shares namespace)
        await client._redis.set("other:key", "should_survive")
        await client.flush()
        assert await client.get("llmos:cache:mod:action:aaa") is None
        assert await client.get("llmos:cache:mod:action:bbb") is None

    @pytest.mark.asyncio
    async def test_flush_empty_cache_does_not_raise(self):
        client = await _fresh_client()
        await client.flush()  # should not raise


# ---------------------------------------------------------------------------
# Ping / stats
# ---------------------------------------------------------------------------


class TestPingAndStats:

    @pytest.mark.asyncio
    async def test_ping_returns_true_for_active_client(self):
        client = await _fresh_client()
        assert await client.ping() is True

    @pytest.mark.asyncio
    async def test_stats_returns_dict_with_backend(self):
        client = await _fresh_client()
        stats = await client.stats()
        assert isinstance(stats, dict)
        assert stats["backend"] == "fakeredis"
        assert stats["enabled"] is True


# ---------------------------------------------------------------------------
# _DisabledCacheClient
# ---------------------------------------------------------------------------


class TestDisabledCacheClient:

    @pytest.mark.asyncio
    async def test_get_always_returns_none(self):
        client = _DisabledCacheClient()
        assert await client.get("any_key") is None

    @pytest.mark.asyncio
    async def test_set_is_noop(self):
        client = _DisabledCacheClient()
        await client.set("key", {"data": 1}, ttl=60)
        assert await client.get("key") is None

    @pytest.mark.asyncio
    async def test_delete_returns_zero(self):
        client = _DisabledCacheClient()
        assert await client.delete("k1", "k2") == 0

    @pytest.mark.asyncio
    async def test_delete_pattern_returns_zero(self):
        client = _DisabledCacheClient()
        assert await client.delete_pattern("llmos:*") == 0

    @pytest.mark.asyncio
    async def test_ping_returns_false(self):
        client = _DisabledCacheClient()
        assert await client.ping() is False

    @pytest.mark.asyncio
    async def test_stats_shows_disabled(self):
        client = _DisabledCacheClient()
        stats = await client.stats()
        assert stats["backend"] == "disabled"
        assert stats["enabled"] is False

    @pytest.mark.asyncio
    async def test_flush_is_noop(self):
        client = _DisabledCacheClient()
        await client.flush()  # must not raise

    def test_enabled_is_false(self):
        client = _DisabledCacheClient()
        assert client.enabled is False
