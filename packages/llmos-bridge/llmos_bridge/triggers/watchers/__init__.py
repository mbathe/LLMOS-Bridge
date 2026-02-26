"""Trigger watcher implementations.

Each watcher monitors one type of event source and calls a ``fire_callback``
when its condition is met.

Available watchers
------------------
BaseWatcher         — abstract base class (watchers/base.py)
CronWatcher         — fires on a cron schedule (temporal.py)
IntervalWatcher     — fires every N seconds (temporal.py)
OnceWatcher         — fires once at a specific time (temporal.py)
FileSystemWatcher   — fires on file/directory changes (system.py)
ProcessWatcher      — fires when a process starts/stops (system.py)
ResourceWatcher     — fires when CPU/RAM/disk crosses a threshold (system.py)
CompositeWatcher    — combines multiple watchers with AND/OR/NOT/SEQ/WINDOW (composite.py)
"""

from llmos_bridge.triggers.watchers.base import BaseWatcher, WatcherFactory
from llmos_bridge.triggers.watchers.temporal import CronWatcher, IntervalWatcher, OnceWatcher
from llmos_bridge.triggers.watchers.system import (
    FileSystemWatcher,
    ProcessWatcher,
    ResourceWatcher,
)
from llmos_bridge.triggers.watchers.composite import CompositeWatcher

__all__ = [
    "BaseWatcher",
    "WatcherFactory",
    "CronWatcher",
    "IntervalWatcher",
    "OnceWatcher",
    "FileSystemWatcher",
    "ProcessWatcher",
    "ResourceWatcher",
    "CompositeWatcher",
]
