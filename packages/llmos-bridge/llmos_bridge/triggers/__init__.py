"""LLMOS Bridge — TriggerDaemon subsystem.

The trigger subsystem adds reactive automation to LLMOS Bridge:
LLM plans can be automatically launched in response to OS events,
schedules, IoT signals, or composite conditions — without any human
interaction.

Think of it as ``systemd`` for LLMs: you define *what to watch* and
*what to do*, and TriggerDaemon handles the rest.

Package structure
-----------------
triggers/
  models.py      — Data models: TriggerDefinition, TriggerType, TriggerState
  store.py       — SQLite-backed persistence (survives daemon restarts)
  watchers/      — One watcher implementation per trigger type
    base.py      — BaseWatcher ABC
    temporal.py  — CronWatcher, IntervalWatcher, OnceWatcher
    system.py    — FileSystemWatcher, ProcessWatcher, ResourceWatcher
    composite.py — AND / OR / NOT / SEQ / WINDOW logic
  scheduler.py   — Priority-based fire scheduler + preemption
  conflict.py    — ConflictResolver (queue / preempt / reject policies)
  daemon.py      — TriggerDaemon — the main orchestrator
"""

from llmos_bridge.triggers.models import (
    TriggerCondition,
    TriggerDefinition,
    TriggerHealth,
    TriggerPriority,
    TriggerState,
    TriggerType,
)
from llmos_bridge.triggers.daemon import TriggerDaemon
from llmos_bridge.triggers.store import TriggerStore

__all__ = [
    "TriggerCondition",
    "TriggerDefinition",
    "TriggerHealth",
    "TriggerPriority",
    "TriggerState",
    "TriggerType",
    "TriggerDaemon",
    "TriggerStore",
]
