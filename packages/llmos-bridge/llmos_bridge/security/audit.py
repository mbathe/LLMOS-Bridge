"""Security layer — Audit logger.

Writes immutable, append-only audit records for:
  - Plan submissions
  - Action executions (start + finish)
  - Permission denials
  - User approvals / rejections
  - Security violations

The AuditLogger is a thin semantic layer on top of the EventBus.  It converts
typed ``AuditEvent`` enum values and keyword arguments into structured dicts
and publishes them to the ``llmos.security`` and ``llmos.actions`` topics.

Swapping the audit backend is done entirely at the EventBus level — the
AuditLogger API never changes:

    # Phase 1 — NDJSON file
    logger = AuditLogger(audit_file=Path("~/.llmos/events.ndjson"))

    # Phase 4 — Redis Streams
    logger = AuditLogger(bus=RedisStreamsBus(...))

    # Fanout — file + live WebSocket
    logger = AuditLogger(bus=FanoutEventBus([LogEventBus(...), ws_bus]))

The legacy constructor ``AuditLogger(audit_file=Path(...))`` is retained for
backward compatibility and creates a ``LogEventBus`` internally.
"""

from __future__ import annotations

import time
from enum import Enum
from pathlib import Path
from typing import Any

from llmos_bridge.events.bus import (
    TOPIC_ACTIONS,
    TOPIC_PLANS,
    TOPIC_SECURITY,
    EventBus,
    LogEventBus,
    NullEventBus,
)
from llmos_bridge.logging import get_logger

log = get_logger(__name__)


class AuditEvent(str, Enum):
    PLAN_SUBMITTED = "plan_submitted"
    PLAN_STARTED = "plan_started"
    PLAN_COMPLETED = "plan_completed"
    PLAN_FAILED = "plan_failed"
    PLAN_CANCELLED = "plan_cancelled"
    ACTION_STARTED = "action_started"
    ACTION_COMPLETED = "action_completed"
    ACTION_FAILED = "action_failed"
    ACTION_SKIPPED = "action_skipped"
    PERMISSION_DENIED = "permission_denied"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_GRANTED = "approval_granted"
    APPROVAL_REJECTED = "approval_rejected"
    SECURITY_VIOLATION = "security_violation"


# Map each AuditEvent to the EventBus topic it should be published on.
_EVENT_TOPIC: dict[AuditEvent, str] = {
    AuditEvent.PLAN_SUBMITTED: TOPIC_PLANS,
    AuditEvent.PLAN_STARTED: TOPIC_PLANS,
    AuditEvent.PLAN_COMPLETED: TOPIC_PLANS,
    AuditEvent.PLAN_FAILED: TOPIC_PLANS,
    AuditEvent.PLAN_CANCELLED: TOPIC_PLANS,
    AuditEvent.ACTION_STARTED: TOPIC_ACTIONS,
    AuditEvent.ACTION_COMPLETED: TOPIC_ACTIONS,
    AuditEvent.ACTION_FAILED: TOPIC_ACTIONS,
    AuditEvent.ACTION_SKIPPED: TOPIC_ACTIONS,
    AuditEvent.PERMISSION_DENIED: TOPIC_SECURITY,
    AuditEvent.APPROVAL_REQUESTED: TOPIC_SECURITY,
    AuditEvent.APPROVAL_GRANTED: TOPIC_SECURITY,
    AuditEvent.APPROVAL_REJECTED: TOPIC_SECURITY,
    AuditEvent.SECURITY_VIOLATION: TOPIC_SECURITY,
}


class AuditLogger:
    """Async-safe audit logger backed by an EventBus.

    Usage (Phase 1 — file backend)::

        logger = AuditLogger(audit_file=Path("~/.llmos/events.ndjson"))
        await logger.log(AuditEvent.PLAN_SUBMITTED, plan_id="abc")

    Usage (Phase 4 — inject any EventBus backend)::

        logger = AuditLogger(bus=redis_bus)

    If both ``audit_file`` and ``bus`` are provided, ``bus`` takes precedence.
    If neither is provided, a ``NullEventBus`` is used (no output).
    """

    def __init__(
        self,
        audit_file: Path | None = None,
        bus: EventBus | None = None,
    ) -> None:
        if bus is not None:
            self._bus: EventBus = bus
        elif audit_file is not None:
            self._bus = LogEventBus(audit_file)
        else:
            self._bus = NullEventBus()

    @property
    def bus(self) -> EventBus:
        """Expose the underlying EventBus for callers that need direct access."""
        return self._bus

    async def log(
        self,
        event: AuditEvent,
        plan_id: str | None = None,
        action_id: str | None = None,
        session_id: str | None = None,
        **data: Any,
    ) -> None:
        """Publish an audit event to the appropriate EventBus topic."""
        record = self._build_record(event, plan_id, action_id, session_id, data)
        topic = _EVENT_TOPIC.get(event, TOPIC_SECURITY)
        log.debug(
            "audit_event",
            audit_event=event.value,
            plan_id=plan_id,
            action_id=action_id,
        )
        await self._bus.emit(topic, record)

    def log_sync(
        self,
        event: AuditEvent,
        plan_id: str | None = None,
        action_id: str | None = None,
        **data: Any,
    ) -> None:
        """Synchronous variant for use outside async contexts."""
        record = self._build_record(event, plan_id, action_id, None, data)
        topic = _EVENT_TOPIC.get(event, TOPIC_SECURITY)
        # Only LogEventBus supports synchronous emission; others silently skip.
        if isinstance(self._bus, LogEventBus):
            self._bus.emit_sync(topic, record)
        else:
            log.debug(
                "audit_event_sync_skipped",
                audit_event=event.value,
                reason="Backend does not support sync emission.",
            )

    @staticmethod
    def _build_record(
        event: AuditEvent,
        plan_id: str | None,
        action_id: str | None,
        session_id: str | None,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        record: dict[str, Any] = {
            "event": event.value,
            "timestamp": time.time(),
        }
        if plan_id is not None:
            record["plan_id"] = plan_id
        if action_id is not None:
            record["action_id"] = action_id
        if session_id is not None:
            record["session_id"] = session_id
        record.update(data)
        return record
