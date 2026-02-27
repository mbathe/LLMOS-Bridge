"""Event streaming infrastructure — EventBus protocol and implementations.

The EventBus is the single communication backbone for all runtime events in
LLMOS Bridge.  Every significant occurrence — plan submission, action start,
action failure, security violation, IoT sensor reading, DB change — is emitted
as a structured dict to a topic, allowing consumers (audit logger, dashboard,
LLM observer, rollback engine) to react to them independently.

Architecture:
                                          ┌────────────────────┐
  PlanExecutor ──emit("llmos.actions")──► │                    │◄── AuditLogger
  AuditLogger  ──emit("llmos.security")──►│    EventBus impl   │◄── LLM Observer (Phase 4)
  IoTModule    ──emit("llmos.iot")──────► │                    │◄── Dashboard WebSocket
  DBModule     ──emit("llmos.db.changes")►│                    │◄── Rollback Engine
                                          └────────────────────┘

Swap the backend by injecting a different EventBus implementation:
  - NullEventBus    → default (no-op, zero overhead)
  - LogEventBus     → NDJSON append-only file (current Phase 1 behavior)
  - RedisStreamsBus → Redis Streams (Phase 4, install redis extra)
  - KafkaBus        → Apache Kafka / Redpanda (Phase 5, enterprise)

This means the executor and all modules never change when the backend changes.

Standard topic names (use the constants below for consistency):
  TOPIC_PLANS     = "llmos.plans"      — plan lifecycle events
  TOPIC_ACTIONS   = "llmos.actions"    — action execution events
  TOPIC_SECURITY  = "llmos.security"   — permission denials, approvals
  TOPIC_ERRORS    = "llmos.errors"     — unhandled runtime errors
  TOPIC_PERCEPTION= "llmos.perception" — screenshot / OCR events
  TOPIC_IOT       = "llmos.iot"        — IoT sensor readings (Phase 4)
  TOPIC_DB        = "llmos.db.changes" — CDC events from database (Phase 4)
  TOPIC_FILESYSTEM= "llmos.filesystem" — filesystem change events (Phase 4)
"""

from __future__ import annotations

import asyncio
import json
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, AsyncIterator

from llmos_bridge.logging import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Standard topic constants
# ---------------------------------------------------------------------------

TOPIC_PLANS = "llmos.plans"
TOPIC_ACTIONS = "llmos.actions"
TOPIC_SECURITY = "llmos.security"
TOPIC_ERRORS = "llmos.errors"
TOPIC_PERCEPTION = "llmos.perception"
TOPIC_IOT = "llmos.iot"
TOPIC_DB = "llmos.db.changes"
TOPIC_FILESYSTEM = "llmos.filesystem"
TOPIC_PERMISSIONS = "llmos.permissions"


# ---------------------------------------------------------------------------
# Abstract interface
# ---------------------------------------------------------------------------


class EventBus(ABC):
    """Abstract event bus.  All implementations must be safe for concurrent async use.

    An event is a plain dict.  The bus adds a ``_topic`` key and a
    ``_timestamp`` (Unix epoch float) before forwarding to the backend.
    Consumers must not depend on field ordering.
    """

    @abstractmethod
    async def emit(self, topic: str, event: dict[str, Any]) -> None:
        """Publish *event* to *topic*.

        This method must not raise — failures are logged and swallowed so that
        a backend outage never propagates into the action execution path.
        """

    async def subscribe(
        self, topics: list[str]
    ) -> AsyncIterator[dict[str, Any]]:
        """Return an async iterator of events from *topics*.

        Not all backends support subscriptions (NullEventBus, LogEventBus).
        Raise ``NotImplementedError`` for backends where it is not meaningful.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support event subscriptions."
        )
        # Make this a generator so Python accepts the return type annotation.
        return  # type: ignore[misc]
        yield {}  # noqa: unreachable

    def _stamp(self, topic: str, event: dict[str, Any]) -> dict[str, Any]:
        """Add metadata fields to *event* in-place and return it."""
        event.setdefault("_topic", topic)
        event.setdefault("_timestamp", time.time())
        return event


# ---------------------------------------------------------------------------
# NullEventBus — default, zero overhead
# ---------------------------------------------------------------------------


class NullEventBus(EventBus):
    """Discards all events.  Used when no event streaming is configured.

    This is the default backend so that the rest of the system never needs
    to check whether an EventBus is present.
    """

    async def emit(self, topic: str, event: dict[str, Any]) -> None:
        pass


# ---------------------------------------------------------------------------
# LogEventBus — NDJSON file (Phase 1 / Phase 2)
# ---------------------------------------------------------------------------


class LogEventBus(EventBus):
    """Writes events as NDJSON to a file — one line per event, append-only.

    This is the primary backend for Phase 1.  It replaces the direct file
    writing in ``AuditLogger`` and allows the same log file to receive events
    from any producer (not just the audit system).

    Usage::

        bus = LogEventBus(Path("~/.llmos/events.ndjson"))
        await bus.emit(TOPIC_ACTIONS, {"event": "action_started", "action_id": "a1"})
    """

    def __init__(self, log_file: Path | None = None) -> None:
        self._file = log_file.expanduser() if log_file else None
        self._lock = asyncio.Lock()

    async def emit(self, topic: str, event: dict[str, Any]) -> None:
        self._stamp(topic, event)
        log.debug("event_bus_emit", topic=topic, event_type=event.get("event"))
        if self._file is None:
            return
        line = json.dumps(event, default=str) + "\n"
        async with self._lock:
            try:
                self._file.parent.mkdir(parents=True, exist_ok=True)
                with self._file.open("a", encoding="utf-8") as f:
                    f.write(line)
            except OSError as exc:
                log.error("event_bus_write_failed", topic=topic, error=str(exc))

    def emit_sync(self, topic: str, event: dict[str, Any]) -> None:
        """Synchronous variant for use outside async contexts (e.g. module __init__)."""
        self._stamp(topic, event)
        if self._file is None:
            return
        line = json.dumps(event, default=str) + "\n"
        try:
            self._file.parent.mkdir(parents=True, exist_ok=True)
            with self._file.open("a", encoding="utf-8") as f:
                f.write(line)
        except OSError as exc:
            log.error("event_bus_sync_write_failed", topic=topic, error=str(exc))


# ---------------------------------------------------------------------------
# FanoutEventBus — broadcast to multiple backends simultaneously
# ---------------------------------------------------------------------------


class FanoutEventBus(EventBus):
    """Routes each event to multiple EventBus backends in parallel.

    Useful for sending events to both a log file (LogEventBus) and a
    live WebSocket broadcaster simultaneously, without changing any producer
    code.

    Usage::

        bus = FanoutEventBus([
            LogEventBus(Path("~/.llmos/events.ndjson")),
            websocket_bus,          # Phase 3 — real-time dashboard
            redis_bus,              # Phase 4 — Redis Streams
        ])
    """

    def __init__(self, backends: list[EventBus]) -> None:
        self._backends = backends

    async def emit(self, topic: str, event: dict[str, Any]) -> None:
        self._stamp(topic, event)
        await asyncio.gather(
            *(b.emit(topic, dict(event)) for b in self._backends),
            return_exceptions=True,
        )
