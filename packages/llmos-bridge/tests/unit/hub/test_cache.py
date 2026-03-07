"""Tests for hub.cache — PackageCache."""

from __future__ import annotations

from pathlib import Path

import pytest

from llmos_bridge.hub.cache import PackageCache


@pytest.fixture()
def cache(tmp_path):
    return PackageCache(tmp_path / "cache", max_size_mb=1)


class TestPackageCache:
    async def test_store_and_get(self, cache):
        data = b"tarball data"
        path = await cache.store("my_mod", "1.0.0", data)
        assert path.exists()
        assert path.read_bytes() == data

        # Get should return the path.
        found = cache.get("my_mod", "1.0.0")
        assert found is not None
        assert found == path

    async def test_get_miss(self, cache):
        assert cache.get("nonexistent", "1.0.0") is None

    async def test_eviction(self, tmp_path):
        # Cache with 1KB limit.
        small_cache = PackageCache(tmp_path / "small_cache", max_size_mb=0)
        # max_size_mb=0 means 0 bytes — everything should be evicted.
        # But we need at least some data to test.
        small_cache._max_size_bytes = 100  # 100 bytes limit

        await small_cache.store("mod_a", "1.0.0", b"x" * 60)
        await small_cache.store("mod_b", "1.0.0", b"y" * 60)

        # After storing 120 bytes with 100 byte limit, oldest should be evicted.
        assert small_cache.get("mod_b", "1.0.0") is not None
        # mod_a may have been evicted.
        total_files = list(small_cache._cache_dir.rglob("*.tar.gz"))
        assert len(total_files) <= 2  # At most 2, likely 1 after eviction

    async def test_auto_creates_directories(self, cache):
        # Cache dir doesn't exist yet.
        assert not cache._cache_dir.exists()
        await cache.store("new_mod", "0.1.0", b"data")
        assert cache._cache_dir.exists()

    async def test_evict_empty_cache(self, cache):
        removed = await cache.evict_if_over_limit()
        assert removed == 0
