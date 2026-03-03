"""EventRebroadcaster — consumes Redis Streams and forwards to the local bus.

Architecture
~~~~~~~~~~~~
The two-bus design prevents infinite event loops::

    Producer → full_bus (FanoutEventBus)
                ├── local_bus  (LogEventBus + WebSocketEventBus)
                └── redis_bus  (RedisStreamsBus → XADD)

    Redis ──XREADGROUP──► EventRebroadcaster ──emit──► local_bus ONLY

Because the rebroadcaster writes to ``local_bus`` (not ``full_bus``), events
arriving from other nodes are delivered locally but **never** re-published
back to Redis — breaking the loop.

Self-emitted events are filtered out using the ``_source_node`` field that
:class:`~llmos_bridge.events.redis_bus.RedisStreamsBus` stamps on every event.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from llmos_bridge.events.bus import (
    TOPIC_ACTIONS,
    TOPIC_ACTION_PROGRESS,
    TOPIC_ACTION_RESULTS,
    TOPIC_ERRORS,
    TOPIC_MODULES,
    TOPIC_NODES,
    TOPIC_PERMISSIONS,
    TOPIC_PLANS,
    TOPIC_SECURITY,
    EventBus,
)
from llmos_bridge.logging import get_logger

log = get_logger(__name__)

_DEFAULT_TOPICS = [
    TOPIC_PLANS,
    TOPIC_ACTIONS,
    TOPIC_ACTION_PROGRESS,
    TOPIC_ACTION_RESULTS,
    TOPIC_SECURITY,
    TOPIC_ERRORS,
    TOPIC_NODES,
    TOPIC_MODULES,
    TOPIC_PERMISSIONS,
]


class EventRebroadcaster:
    """Consume events from Redis Streams and forward them to the local event bus.

    Parameters
    ----------
    redis_url:
        Redis connection URL (e.g. ``redis://localhost:6379/0``).
    local_bus:
        The **local** event bus — events are forwarded here, never to ``full_bus``.
    node_name:
        This node's identifier for self-filtering.
    consumer_group:
        Redis consumer group name (``XREADGROUP``).
    topics:
        Topics to subscribe to.  ``None`` = all standard topics.
    """

    def __init__(
        self,
        redis_url: str,
        local_bus: EventBus,
        node_name: str,
        consumer_group: str = "llmos-bridge",
        topics: list[str] | None = None,
    ) -> None:
        self._redis_url = redis_url
        self._local_bus = local_bus
        self._node_name = node_name
        self._consumer_group = consumer_group
        self._topics = topics or list(_DEFAULT_TOPICS)
        self._task: asyncio.Task[None] | None = None
        self._redis: Any = None  # redis.asyncio.Redis

    # -- Lifecycle -----------------------------------------------------------

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        """Connect to Redis, create consumer groups, and start the read loop."""
        if self._task is not None:
            return

        import redis.asyncio as aioredis

        self._redis = aioredis.from_url(self._redis_url, decode_responses=True)

        # Create consumer groups (idempotent — BUSYGROUP is expected on restart).
        for topic in self._topics:
            stream_key = f"llmos:{topic}"
            try:
                await self._redis.xgroup_create(
                    stream_key,
                    self._consumer_group,
                    id="0",
                    mkstream=True,
                )
            except Exception:
                pass  # BUSYGROUP — group already exists

        self._task = asyncio.create_task(self._read_loop(), name="rebroadcaster")
        log.info(
            "rebroadcaster_started",
            node=self._node_name,
            topics=len(self._topics),
            group=self._consumer_group,
        )

    async def stop(self) -> None:
        """Cancel the read loop and close Redis."""
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None

        log.info("rebroadcaster_stopped")

    # -- Internal ------------------------------------------------------------

    async def _read_loop(self) -> None:
        """Block-read from Redis Streams and dispatch to ``local_bus``."""
        consumer_name = self._node_name
        streams = {f"llmos:{t}": ">" for t in self._topics}

        while True:
            try:
                results = await self._redis.xreadgroup(
                    self._consumer_group,
                    consumer_name,
                    streams,
                    count=100,
                    block=1000,
                )
                if not results:
                    continue

                for stream_key, messages in results:
                    topic = stream_key.removeprefix("llmos:")
                    for msg_id, fields in messages:
                        try:
                            event = json.loads(fields.get("data", "{}"))
                        except (json.JSONDecodeError, TypeError):
                            # Malformed message — ACK and skip.
                            await self._redis.xack(
                                stream_key, self._consumer_group, msg_id,
                            )
                            continue

                        # Skip events emitted by this node.
                        if event.get("_source_node") == self._node_name:
                            await self._redis.xack(
                                stream_key, self._consumer_group, msg_id,
                            )
                            continue

                        # Forward to local bus only.
                        try:
                            await self._local_bus.emit(topic, event)
                        except Exception as exc:
                            log.warning(
                                "rebroadcast_emit_failed",
                                topic=topic,
                                error=str(exc),
                            )

                        await self._redis.xack(
                            stream_key, self._consumer_group, msg_id,
                        )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("rebroadcaster_read_error", error=str(exc))
                await asyncio.sleep(1.0)  # back-off on transient errors
