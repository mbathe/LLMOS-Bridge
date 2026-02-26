"""Composite trigger watcher — combines multiple triggers with logic operators.

Operators
---------
AND     All sub-triggers must fire (within optional ``timeout_seconds``)
OR      Any one sub-trigger fires
NOT     Fires when NONE of the sub-triggers have fired recently
SEQ     Sub-triggers must fire in order within ``timeout_seconds``
WINDOW  A single sub-trigger must fire ``count`` times in ``window_seconds``

Condition params::

    # AND — all of t1 AND t2 must fire within 60 s
    {"operator": "AND", "trigger_ids": ["t1", "t2"], "timeout_seconds": 60}

    # OR — t1 OR t2
    {"operator": "OR", "trigger_ids": ["t1", "t2"]}

    # NOT — fires every check_interval_seconds if t1 has NOT fired recently
    {"operator": "NOT", "trigger_ids": ["t1"], "silence_seconds": 300,
     "check_interval_seconds": 60}

    # SEQ — t1 then t2 in order, within 120 s
    {"operator": "SEQ", "trigger_ids": ["t1", "t2"], "timeout_seconds": 120}

    # WINDOW — t1 fires 3 times within 5 min
    {"operator": "WINDOW", "trigger_ids": ["t1"], "count": 3, "window_seconds": 300}

Implementation note
-------------------
CompositeWatcher does NOT create real BaseWatcher instances for its sub-triggers.
Instead, TriggerDaemon holds a reference to the CompositeWatcher and calls
``notify_sub_fire(sub_trigger_id, event_type, payload)`` whenever any sub-trigger
fires.  This avoids nested watcher hierarchies.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any

from llmos_bridge.logging import get_logger
from llmos_bridge.triggers.models import TriggerCondition
from llmos_bridge.triggers.watchers.base import BaseWatcher, FireCallback

log = get_logger(__name__)


class CompositeWatcher(BaseWatcher):
    """Combines multiple sub-trigger fires with a logical operator.

    Usage by TriggerDaemon::

        watcher = CompositeWatcher(trigger_id, condition, callback)
        await watcher.start()

        # When a sub-trigger fires:
        await watcher.notify_sub_fire("sub_trigger_id", "event_type", payload)
    """

    def __init__(
        self,
        trigger_id: str,
        condition: TriggerCondition,
        fire_callback: FireCallback,
    ) -> None:
        super().__init__(trigger_id, condition, fire_callback)
        params = condition.params
        self._operator: str = params.get("operator", "OR").upper()
        self._sub_ids: list[str] = list(params.get("trigger_ids", []))
        self._timeout: float = float(params.get("timeout_seconds", 60.0))
        self._silence: float = float(params.get("silence_seconds", 300.0))  # NOT only
        self._check_interval: float = float(params.get("check_interval_seconds", 60.0))  # NOT only
        self._count: int = int(params.get("count", 1))  # WINDOW only
        self._window: float = float(params.get("window_seconds", 300.0))  # WINDOW only

        # State
        self._fires: dict[str, float] = {}     # sub_id → last fire timestamp
        self._seq_pos: int = 0                  # SEQ: index of next expected sub-trigger
        self._seq_start: float | None = None    # SEQ: when sequence started
        self._window_times: deque[float] = deque()  # WINDOW: timestamps of recent fires
        self._queue: asyncio.Queue[tuple[str, str, dict[str, Any]]] = asyncio.Queue()

    # ---------------------------------------------------------------------------
    # External API — called by TriggerDaemon
    # ---------------------------------------------------------------------------

    async def notify_sub_fire(
        self, sub_trigger_id: str, event_type: str, payload: dict[str, Any]
    ) -> None:
        """Called by TriggerDaemon when a sub-trigger fires."""
        if sub_trigger_id in self._sub_ids:
            await self._queue.put((sub_trigger_id, event_type, payload))

    # ---------------------------------------------------------------------------
    # BaseWatcher implementation
    # ---------------------------------------------------------------------------

    async def _run(self) -> None:
        log.debug(
            "composite_watcher_started",
            trigger_id=self._trigger_id,
            operator=self._operator,
            subs=self._sub_ids,
        )
        if self._operator == "NOT":
            await self._run_not()
        else:
            await self._run_event_loop()

    async def _run_event_loop(self) -> None:
        """Process sub-fire notifications for AND / OR / SEQ / WINDOW."""
        while not self._stopped:
            try:
                sub_id, event_type, payload = await asyncio.wait_for(
                    self._queue.get(), timeout=1.0
                )
            except asyncio.TimeoutError:
                self._cleanup_expired()
                continue

            self._fires[sub_id] = time.time()
            fired, composite_payload = self._evaluate(sub_id, event_type, payload)
            if fired:
                await self._fire("composite.fired", composite_payload)
                self._reset_state()

    async def _run_not(self) -> None:
        """NOT operator — fires when sub-trigger has been silent for ``silence_seconds``."""
        while not self._stopped:
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._check_interval)
                return
            except asyncio.TimeoutError:
                pass
            # Drain any pending sub-fire notifications (to update last-fire time)
            while not self._queue.empty():
                try:
                    sub_id, _, _ = self._queue.get_nowait()
                    self._fires[sub_id] = time.time()
                except asyncio.QueueEmpty:
                    break
            # Check if all sub-triggers have been silent
            now = time.time()
            all_silent = all(
                sub_id not in self._fires or now - self._fires[sub_id] > self._silence
                for sub_id in self._sub_ids
            )
            if all_silent:
                await self._fire("composite.not_fired", {"silence_seconds": self._silence})

    # ---------------------------------------------------------------------------
    # Logic evaluation
    # ---------------------------------------------------------------------------

    def _evaluate(
        self, sub_id: str, event_type: str, payload: dict[str, Any]
    ) -> tuple[bool, dict[str, Any]]:
        """Return (should_fire, composite_payload)."""
        base = {"operator": self._operator, "sub_trigger_id": sub_id, "event_type": event_type, "payload": payload}

        if self._operator == "OR":
            return True, base

        if self._operator == "AND":
            all_fired = all(
                s in self._fires and time.time() - self._fires[s] < self._timeout
                for s in self._sub_ids
            )
            return all_fired, base

        if self._operator == "SEQ":
            expected = self._sub_ids[self._seq_pos] if self._seq_pos < len(self._sub_ids) else None
            if sub_id == expected:
                if self._seq_pos == 0:
                    self._seq_start = time.time()
                self._seq_pos += 1
                # Check timeout
                if self._seq_start is not None and time.time() - self._seq_start > self._timeout:
                    self._seq_pos = 0
                    self._seq_start = None
                    return False, {}
                if self._seq_pos >= len(self._sub_ids):
                    return True, base
            else:
                self._seq_pos = 0
                self._seq_start = None
            return False, {}

        if self._operator == "WINDOW":
            now = time.time()
            self._window_times.append(now)
            # Evict entries outside the window
            while self._window_times and now - self._window_times[0] > self._window:
                self._window_times.popleft()
            if len(self._window_times) >= self._count:
                return True, {**base, "count": len(self._window_times), "window_seconds": self._window}
            return False, {}

        return False, {}

    def _cleanup_expired(self) -> None:
        """Remove stale fire records (older than timeout) for AND/SEQ."""
        now = time.time()
        self._fires = {k: v for k, v in self._fires.items() if now - v < self._timeout}

    def _reset_state(self) -> None:
        """Reset after a successful composite fire."""
        self._fires.clear()
        self._seq_pos = 0
        self._seq_start = None
        self._window_times.clear()
