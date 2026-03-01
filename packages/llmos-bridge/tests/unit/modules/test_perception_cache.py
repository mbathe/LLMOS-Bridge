"""Unit tests — PerceptionCache + SpeculativePrefetcher."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llmos_bridge.modules.perception_vision.base import VisionParseResult
from llmos_bridge.modules.perception_vision.cache import (
    PerceptionCache,
    SpeculativePrefetcher,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_result(text: str = "test") -> VisionParseResult:
    return VisionParseResult(
        elements=[],
        width=1920,
        height=1080,
        raw_ocr=text,
        parse_time_ms=100.0,
        model_id="test",
    )


# ---------------------------------------------------------------------------
# PerceptionCache
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPerceptionCache:
    def test_put_and_get(self):
        cache = PerceptionCache()
        data = b"screenshot_bytes"
        result = _make_result()

        cache.put(data, result)
        assert cache.get(data) is result
        assert cache.hits == 1
        assert cache.misses == 0

    def test_cache_miss(self):
        cache = PerceptionCache()
        assert cache.get(b"nonexistent") is None
        assert cache.misses == 1

    def test_lru_eviction(self):
        cache = PerceptionCache(max_entries=2)
        cache.put(b"a", _make_result("a"))
        cache.put(b"b", _make_result("b"))
        cache.put(b"c", _make_result("c"))  # evicts "a"

        assert cache.get(b"a") is None
        assert cache.get(b"b") is not None
        assert cache.get(b"c") is not None

    def test_ttl_expiration(self):
        cache = PerceptionCache(ttl_seconds=0.05)
        cache.put(b"data", _make_result())

        # Should hit before TTL.
        assert cache.get(b"data") is not None

        # Wait for TTL.
        time.sleep(0.06)
        assert cache.get(b"data") is None
        assert cache.misses == 1

    def test_no_ttl(self):
        cache = PerceptionCache(ttl_seconds=0)
        cache.put(b"data", _make_result())
        # TTL=0 means entries never expire.
        assert cache.get(b"data") is not None

    def test_update_existing(self):
        cache = PerceptionCache()
        cache.put(b"data", _make_result("v1"))
        cache.put(b"data", _make_result("v2"))
        result = cache.get(b"data")
        assert result is not None
        assert result.raw_ocr == "v2"
        assert cache.size == 1

    def test_clear(self):
        cache = PerceptionCache()
        cache.put(b"a", _make_result())
        cache.put(b"b", _make_result())
        assert cache.size == 2

        cache.clear()
        assert cache.size == 0
        assert cache.get(b"a") is None

    def test_stats(self):
        cache = PerceptionCache(max_entries=10, ttl_seconds=5.0)
        cache.put(b"a", _make_result())
        cache.get(b"a")  # hit
        cache.get(b"b")  # miss

        stats = cache.stats()
        assert stats["size"] == 1
        assert stats["max_entries"] == 10
        assert stats["ttl_seconds"] == 5.0
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["hit_rate"] == pytest.approx(0.5)

    def test_same_content_same_key(self):
        """Same bytes should hash to the same key."""
        cache = PerceptionCache()
        data = b"identical_screenshot"
        cache.put(data, _make_result())
        assert cache.get(data) is not None
        assert cache.size == 1

    def test_different_content_different_key(self):
        cache = PerceptionCache()
        cache.put(b"screen1", _make_result("1"))
        cache.put(b"screen2", _make_result("2"))
        assert cache.size == 2

    def test_lru_access_updates_order(self):
        """Accessing an entry should make it most recently used."""
        cache = PerceptionCache(max_entries=2)
        cache.put(b"a", _make_result("a"))
        cache.put(b"b", _make_result("b"))

        # Access "a" to make it most recent.
        cache.get(b"a")

        # Add "c" — should evict "b" (least recently used), not "a".
        cache.put(b"c", _make_result("c"))

        assert cache.get(b"a") is not None
        assert cache.get(b"b") is None
        assert cache.get(b"c") is not None


# ---------------------------------------------------------------------------
# SpeculativePrefetcher
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSpeculativePrefetcher:
    @pytest.mark.asyncio
    async def test_get_or_parse_without_trigger(self):
        """Without trigger, should call parse_fn directly."""
        result = _make_result()
        parse_fn = AsyncMock(return_value=(b"screen", result))

        cache = PerceptionCache()
        prefetcher = SpeculativePrefetcher(cache, parse_fn)

        got = await prefetcher.get_or_parse()
        assert got is result
        parse_fn.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_trigger_and_get(self):
        """Trigger should start background parse, get_or_parse uses it."""
        result = _make_result("prefetched")
        parse_fn = AsyncMock(return_value=(b"screen", result))

        cache = PerceptionCache()
        prefetcher = SpeculativePrefetcher(cache, parse_fn)

        prefetcher.trigger()
        # Wait for the prefetch to complete (50ms sleep + actual call).
        await asyncio.sleep(0.15)

        got = await prefetcher.get_or_parse()
        assert got.raw_ocr == "prefetched"
        assert prefetcher.prefetch_used >= 1

    @pytest.mark.asyncio
    async def test_stats(self):
        result = _make_result()
        parse_fn = AsyncMock(return_value=(b"screen", result))

        cache = PerceptionCache()
        prefetcher = SpeculativePrefetcher(cache, parse_fn)

        stats = prefetcher.stats()
        assert stats["prefetch_count"] == 0
        assert stats["prefetch_used"] == 0

    @pytest.mark.asyncio
    async def test_prefetch_count(self):
        result = _make_result()
        parse_fn = AsyncMock(return_value=(b"screen", result))

        cache = PerceptionCache()
        prefetcher = SpeculativePrefetcher(cache, parse_fn)

        prefetcher.trigger()
        assert prefetcher.prefetch_count == 1

        prefetcher.trigger()
        assert prefetcher.prefetch_count == 2

    @pytest.mark.asyncio
    async def test_failed_prefetch_falls_back(self):
        """If prefetch fails, get_or_parse should fall back to fresh parse."""
        call_count = 0

        async def failing_then_success():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("GPU error")
            return (b"screen", _make_result("fresh"))

        cache = PerceptionCache()
        prefetcher = SpeculativePrefetcher(cache, failing_then_success)

        prefetcher.trigger()
        await asyncio.sleep(0.15)

        got = await prefetcher.get_or_parse()
        assert got.raw_ocr == "fresh"
