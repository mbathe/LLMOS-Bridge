"""Universal Event Format — structured schema for all LLMOS Bridge events.

The UniversalEvent wraps the plain dict events used by EventBus into a typed,
causality-aware structure.  Existing EventBus backends remain unchanged —
UniversalEvent is an optional structured helper for producers that need
causality tracking, session binding, or priority routing.

Key concepts
------------
causality chain:
    ``caused_by``  — the ID of the parent event that triggered this one
    ``causes``     — IDs of child events spawned by this one (populated lazily)
    Together they form a directed acyclic graph of event causality.

session binding:
    ``session_id`` links an event back to the LLM session that originated it.
    When TriggerDaemon fires a plan, the session_id is injected so downstream
    consumers (audit logger, context propagator) can attribute the action.

correlation:
    ``correlation_id`` groups all events belonging to one logical operation
    (e.g. a single plan run, one trigger fire cycle).

priority:
    ``EventPriority`` allows the EventRouter to prioritise delivery.
    Not all backends honour priority — it is advisory only.

Backward compatibility
----------------------
All existing code that emits plain dicts via ``bus.emit(topic, {...})`` keeps
working unchanged.  UniversalEvent is opt-in for new producers.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any


# ---------------------------------------------------------------------------
# EventPriority
# ---------------------------------------------------------------------------


class EventPriority(IntEnum):
    """Advisory processing priority.  Lower ordinal = higher urgency."""

    CRITICAL = 0
    HIGH = 1
    NORMAL = 2
    LOW = 3
    BACKGROUND = 4


# ---------------------------------------------------------------------------
# UniversalEvent
# ---------------------------------------------------------------------------


@dataclass
class UniversalEvent:
    """Structured envelope for all LLMOS Bridge events.

    Producers that want causality tracking create a ``UniversalEvent`` and
    call ``to_dict()`` before handing it to ``EventBus.emit()``.  Consumers
    that receive raw dicts can reconstruct a ``UniversalEvent`` via
    ``UniversalEvent.from_dict(d)``.

    Example::

        event = UniversalEvent(
            type="filesystem.changed",
            topic=TOPIC_FILESYSTEM,
            source="filesystem_watcher",
            payload={"path": "/home/user/doc.txt", "change": "modified"},
            session_id="sess_abc",
        )
        await bus.emit(event.topic, event.to_dict())
    """

    # --- identity ---
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    type: str = ""           # semantic type, e.g. "action_started"
    topic: str = ""          # bus topic, e.g. "llmos.actions"

    # --- timing ---
    timestamp: float = field(default_factory=time.time)

    # --- origin ---
    source: str = ""         # module_id or system component name

    # --- payload ---
    payload: dict[str, Any] = field(default_factory=dict)

    # --- causality chain ---
    caused_by: str | None = None          # parent UniversalEvent.id
    causes: list[str] = field(default_factory=list)  # child event IDs

    # --- session binding ---
    session_id: str | None = None         # originating LLM session
    correlation_id: str | None = None     # groups related events

    # --- routing ---
    priority: EventPriority = EventPriority.NORMAL

    # --- extensible metadata ---
    metadata: dict[str, Any] = field(default_factory=dict)

    # ---------------------------------------------------------------------------
    # Serialisation
    # ---------------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Return a plain dict compatible with ``EventBus.emit()``.

        Standard EventBus fields (``_topic``, ``_timestamp``) are included so
        the bus's ``_stamp()`` call is idempotent.
        """
        d: dict[str, Any] = {
            "_event_id": self.id,
            "_topic": self.topic,
            "_timestamp": self.timestamp,
            "event": self.type,
            "source": self.source,
            **self.payload,
        }
        if self.caused_by is not None:
            d["_caused_by"] = self.caused_by
        if self.causes:
            d["_causes"] = list(self.causes)
        if self.session_id is not None:
            d["_session_id"] = self.session_id
        if self.correlation_id is not None:
            d["_correlation_id"] = self.correlation_id
        if self.priority != EventPriority.NORMAL:
            d["_priority"] = int(self.priority)
        if self.metadata:
            d["_metadata"] = dict(self.metadata)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "UniversalEvent":
        """Reconstruct from a plain event dict stored/forwarded by EventBus."""
        payload = {
            k: v
            for k, v in d.items()
            if not k.startswith("_") and k not in ("event", "source")
        }
        priority_raw = d.get("_priority", EventPriority.NORMAL)
        return cls(
            id=d.get("_event_id", str(uuid.uuid4())),
            type=d.get("event", ""),
            topic=d.get("_topic", ""),
            timestamp=d.get("_timestamp", time.time()),
            source=d.get("source", ""),
            payload=payload,
            caused_by=d.get("_caused_by"),
            causes=list(d.get("_causes", [])),
            session_id=d.get("_session_id"),
            correlation_id=d.get("_correlation_id"),
            priority=EventPriority(priority_raw),
            metadata=dict(d.get("_metadata", {})),
        )

    # ---------------------------------------------------------------------------
    # Causality helpers
    # ---------------------------------------------------------------------------

    def spawn_child(
        self,
        event_type: str,
        topic: str,
        source: str,
        payload: dict[str, Any] | None = None,
    ) -> "UniversalEvent":
        """Create a child event caused by this one.

        The child inherits ``session_id``, ``correlation_id``, and
        ``priority`` from the parent.  ``caused_by`` is set to this
        event's ``id``, and this event's ``causes`` list is updated.

        Example::

            trigger_event = UniversalEvent(
                type="trigger.fired", topic="llmos.triggers", source="trigger_daemon",
                payload={"trigger_id": "t1"}, session_id="sess_abc",
            )
            plan_event = trigger_event.spawn_child(
                "plan.submitted", TOPIC_PLANS, "executor",
                payload={"plan_id": "plan_xyz"},
            )
        """
        child = UniversalEvent(
            type=event_type,
            topic=topic,
            source=source,
            payload=payload or {},
            caused_by=self.id,
            session_id=self.session_id,
            correlation_id=self.correlation_id,
            priority=self.priority,
        )
        self.causes.append(child.id)
        return child

    # ---------------------------------------------------------------------------
    # Convenience
    # ---------------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"UniversalEvent(id={self.id!r}, type={self.type!r}, "
            f"topic={self.topic!r}, source={self.source!r})"
        )
