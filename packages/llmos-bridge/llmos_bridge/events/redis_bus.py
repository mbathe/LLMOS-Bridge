"""Redis Streams event bus backend.

Publishes events to Redis Streams via XADD.  Reading (consumption) is handled
separately by :class:`~llmos_bridge.cluster.rebroadcaster.EventRebroadcaster`
so that publication is fire-and-forget with zero blocking.

Redis is **fully optional**.  If the ``redis`` package is not installed,
importing this module still works — :class:`RedisStreamsBus` is replaced with
a stub that raises ``RuntimeError`` on instantiation.

Install the extra with::

    pip install llmos-bridge[redis]
"""

from __future__ import annotations

import json
from typing import Any

from llmos_bridge.logging import get_logger

log = get_logger(__name__)

_REDIS_AVAILABLE = True

try:
    import redis.asyncio as aioredis  # noqa: F401
except ImportError:
    _REDIS_AVAILABLE = False

if _REDIS_AVAILABLE:
    from llmos_bridge.events.bus import EventBus

    class RedisStreamsBus(EventBus):
        """Publish events to Redis Streams (XADD).

        Each topic is mapped to a Redis stream keyed ``llmos:{topic}``.
        Every event is stamped with ``_source_node`` so that the
        :class:`EventRebroadcaster` can filter out self-emitted events.

        This class only **publishes** — it never reads from Redis.
        """

        def __init__(
            self,
            redis_url: str,
            node_name: str,
            max_length: int = 10_000,
        ) -> None:
            super().__init__()
            self._redis_url = redis_url
            self._node_name = node_name
            self._max_length = max_length
            self._redis: aioredis.Redis | None = None  # type: ignore[name-defined]

        # -- Lifecycle -------------------------------------------------------

        async def connect(self) -> None:
            """Create the async Redis connection."""
            self._redis = aioredis.from_url(  # type: ignore[attr-defined]
                self._redis_url, decode_responses=True,
            )
            log.info("redis_bus_connected", url=self._redis_url, node=self._node_name)

        async def close(self) -> None:
            """Close the Redis connection."""
            if self._redis is not None:
                await self._redis.aclose()
                self._redis = None
                log.info("redis_bus_closed")

        @property
        def is_connected(self) -> bool:
            return self._redis is not None

        # -- EventBus implementation -----------------------------------------

        async def emit(self, topic: str, event: dict[str, Any]) -> None:  # noqa: D401
            """XADD the event to ``llmos:{topic}``.  Never raises."""
            if self._redis is None:
                return

            self._stamp(topic, event)
            event["_source_node"] = self._node_name

            stream_key = f"llmos:{topic}"
            try:
                payload = json.dumps(event, default=str)
                await self._redis.xadd(
                    stream_key,
                    {"data": payload},
                    maxlen=self._max_length,
                    approximate=True,
                )
            except Exception as exc:
                log.warning("redis_emit_failed", topic=topic, error=str(exc))

            await self._dispatch_to_listeners(topic, event)

else:
    # redis package not installed — provide a stub so imports never crash.
    class RedisStreamsBus:  # type: ignore[no-redef]
        """Stub — redis package is not installed."""

        def __init__(self, *args: object, **kwargs: object) -> None:
            raise RuntimeError(
                "RedisStreamsBus requires the 'redis' package. "
                "Install it with: pip install llmos-bridge[redis]"
            )


__all__ = ["RedisStreamsBus"]
