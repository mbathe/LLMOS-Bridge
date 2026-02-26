"""Priority-based fire scheduler for TriggerDaemon.

The scheduler sits between the watchers (which detect events) and the
PlanExecutor (which runs IML plans).  Its responsibilities:

1. **Priority queue** — higher-priority fires are submitted first when
   multiple triggers fire simultaneously.

2. **Concurrency limit** — at most ``max_concurrent`` triggered plans run
   in parallel.  Additional fires are queued.

3. **Preemption** — a CRITICAL or HIGH priority fire can cancel an
   already-running LOWER-priority plan if the trigger's conflict_policy
   is "preempt".

4. **Throttle tracking** — counts fires per trigger per hour to enforce
   ``max_fires_per_hour``.

The scheduler is intentionally simple: it processes the queue in an async
loop rather than using a complex scheduling algorithm.  This is sufficient
for the expected load (<<100 concurrent triggers).
"""

from __future__ import annotations

import asyncio
import heapq
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from llmos_bridge.logging import get_logger
from llmos_bridge.triggers.models import TriggerDefinition, TriggerFireEvent, TriggerPriority

log = get_logger(__name__)

# Callback type: async fn(trigger, fire_event) → plan_id | None
SubmitCallback = Callable[[TriggerDefinition, TriggerFireEvent], Awaitable[str | None]]
CancelCallback = Callable[[str], Awaitable[None]]  # cancel by plan_id


@dataclass(order=True)
class _QueuedFire:
    """Internal priority-queue item."""

    # Negated priority so higher TriggerPriority = earlier dequeue
    neg_priority: int = field(compare=True)
    sequence: int = field(compare=True)       # tie-breaker: FIFO within priority
    trigger: TriggerDefinition = field(compare=False)
    fire_event: TriggerFireEvent = field(compare=False)


