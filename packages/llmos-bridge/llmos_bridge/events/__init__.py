"""Event streaming layer — EventBus infrastructure.

Provides the backbone for all runtime event emission in LLMOS Bridge.
Events flow from producers (executor, modules, audit logger) through an
EventBus implementation to consumers (audit file, dashboard, LLM observer).

Current implementations:
  - NullEventBus  — default, discards all events
  - LogEventBus   — NDJSON append-only file (Phase 1/2)
  - FanoutEventBus — broadcasts to multiple backends (Phase 3+)

Planned (not yet implemented):
  - RedisStreamsBus — Redis Streams backend (Phase 4)
  - KafkaBus        — Apache Kafka / Redpanda backend (Phase 5)

Quick start::

    from llmos_bridge.events import EventBus, LogEventBus, TOPIC_ACTIONS

    bus = LogEventBus(Path("~/.llmos/events.ndjson"))
    await bus.emit(TOPIC_ACTIONS, {"event": "action_started", "action_id": "a1"})
"""

from llmos_bridge.events.bus import (
    TOPIC_ACTIONS,
    TOPIC_DB,
    TOPIC_ERRORS,
    TOPIC_FILESYSTEM,
    TOPIC_IOT,
    TOPIC_PERCEPTION,
    TOPIC_PLANS,
    TOPIC_SECURITY,
    EventBus,
    FanoutEventBus,
    LogEventBus,
    NullEventBus,
)

__all__ = [
    # Interface
    "EventBus",
    # Implementations
    "NullEventBus",
    "LogEventBus",
    "FanoutEventBus",
    # Topic constants
    "TOPIC_PLANS",
    "TOPIC_ACTIONS",
    "TOPIC_SECURITY",
    "TOPIC_ERRORS",
    "TOPIC_PERCEPTION",
    "TOPIC_IOT",
    "TOPIC_DB",
    "TOPIC_FILESYSTEM",
]
