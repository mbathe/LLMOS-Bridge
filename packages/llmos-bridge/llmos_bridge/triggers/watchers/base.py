"""BaseWatcher — abstract base class for all trigger watchers.

A watcher runs in the background as an asyncio task.  When its condition
is satisfied it invokes the ``fire_callback`` supplied at construction.

Contract
--------
- ``start()``  — starts the background asyncio task
- ``stop()``   — cancels the task and cleans up resources
- ``is_running`` — True between start() and stop()

The ``fire_callback`` signature::

    async def on_fire(trigger_id: str, event_type: str, payload: dict) -> None:
        ...

Implementations must:
1. Override ``_run()`` — the main loop / watch logic
2. Call ``await self._fire(event_type, payload)`` when the condition fires
3. Never let exceptions propagate out of ``_run()`` — catch + log + continue
   (or set self._error and return to signal a FAILED state)
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Any, Awaitable, Callable

from llmos_bridge.logging import get_logger
from llmos_bridge.triggers.models import TriggerCondition, TriggerType

log = get_logger(__name__)

FireCallback = Callable[[str, str, dict[str, Any]], Awaitable[None]]
"""
Signature: async def callback(trigger_id: str, event_type: str, payload: dict) -> None
"""


class BaseWatcher(ABC):
    """Abstract base for all trigger watchers."""

    def __init__(
        self,
        trigger_id: str,
        condition: TriggerCondition,
        fire_callback: FireCallback,
    ) -> None:
        self._trigger_id = trigger_id
        self._condition = condition
        self._fire_callback = fire_callback
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self.error: str | None = None  # set on unrecoverable failure

    # ---------------------------------------------------------------------------
    # Public API
    # ---------------------------------------------------------------------------

    async def start(self) -> None:
        """Start the watcher background task."""
        if self._task is not None and not self._task.done():
            return  # already running
        self._stop_event.clear()
        self._task = asyncio.create_task(self._guarded_run(), name=f"watcher_{self._trigger_id}")
        log.debug("watcher_started", trigger_id=self._trigger_id, type=self._condition.type.value)

    async def stop(self) -> None:
        """Signal the watcher to stop and wait for it to finish."""
        self._stop_event.set()
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        log.debug("watcher_stopped", trigger_id=self._trigger_id)

    @property
    def is_running(self) -> bool:
        """True if the background task is alive."""
        return self._task is not None and not self._task.done()

    # ---------------------------------------------------------------------------
    # Abstract implementation hook
    # ---------------------------------------------------------------------------

    @abstractmethod
    async def _run(self) -> None:
        """Main watch loop.  Run until ``self._stop_event`` is set.

        Call ``await self._fire(event_type, payload)`` when the condition fires.
        On unrecoverable error: set ``self.error = message`` and return.
        """

    # ---------------------------------------------------------------------------
    # Internal helpers
    # ---------------------------------------------------------------------------

    async def _guarded_run(self) -> None:
        """Wrap ``_run()`` so exceptions don't kill the event loop."""
        try:
            await self._run()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.error = str(exc)
            log.error("watcher_crashed", trigger_id=self._trigger_id, error=str(exc))

    async def _fire(self, event_type: str, payload: dict[str, Any]) -> None:
        """Invoke the fire callback.  Errors are caught and logged."""
        try:
            await self._fire_callback(self._trigger_id, event_type, payload)
        except Exception as exc:
            log.error(
                "watcher_fire_callback_error",
                trigger_id=self._trigger_id,
                error=str(exc),
            )

    @property
    def _stopped(self) -> bool:
        """True if stop has been requested."""
        return self._stop_event.is_set()


# ---------------------------------------------------------------------------
# WatcherFactory
# ---------------------------------------------------------------------------


class WatcherFactory:
    """Creates the correct BaseWatcher subclass for a given TriggerType.

    Usage::

        watcher = WatcherFactory.create(trigger_id, condition, callback)
        await watcher.start()
    """

    @staticmethod
    def create(
        trigger_id: str,
        condition: TriggerCondition,
        fire_callback: FireCallback,
    ) -> BaseWatcher:
        """Instantiate the appropriate watcher for *condition.type*."""
        from llmos_bridge.triggers.watchers.temporal import (
            CronWatcher,
            IntervalWatcher,
            OnceWatcher,
        )
        from llmos_bridge.triggers.watchers.system import (
            FileSystemWatcher,
            ProcessWatcher,
            ResourceWatcher,
        )
        from llmos_bridge.triggers.watchers.composite import CompositeWatcher

        cls_map = {
            TriggerType.TEMPORAL: _pick_temporal,
            TriggerType.FILESYSTEM: lambda tid, cond, cb: FileSystemWatcher(tid, cond, cb),
            TriggerType.PROCESS: lambda tid, cond, cb: ProcessWatcher(tid, cond, cb),
            TriggerType.RESOURCE: lambda tid, cond, cb: ResourceWatcher(tid, cond, cb),
            TriggerType.COMPOSITE: lambda tid, cond, cb: CompositeWatcher(tid, cond, cb),
        }
        factory_fn = cls_map.get(condition.type)
        if factory_fn is None:
            raise ValueError(f"No watcher implementation for trigger type: {condition.type!r}")
        return factory_fn(trigger_id, condition, fire_callback)


def _pick_temporal(
    trigger_id: str,
    condition: TriggerCondition,
    fire_callback: FireCallback,
) -> BaseWatcher:
    """Select the right temporal watcher based on params."""
    from llmos_bridge.triggers.watchers.temporal import CronWatcher, IntervalWatcher, OnceWatcher

    params = condition.params
    if "schedule" in params:
        return CronWatcher(trigger_id, condition, fire_callback)
    if "interval_seconds" in params:
        return IntervalWatcher(trigger_id, condition, fire_callback)
    if "run_at" in params:
        return OnceWatcher(trigger_id, condition, fire_callback)
    raise ValueError(f"TEMPORAL trigger must have 'schedule', 'interval_seconds', or 'run_at' in params: {params}")
