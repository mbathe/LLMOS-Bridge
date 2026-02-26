"""EventRouter — pattern-based topic routing for LLMOS Bridge events.

Extends the EventBus interface with MQTT-style wildcard matching so that
individual consumers (TriggerDaemon, WebSocket broadcaster, audit logger)
can subscribe only to the topics they care about.

Wildcard syntax
---------------
``*``   matches exactly one path segment (no dots).
        "llmos.filesystem.*" matches "llmos.filesystem.changed"
        but NOT "llmos.filesystem.a.b".

``#``   matches zero or more path segments (any depth).
        "llmos.iot.#" matches "llmos.iot", "llmos.iot.temp", "llmos.iot.a.b.c".

exact   no wildcards — literal topic comparison.

The EventRouter is the recommended EventBus implementation when running
TriggerDaemon, replacing the plain FanoutEventBus in server.py startup.

Usage::

    router = EventRouter(fallback=LogEventBus(audit_path))

    # All filesystem events → trigger daemon
    router.add_route("llmos.filesystem.*", trigger_daemon.on_event)

    # IoT events at any depth → trigger daemon
    router.add_route("llmos.iot.#", trigger_daemon.on_event)

    # Plan + action events → WebSocket broadcaster
    router.add_route("llmos.plans", ws_bus.emit)
    router.add_route("llmos.actions", ws_bus.emit)

    # Inject as event_bus everywhere
    executor = PlanExecutor(..., event_bus_unused_param=router)

Note: the fallback receives events with NO matching route only.
Handlers registered via add_route receive events in addition to the
fallback (not instead of it).  To suppress fallback for certain topics,
do not set a fallback and register explicit routes for everything.
"""

from __future__ import annotations

import re
from typing import Any, Awaitable, Callable, Union

from llmos_bridge.events.bus import EventBus
from llmos_bridge.logging import get_logger

log = get_logger(__name__)

# Async or sync callable that accepts (topic, event_dict)
EventHandler = Callable[[str, dict[str, Any]], Union[Awaitable[None], None]]


# ---------------------------------------------------------------------------
# Topic pattern matching
# ---------------------------------------------------------------------------


def topic_matches(pattern: str, topic: str) -> bool:
    """Return True if *topic* matches *pattern*.

    Args:
        pattern: Pattern string with optional ``*`` or ``#`` wildcards.
        topic:   Fully-qualified topic string to test.

    Examples::

        topic_matches("llmos.plans", "llmos.plans")        → True
        topic_matches("llmos.filesystem.*", "llmos.filesystem.changed") → True
        topic_matches("llmos.filesystem.*", "llmos.filesystem.a.b")     → False
        topic_matches("llmos.iot.#", "llmos.iot.sensors.temp")          → True
        topic_matches("#", "any.topic.ever")                            → True
    """
    if "#" not in pattern and "*" not in pattern:
        return pattern == topic

    # Build a regex from the MQTT-style pattern.
    # Special case: "parent.#" should match "parent" (no sub-topic) AND
    # "parent.sub1" AND "parent.sub1.sub2" — i.e. the separator before # is optional.
    # Strategy: first collapse trailing ".#" into "(\..+)?" before parsing.
    normalized = pattern
    if normalized.endswith(".#"):
        # "a.b.#" → match "a.b" or "a.b.<anything>"
        prefix = re.escape(normalized[:-2])  # escape "a.b"
        return bool(re.fullmatch(prefix + r"(\..+)?", topic))

    regex_parts: list[str] = []
    for segment in re.split(r"(\*|#)", normalized):
        if segment == "#":
            regex_parts.append(".*")
        elif segment == "*":
            regex_parts.append(r"[^.]+")
        else:
            regex_parts.append(re.escape(segment))
    regex = "".join(regex_parts)
    return bool(re.fullmatch(regex, topic))


# ---------------------------------------------------------------------------
# EventRouter
# ---------------------------------------------------------------------------


class EventRouter(EventBus):
    """Pattern-based event router.

    Routes each emitted event to every handler whose registered pattern
    matches the event's topic.  Events with no matching route are forwarded
    to ``fallback`` (if set).

    Thread / asyncio safety
    -----------------------
    Route registration (``add_route`` / ``remove_route``) is expected to
    happen at startup before concurrent ``emit`` calls begin.  The router
    itself does not lock route mutations — add/remove should not be called
    while the daemon is running unless you manage your own synchronisation.
    """

    def __init__(self, fallback: EventBus | None = None) -> None:
        """
        Args:
            fallback: Receives events that match no registered route.
                      Typically ``NullEventBus()`` or ``LogEventBus(path)``.
        """
        # List of (pattern, handler) pairs — checked in registration order
        self._routes: list[tuple[str, EventHandler]] = []
        self._fallback = fallback

    # ---------------------------------------------------------------------------
    # Route management
    # ---------------------------------------------------------------------------

    def add_route(self, pattern: str, handler: EventHandler) -> None:
        """Register *handler* to be called for events matching *pattern*.

        A single handler may be registered under multiple patterns.
        The same (pattern, handler) pair may be registered multiple times
        without deduplication — it will be called multiple times per event.
        """
        self._routes.append((pattern, handler))
        log.debug("event_route_added", pattern=pattern, handler=getattr(handler, "__qualname__", repr(handler)))

    def remove_route(self, pattern: str, handler: EventHandler) -> None:
        """Unregister the first matching (pattern, handler) pair.

        If the pair was registered multiple times, only the first occurrence
        is removed.
        """
        for i, (p, h) in enumerate(self._routes):
            if p == pattern and h is handler:
                self._routes.pop(i)
                log.debug("event_route_removed", pattern=pattern)
                return

    @property
    def route_count(self) -> int:
        """Number of registered routes."""
        return len(self._routes)

    # ---------------------------------------------------------------------------
    # EventBus implementation
    # ---------------------------------------------------------------------------

    async def emit(self, topic: str, event: dict[str, Any]) -> None:
        """Dispatch *event* to all handlers whose pattern matches *topic*.

        Errors in individual handlers are caught and logged — they never
        propagate to the caller (EventBus contract: ``emit`` must not raise).
        """
        self._stamp(topic, event)

        matched = False
        for pattern, handler in self._routes:
            if topic_matches(pattern, topic):
                matched = True
                try:
                    result = handler(topic, event)
                    if hasattr(result, "__await__"):
                        await result  # type: ignore[misc]
                except Exception as exc:
                    log.error(
                        "event_route_handler_error",
                        pattern=pattern,
                        topic=topic,
                        error=str(exc),
                    )

        if not matched and self._fallback is not None:
            try:
                await self._fallback.emit(topic, event)
            except Exception as exc:
                log.error("event_route_fallback_error", topic=topic, error=str(exc))
