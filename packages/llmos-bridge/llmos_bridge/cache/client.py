"""Cache client — Redis with embedded fakeredis fallback.

Auto-selects backend at startup:
- ``REDIS_URL`` set   → real Redis (production, cross-process sharing)
- ``REDIS_URL`` unset → embedded fakeredis (zero config, in-process)

The API is identical regardless of backend, so user code never changes.

Usage::

    from llmos_bridge.cache.client import get_cache_client

    cache = await get_cache_client()
    await cache.set("my_key", {"result": 42}, ttl=60)
    value = await cache.get("my_key")   # → {"result": 42}
    await cache.delete_pattern("llmos:cache:filesystem:read_file:*")
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Module-level singleton — created once on first call to get_cache_client()
_client: "CacheClient | None" = None


async def get_cache_client() -> "CacheClient":
    """Return the global CacheClient, creating it on first call."""
    global _client
    if _client is None:
        _client = await CacheClient.create()
    return _client


def reset_cache_client() -> None:
    """Reset the singleton (used in tests or on daemon restart)."""
    global _client
    _client = None


class CacheClient:
    """Async Redis client with transparent fakeredis fallback.

    All values are JSON-serialised before storage, so any JSON-serialisable
    Python object can be stored and retrieved without explicit encoding.

    Attributes:
        backend: ``"redis"`` or ``"fakeredis"`` — the active backend name.
        enabled: ``False`` if neither Redis nor fakeredis is available.
    """

    def __init__(self, redis: Any, backend: str) -> None:
        self._redis = redis
        self.backend = backend
        self.enabled = True

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    async def create(cls) -> "CacheClient":
        """Create a CacheClient, auto-selecting the best available backend."""
        redis_url = os.getenv("REDIS_URL")

        if redis_url:
            return await cls._create_real_redis(redis_url)
        else:
            return await cls._create_fakeredis()

    @classmethod
    async def _create_real_redis(cls, url: str) -> "CacheClient":
        try:
            import redis.asyncio as aioredis  # type: ignore

            client = aioredis.from_url(url, encoding="utf-8", decode_responses=True)
            await client.ping()
            logger.info("cache: connected to Redis at %s", url)
            return cls(client, backend="redis")
        except ImportError:
            logger.warning(
                "cache: REDIS_URL is set but 'redis' package is not installed. "
                "Run: pip install redis[hiredis]  — falling back to fakeredis."
            )
            return await cls._create_fakeredis()
        except Exception as exc:
            logger.warning(
                "cache: could not connect to Redis at %s (%s) — falling back to fakeredis.",
                url, exc,
            )
            return await cls._create_fakeredis()

    @classmethod
    async def _create_fakeredis(cls) -> "CacheClient":
        try:
            import fakeredis  # type: ignore

            server = fakeredis.FakeServer()
            # ≥2.0: FakeAsyncRedis; older versions: fakeredis.aioredis.FakeRedis
            AsyncClass = getattr(fakeredis, "FakeAsyncRedis", None)
            if AsyncClass is None:
                AsyncClass = fakeredis.aioredis.FakeRedis
            client = AsyncClass(server=server, decode_responses=True)
            logger.info(
                "cache: using embedded fakeredis (set REDIS_URL to use real Redis)"
            )
            return cls(client, backend="fakeredis")
        except (ImportError, AttributeError):
            logger.warning(
                "cache: fakeredis is not installed and REDIS_URL is not set. "
                "Action cache (L2) is disabled. Run: pip install fakeredis"
            )
            return _DisabledCacheClient()

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    async def get(self, key: str) -> Any | None:
        """Return the cached value, or ``None`` on miss."""
        try:
            raw = await self._redis.get(key)
            if raw is None:
                return None
            return json.loads(raw)
        except Exception as exc:
            logger.debug("cache.get error key=%s: %s", key, exc)
            return None

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        """Store a value in the cache.

        Args:
            key:   Cache key (use :func:`make_cache_key` to build it).
            value: Any JSON-serialisable Python object.
            ttl:   Time-to-live in seconds. ``None`` = no expiry.
        """
        try:
            encoded = json.dumps(value, default=str)
            if ttl and ttl > 0:
                await self._redis.setex(key, ttl, encoded)
            else:
                await self._redis.set(key, encoded)
        except Exception as exc:
            logger.debug("cache.set error key=%s: %s", key, exc)

    async def delete(self, *keys: str) -> int:
        """Delete one or more keys. Returns number of keys deleted."""
        if not keys:
            return 0
        try:
            return await self._redis.delete(*keys)
        except Exception as exc:
            logger.debug("cache.delete error: %s", exc)
            return 0

    async def delete_pattern(self, pattern: str) -> int:
        """Delete all keys matching a glob pattern.

        Args:
            pattern: Redis glob pattern, e.g. ``"llmos:cache:filesystem:read_file:*"``

        Returns:
            Number of keys deleted.
        """
        try:
            keys = await self._redis.keys(pattern)
            if not keys:
                return 0
            return await self._redis.delete(*keys)
        except Exception as exc:
            logger.debug("cache.delete_pattern error pattern=%s: %s", pattern, exc)
            return 0

    async def ping(self) -> bool:
        """Return ``True`` if the backend is reachable."""
        try:
            await self._redis.ping()
            return True
        except Exception:
            return False

    async def stats(self) -> dict[str, Any]:
        """Return basic cache statistics."""
        try:
            info = await self._redis.info("stats")
            return {
                "backend": self.backend,
                "enabled": self.enabled,
                "keyspace_hits": info.get("keyspace_hits", 0),
                "keyspace_misses": info.get("keyspace_misses", 0),
            }
        except Exception:
            return {"backend": self.backend, "enabled": self.enabled}

    async def flush(self) -> None:
        """Flush ALL cache entries. Use with care."""
        try:
            keys = await self._redis.keys("llmos:cache:*")
            if keys:
                await self._redis.delete(*keys)
        except Exception as exc:
            logger.debug("cache.flush error: %s", exc)


class _DisabledCacheClient(CacheClient):
    """No-op cache client used when neither Redis nor fakeredis is available."""

    def __init__(self) -> None:  # type: ignore[override]
        self.backend = "disabled"
        self.enabled = False

    async def get(self, key: str) -> Any | None:
        return None

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        pass

    async def delete(self, *keys: str) -> int:
        return 0

    async def delete_pattern(self, pattern: str) -> int:
        return 0

    async def ping(self) -> bool:
        return False

    async def stats(self) -> dict[str, Any]:
        return {"backend": "disabled", "enabled": False}

    async def flush(self) -> None:
        pass
