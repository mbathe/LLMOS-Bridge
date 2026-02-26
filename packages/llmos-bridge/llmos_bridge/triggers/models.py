"""TriggerDaemon data models.

All trigger state is represented with plain Python dataclasses so that it
can be serialised to JSON and persisted in SQLite without an ORM.

Key classes
-----------
TriggerType         — category of watched event source
TriggerState        — lifecycle state machine
TriggerPriority     — execution priority (maps to EventPriority)
TriggerCondition    — *what* to watch (type + type-specific params)
TriggerHealth       — operational metrics (fire count, errors, latency)
TriggerDefinition   — complete trigger record (persisted unit)
TriggerFireEvent    — transient event emitted when a trigger fires

Condition params quick-reference
---------------------------------
TEMPORAL:
    schedule            str   — cron expression "0 9 * * 1-5"
    interval_seconds    float — repeat every N seconds
    run_at              float — Unix timestamp, fires once

FILESYSTEM:
    path                str   — absolute path to watch
    recursive           bool  — watch subdirectories (default False)
    events              list  — ["created","modified","deleted","moved"]

PROCESS:
    name                str   — process name pattern (fnmatch)
    event               str   — "started" | "stopped" | "crashed"

RESOURCE:
    metric              str   — "cpu_percent" | "memory_percent" | "disk_percent"
    threshold           float — trigger when metric exceeds this value
    duration_seconds    float — metric must stay above threshold for this long

IOT:
    pin                 int   — GPIO pin number (Raspberry Pi BCM)
    edge                str   — "rising" | "falling" | "both"
    mqtt_topic          str   — MQTT topic to subscribe to
    mqtt_threshold      float — trigger when float payload exceeds threshold

COMPOSITE (operator / sub-conditions):
    operator            str   — "AND" | "OR" | "NOT" | "SEQ" | "WINDOW"
    trigger_ids         list  — IDs of sub-triggers to combine
    timeout_seconds     float — for SEQ: all must fire within this window
    count               int   — for WINDOW: N fires required within window
    window_seconds      float — for WINDOW: sliding window duration
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any, Literal


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class TriggerType(str, Enum):
    """Category of trigger based on its event source."""

    TEMPORAL = "temporal"
    FILESYSTEM = "filesystem"
    PROCESS = "process"
    RESOURCE = "resource"
    APPLICATION = "application"
    IOT = "iot"
    COMPOSITE = "composite"


class TriggerState(str, Enum):
    """Lifecycle state of a TriggerDefinition.

    State machine::

        REGISTERED → INACTIVE (if created disabled)
                   → ACTIVE   (when daemon starts watching)

        ACTIVE  → WATCHING  (partial match for SEQ/WINDOW composites)
                → FIRED     (condition fully met, plan submitted)
                → THROTTLED (too many fires in time window)
                → FAILED    (watcher encountered unrecoverable error)

        FIRED / THROTTLED → ACTIVE (after cooldown, ready to re-arm)
        FAILED → INACTIVE (manual re-enable required)
        INACTIVE → ACTIVE (via API enable call)
    """

    REGISTERED = "registered"
    INACTIVE = "inactive"
    ACTIVE = "active"
    WATCHING = "watching"
    FIRED = "fired"
    THROTTLED = "throttled"
    FAILED = "failed"


class TriggerPriority(IntEnum):
    """Execution priority for plans launched by triggers.

    Higher value = higher urgency.  Maps to ``EventPriority`` (inverted
    ordinal) when emitting trigger-fire events.
    """

    BACKGROUND = 0
    LOW = 1
    NORMAL = 2
    HIGH = 3
    CRITICAL = 4


# ---------------------------------------------------------------------------
# Condition
# ---------------------------------------------------------------------------


@dataclass
class TriggerCondition:
    """Encapsulates *what* a trigger watches.

    ``type``   — which watcher implementation to instantiate.
    ``params`` — type-specific configuration (see module docstring).
    """

    type: TriggerType
    params: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Health metrics
# ---------------------------------------------------------------------------


@dataclass
class TriggerHealth:
    """Operational metrics — updated in-memory and flushed to SQLite."""

    fire_count: int = 0
    fail_count: int = 0
    throttle_count: int = 0
    last_fired_at: float | None = None
    last_error: str | None = None
    avg_latency_ms: float = 0.0   # ms from event detection → plan submit
    created_at: float = field(default_factory=time.time)

    def record_fire(self, latency_ms: float) -> None:
        """Update counters after a successful fire."""
        self.fire_count += 1
        self.last_fired_at = time.time()
        # Exponential moving average (α = 0.2)
        if self.avg_latency_ms == 0.0:
            self.avg_latency_ms = latency_ms
        else:
            self.avg_latency_ms = 0.8 * self.avg_latency_ms + 0.2 * latency_ms

    def record_fail(self, error: str) -> None:
        self.fail_count += 1
        self.last_error = error

    def record_throttle(self) -> None:
        self.throttle_count += 1


# ---------------------------------------------------------------------------
# TriggerDefinition — the persisted unit
# ---------------------------------------------------------------------------


@dataclass
class TriggerDefinition:
    """Complete definition of a trigger — the primary persisted object.

    A TriggerDefinition describes:
    - *what* to watch (``condition``)
    - *what to do* when it fires (``plan_template``)
    - *how* to manage it (priority, throttling, conflict policy, chaining)
    """

    # --- identity ---
    trigger_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    description: str = ""

    # --- condition (what to watch) ---
    condition: TriggerCondition = field(
        default_factory=lambda: TriggerCondition(TriggerType.TEMPORAL)
    )

    # --- action (what to do when fired) ---
    plan_template: dict[str, Any] = field(default_factory=dict)
    """IML plan JSON template.  Template variables are resolved at fire-time:
    {{trigger.event_type}}, {{trigger.payload.path}}, etc."""

    plan_id_prefix: str = "trigger"
    """Auto-generated plan IDs will be ``{plan_id_prefix}_{short_uuid}``."""

    # --- lifecycle ---
    state: TriggerState = TriggerState.REGISTERED
    priority: TriggerPriority = TriggerPriority.NORMAL
    enabled: bool = True

    # --- throttling ---
    min_interval_seconds: float = 0.0
    """Minimum seconds between consecutive fires.  0 = no throttle."""

    max_fires_per_hour: int = 0
    """Maximum fires per hour.  0 = unlimited."""

    # --- conflict resolution ---
    conflict_policy: Literal["queue", "preempt", "reject"] = "queue"
    """
    queue   — if a plan from this trigger is already running, queue the fire
    preempt — cancel the running plan and start a new one (requires HIGH+)
    reject  — discard the fire if a plan is already running
    """

    resource_lock: str | None = None
    """Optional resource name this trigger locks.  Two triggers with the same
    resource_lock cannot have plans running concurrently."""

    # --- trigger chaining ---
    parent_trigger_id: str | None = None
    """Set when this trigger was created dynamically by a running plan."""

    chain_depth: int = 0
    """How deep in the trigger chain this trigger sits (loop protection)."""

    max_chain_depth: int = 5
    """Maximum allowed chain depth.  Triggers beyond this are rejected."""

    # --- ownership ---
    created_by: str = "user"
    """Who created this trigger: "user", "llm", or "system"."""

    tags: list[str] = field(default_factory=list)

    # --- health ---
    health: TriggerHealth = field(default_factory=TriggerHealth)

    # --- timestamps ---
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    expires_at: float | None = None
    """Auto-delete this trigger after this Unix timestamp.  None = permanent."""

    # ---------------------------------------------------------------------------
    # Business logic
    # ---------------------------------------------------------------------------

    def is_expired(self) -> bool:
        """Return True if the trigger has passed its expiry time."""
        return self.expires_at is not None and time.time() > self.expires_at

    def can_fire(self) -> bool:
        """Return True if the trigger is eligible to fire right now."""
        if not self.enabled:
            return False
        if self.state not in (TriggerState.ACTIVE, TriggerState.WATCHING, TriggerState.FIRED):
            return False
        if self.is_expired():
            return False
        if (
            self.min_interval_seconds > 0
            and self.health.last_fired_at is not None
            and time.time() - self.health.last_fired_at < self.min_interval_seconds
        ):
            return False
        return True

    def generate_plan_id(self) -> str:
        """Return a unique plan_id for a new fire instance."""
        return f"{self.plan_id_prefix}_{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# TriggerFireEvent — transient event emitted on each fire
# ---------------------------------------------------------------------------


@dataclass
class TriggerFireEvent:
    """Transient record emitted to the EventBus when a trigger fires.

    Used internally by TriggerDaemon and as the source of template
    variables in triggered plans.
    """

    trigger_id: str
    trigger_name: str
    event_type: str           # e.g. "filesystem.changed"
    payload: dict[str, Any]   # raw event payload from the watcher
    fired_at: float = field(default_factory=time.time)
    plan_id: str = ""         # set after plan is submitted
    session_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "trigger_id": self.trigger_id,
            "trigger_name": self.trigger_name,
            "event_type": self.event_type,
            "payload": self.payload,
            "fired_at": self.fired_at,
            "plan_id": self.plan_id,
            "session_id": self.session_id,
        }

    def as_template_context(self) -> dict[str, Any]:
        """Return dict usable as ``trigger`` template namespace."""
        return {
            "trigger_id": self.trigger_id,
            "trigger_name": self.trigger_name,
            "event_type": self.event_type,
            "payload": self.payload,
            "fired_at": self.fired_at,
        }