class PriorityFireScheduler:
    """Manages ordered submission of triggered plans to PlanExecutor.

    Usage::

        scheduler = PriorityFireScheduler(
            submit_callback=executor_submit_fn,
            cancel_callback=executor_cancel_fn,
            max_concurrent=5,
        )
        await scheduler.start()

        # TriggerDaemon calls this when a watcher fires
        await scheduler.enqueue(trigger_def, fire_event)

        await scheduler.stop()
    """

    def __init__(
        self,
        submit_callback: SubmitCallback,
        cancel_callback: CancelCallback,
        max_concurrent: int = 5,
    ) -> None:
        self._submit = submit_callback
        self._cancel = cancel_callback
        self._max_concurrent = max_concurrent

        self._heap: list[_QueuedFire] = []
        self._sequence: int = 0
        self._heap_lock = asyncio.Lock()
        self._work_available = asyncio.Event()

        # plan_id → (priority, trigger_id) for preemption decisions
        self._running: dict[str, tuple[int, str]] = {}

        # Rate limiting: trigger_id → deque of fire timestamps (last hour)
        self._fire_times: dict[str, list[float]] = {}

        self._task: asyncio.Task[None] | None = None

    # ---------------------------------------------------------------------------
    # Lifecycle
    # ---------------------------------------------------------------------------

    async def start(self) -> None:
        """Start the scheduling loop."""
        self._task = asyncio.create_task(self._loop(), name="fire_scheduler")
        log.debug("fire_scheduler_started", max_concurrent=self._max_concurrent)

    async def stop(self) -> None:
        """Stop the scheduling loop."""
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        log.debug("fire_scheduler_stopped")

    # ---------------------------------------------------------------------------
    # Public interface
    # ---------------------------------------------------------------------------

    async def enqueue(self, trigger: TriggerDefinition, fire_event: TriggerFireEvent) -> None:
        """Add a trigger fire to the priority queue.

        If the trigger has exceeded ``max_fires_per_hour``, the fire is
        silently dropped and ``TriggerState.THROTTLED`` is set by the caller.
        """
        if not self._check_rate(trigger):
            log.warning("trigger_throttled_by_scheduler", trigger_id=trigger.trigger_id)
            return

        item = _QueuedFire(
            neg_priority=-int(trigger.priority),
            sequence=self._sequence,
            trigger=trigger,
            fire_event=fire_event,
        )
        self._sequence += 1

        async with self._heap_lock:
            heapq.heappush(self._heap, item)
        self._work_available.set()
        log.debug(
            "fire_enqueued",
            trigger_id=trigger.trigger_id,
            priority=trigger.priority.name,
            queue_depth=len(self._heap),
        )

    @property
    def queue_depth(self) -> int:
        return len(self._heap)

    @property
    def running_count(self) -> int:
        return len(self._running)

    def on_plan_completed(self, plan_id: str) -> None:
        """Called by TriggerDaemon when a triggered plan finishes."""
        self._running.pop(plan_id, None)
        # Wake the loop to process queued items
        self._work_available.set()

    # ---------------------------------------------------------------------------
    # Internal loop
    # ---------------------------------------------------------------------------

    async def _loop(self) -> None:
        while True:
            await self._work_available.wait()
            self._work_available.clear()

            while len(self._running) < self._max_concurrent:
                async with self._heap_lock:
                    if not self._heap:
                        break
                    item = heapq.heappop(self._heap)

                trigger = item.trigger
                fire_event = item.fire_event

                # Check preemption
                if trigger.conflict_policy == "preempt":
                    await self._maybe_preempt(trigger)
                elif trigger.conflict_policy == "reject":
                    if self._has_running_for(trigger.trigger_id):
                        log.debug("trigger_fire_rejected", trigger_id=trigger.trigger_id)
                        continue

                # Submit
                try:
                    plan_id = await self._submit(trigger, fire_event)
                    if plan_id:
                        self._running[plan_id] = (int(trigger.priority), trigger.trigger_id)
                        self._record_fire(trigger)
                        log.info(
                            "trigger_plan_submitted",
                            trigger_id=trigger.trigger_id,
                            plan_id=plan_id,
                            priority=trigger.priority.name,
                        )
                except Exception as exc:
                    log.error(
                        "trigger_submit_error",
                        trigger_id=trigger.trigger_id,
                        error=str(exc),
                    )

    # ---------------------------------------------------------------------------
    # Rate limiting
    # ---------------------------------------------------------------------------

    def _check_rate(self, trigger: TriggerDefinition) -> bool:
        """Return False if the trigger has exceeded max_fires_per_hour."""
        if trigger.max_fires_per_hour <= 0:
            return True
        now = time.time()
        times = self._fire_times.setdefault(trigger.trigger_id, [])
        # Evict entries older than 1 hour
        cutoff = now - 3600
        times[:] = [t for t in times if t > cutoff]
        return len(times) < trigger.max_fires_per_hour

    def _record_fire(self, trigger: TriggerDefinition) -> None:
        times = self._fire_times.setdefault(trigger.trigger_id, [])
        times.append(time.time())

    # ---------------------------------------------------------------------------
    # Preemption / conflict helpers
    # ---------------------------------------------------------------------------

    def _has_running_for(self, trigger_id: str) -> bool:
        return any(tid == trigger_id for _, tid in self._running.values())

    async def _maybe_preempt(self, trigger: TriggerDefinition) -> None:
        """Cancel running plans with lower priority owned by the same trigger."""
        to_cancel = [
            plan_id
            for plan_id, (prio, tid) in self._running.items()
            if tid == trigger.trigger_id and prio < int(trigger.priority)
        ]
        for plan_id in to_cancel:
            log.info("trigger_preempting_plan", trigger_id=trigger.trigger_id, plan_id=plan_id)
            try:
                await self._cancel(plan_id)
            except Exception as exc:
                log.warning("trigger_preempt_error", plan_id=plan_id, error=str(exc))
