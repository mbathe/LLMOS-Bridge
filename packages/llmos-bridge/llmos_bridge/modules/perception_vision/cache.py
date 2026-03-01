"""Perception Cache — Screenshot-keyed result caching + speculative prefetch.

Avoids redundant GPU parses when the screen hasn't changed:

  - **PerceptionCache**: MD5 hash of screenshot bytes → cached VisionParseResult.
    Hit check costs ~2ms vs ~4s for a full GPU parse.
  - **SpeculativePrefetcher**: After each action, immediately start a background
    screen parse. When the next ``read_screen`` is called, the result is
    already available — saving ~4s per iteration.

Timeline without prefetch::

    action(0.1s) → LLM thinks(5s) → parse(4s) → LLM thinks(5s) → ...
                                     ^^^^^^^^ wasted wait

Timeline with prefetch::

    action(0.1s) → [parse in bg] → LLM thinks(5s) [parse done] → next parse: 0ms
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

from llmos_bridge.modules.perception_vision.base import VisionParseResult


@dataclass
class _CacheEntry:
    result: VisionParseResult
    timestamp: float
    access_count: int = 0


class PerceptionCache:
    """LRU cache keyed by screenshot content hash.

    Args:
        max_entries: Maximum cached results (LRU eviction).
        ttl_seconds: Time-to-live for cache entries (0 = no TTL).
    """

    def __init__(self, max_entries: int = 5, ttl_seconds: float = 2.0) -> None:
        self._max_entries = max_entries
        self._ttl = ttl_seconds
        self._cache: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._hits = 0
        self._misses = 0

    @property
    def hits(self) -> int:
        return self._hits

    @property
    def misses(self) -> int:
        return self._misses

    @property
    def size(self) -> int:
        return len(self._cache)

    def get(self, screenshot_bytes: bytes) -> VisionParseResult | None:
        """Look up cached result by screenshot content hash.

        Returns None on miss or expired entry.
        """
        key = self._hash(screenshot_bytes)
        entry = self._cache.get(key)

        if entry is None:
            self._misses += 1
            return None

        # Check TTL.
        if self._ttl > 0 and (time.monotonic() - entry.timestamp) > self._ttl:
            del self._cache[key]
            self._misses += 1
            return None

        # Move to end (most recently used).
        self._cache.move_to_end(key)
        entry.access_count += 1
        self._hits += 1
        return entry.result

    def put(self, screenshot_bytes: bytes, result: VisionParseResult) -> None:
        """Store a parse result, evicting LRU entry if at capacity."""
        key = self._hash(screenshot_bytes)

        if key in self._cache:
            self._cache.move_to_end(key)
            self._cache[key] = _CacheEntry(
                result=result, timestamp=time.monotonic()
            )
            return

        if len(self._cache) >= self._max_entries:
            self._cache.popitem(last=False)  # Evict oldest

        self._cache[key] = _CacheEntry(
            result=result, timestamp=time.monotonic()
        )

    def clear(self) -> None:
        """Clear all cached entries."""
        self._cache.clear()

    def stats(self) -> dict[str, Any]:
        """Return cache statistics."""
        total = self._hits + self._misses
        return {
            "size": len(self._cache),
            "max_entries": self._max_entries,
            "ttl_seconds": self._ttl,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": self._hits / total if total > 0 else 0.0,
        }

    @staticmethod
    def _hash(data: bytes) -> str:
        return hashlib.md5(data).hexdigest()  # noqa: S324


# Type alias for the parse function used by the prefetcher.
ParseFn = Callable[[], Coroutine[Any, Any, tuple[bytes, VisionParseResult]]]


class SpeculativePrefetcher:
    """Speculatively parse the screen after each action completes.

    Usage::

        prefetcher = SpeculativePrefetcher(cache, parse_fn)

        # After each action completes:
        prefetcher.trigger()

        # When read_screen is needed:
        result = await prefetcher.get_or_parse()
    """

    def __init__(
        self,
        cache: PerceptionCache,
        capture_and_parse_fn: ParseFn,
    ) -> None:
        self._cache = cache
        self._parse_fn = capture_and_parse_fn
        self._pending_task: asyncio.Task[tuple[bytes, VisionParseResult]] | None = None
        self._prefetch_count = 0
        self._prefetch_used = 0

    @property
    def prefetch_count(self) -> int:
        return self._prefetch_count

    @property
    def prefetch_used(self) -> int:
        return self._prefetch_used

    def trigger(self) -> None:
        """Start a background screen capture + parse.

        Safe to call multiple times — cancels any pending prefetch.
        """
        if self._pending_task and not self._pending_task.done():
            self._pending_task.cancel()

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # No event loop — can't prefetch.

        self._pending_task = loop.create_task(self._do_prefetch())
        self._prefetch_count += 1

    async def get_or_parse(self) -> VisionParseResult:
        """Get the prefetched result if available, otherwise parse fresh."""
        if self._pending_task and not self._pending_task.done():
            try:
                screenshot_bytes, result = await self._pending_task
                self._cache.put(screenshot_bytes, result)
                self._prefetch_used += 1
                return result
            except (asyncio.CancelledError, Exception):
                pass  # Fall through to fresh parse.

        if self._pending_task and self._pending_task.done():
            try:
                screenshot_bytes, result = self._pending_task.result()
                self._cache.put(screenshot_bytes, result)
                self._prefetch_used += 1
                return result
            except (asyncio.CancelledError, Exception):
                pass

        # No prefetch available — parse fresh.
        screenshot_bytes, result = await self._parse_fn()
        self._cache.put(screenshot_bytes, result)
        return result

    async def _do_prefetch(self) -> tuple[bytes, VisionParseResult]:
        """Background task: capture screen and parse."""
        await asyncio.sleep(0.05)  # Small delay to let the action effect settle.
        return await self._parse_fn()

    def stats(self) -> dict[str, Any]:
        return {
            "prefetch_count": self._prefetch_count,
            "prefetch_used": self._prefetch_used,
            "pending": self._pending_task is not None
            and not self._pending_task.done(),
            "cache": self._cache.stats(),
        }
