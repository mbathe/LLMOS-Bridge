"""Temporal trigger watchers — cron, interval, and one-shot.

These watchers fire based on time alone, with no external event source.

CronWatcher      — fires on a cron schedule ("0 9 * * 1-5")
IntervalWatcher  — fires every N seconds
OnceWatcher      — fires once at a specific Unix timestamp, then stops

Dependencies
------------
CronWatcher requires the ``croniter`` package (optional).
If ``croniter`` is not installed, CronWatcher raises ``ImportError``
with a helpful message.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from llmos_bridge.logging import get_logger
from llmos_bridge.triggers.models import TriggerCondition
from llmos_bridge.triggers.watchers.base import BaseWatcher, FireCallback

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# IntervalWatcher
# ---------------------------------------------------------------------------


class IntervalWatcher(BaseWatcher):
    """Fires every ``interval_seconds`` seconds.

    Condition params::

        {"interval_seconds": 60.0}   # fire every minute

    The first fire happens after one full interval (no immediate fire on start).
    """

    def __init__(
        self,
        trigger_id: str,
        condition: TriggerCondition,
        fire_callback: FireCallback,
    ) -> None:
        super().__init__(trigger_id, condition, fire_callback)
        self._interval = float(condition.params.get("interval_seconds", 60.0))
        if self._interval <= 0:
            raise ValueError(f"interval_seconds must be positive, got {self._interval}")

    async def _run(self) -> None:
        log.debug("interval_watcher_started", trigger_id=self._trigger_id, interval=self._interval)
        while not self._stopped:
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self._interval
                )
                return  # stop_event was set
            except asyncio.TimeoutError:
                pass  # interval elapsed — fall through to fire
            if self._stopped:
                return
            await self._fire(
                "temporal.interval",
                {"interval_seconds": self._interval, "fired_at": time.time()},
            )


# ---------------------------------------------------------------------------
# OnceWatcher
# ---------------------------------------------------------------------------


class OnceWatcher(BaseWatcher):
    """Fires once at a specific Unix timestamp, then stops.

    Condition params::

        {"run_at": 1_700_000_000.0}   # specific Unix timestamp

    If ``run_at`` is in the past, fires immediately.
    """

    def __init__(
        self,
        trigger_id: str,
        condition: TriggerCondition,
        fire_callback: FireCallback,
    ) -> None:
        super().__init__(trigger_id, condition, fire_callback)
        self._run_at = float(condition.params["run_at"])

    async def _run(self) -> None:
        now = time.time()
        delay = max(0.0, self._run_at - now)
        log.debug("once_watcher_started", trigger_id=self._trigger_id, delay_seconds=delay)
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
            return  # stopped before firing
        except asyncio.TimeoutError:
            pass
        if self._stopped:
            return
        await self._fire("temporal.once", {"run_at": self._run_at, "fired_at": time.time()})
        # OnceWatcher does not loop — exits after one fire


# ---------------------------------------------------------------------------
# CronWatcher
# ---------------------------------------------------------------------------


class CronWatcher(BaseWatcher):
    """Fires according to a cron expression.

    Condition params::

        {"schedule": "0 9 * * 1-5"}   # 09:00 Monday–Friday

    Requires the ``croniter`` package::

        pip install croniter

    CronWatcher calculates the next fire time after each fire, sleeping
    until then.  Drift is corrected on each iteration.
    """

    def __init__(
        self,
        trigger_id: str,
        condition: TriggerCondition,
        fire_callback: FireCallback,
    ) -> None:
        super().__init__(trigger_id, condition, fire_callback)
        self._schedule = condition.params.get("schedule", "")
        if not self._schedule:
            raise ValueError("CronWatcher requires 'schedule' in condition params")

    async def _run(self) -> None:
        try:
            from croniter import croniter  # type: ignore[import]
        except ImportError as exc:
            self.error = "croniter package not installed. Run: pip install croniter"
            log.error("cron_watcher_missing_dep", trigger_id=self._trigger_id, error=self.error)
            raise ImportError(self.error) from exc

        log.debug("cron_watcher_started", trigger_id=self._trigger_id, schedule=self._schedule)
        cron = croniter(self._schedule, time.time())
        while not self._stopped:
            next_fire = cron.get_next(float)
            delay = max(0.0, next_fire - time.time())
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
                return  # stopped
            except asyncio.TimeoutError:
                pass
            if self._stopped:
                return
            now = time.time()
            await self._fire(
                "temporal.cron",
                {"schedule": self._schedule, "scheduled_at": next_fire, "fired_at": now},
            )
            # Advance croniter past the fire point to compute the *next* slot
            cron = croniter(self._schedule, now)
