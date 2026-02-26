"""TriggerDaemon — the main orchestrator for the trigger subsystem.

TriggerDaemon is the systemd of LLMOS Bridge.  It:

1. **Loads** all active TriggerDefinitions from SQLite on startup.
2. **Arms** each trigger by creating the appropriate BaseWatcher.
3. **Receives** fire callbacks from watchers.
4. **Validates** fires (throttle, chain depth, expiry checks).
5. **Resolves** resource conflicts via ConflictResolver.
6. **Schedules** plan submission via PriorityFireScheduler.
7. **Emits** UniversalEvents for every significant state change.
8. **Persists** state changes back to SQLite.
9. **Monitors** watcher health and transitions FAILED triggers.
10. **Exposes** an API for registering/activating/deactivating triggers at runtime.

Wiring with existing LLMOS Bridge components
--------------------------------------------
TriggerDaemon receives the PlanExecutor and EventBus at construction so that
it can submit plans and emit events without coupling to HTTP or CLI layers.

Plan submission flow::

    Watcher fires
        ↓
    TriggerDaemon._on_watcher_fire()
        ↓
    Validate (can_fire, chain depth, conflict policy)
        ↓
    PriorityFireScheduler.enqueue()
        ↓
    _submit_plan()  →  PlanExecutor.submit_plan()
        ↓
    SessionContextPropagator.bind()
        ↓
    EventBus.emit("llmos.triggers", TriggerFireEvent)

Startup in server.py::

    trigger_daemon = TriggerDaemon(
        store=TriggerStore(settings.triggers.db_path),
        event_bus=event_bus,
        executor=executor,
        session_propagator=session_propagator,
        max_concurrent_plans=settings.triggers.max_concurrent_plans,
    )
    await trigger_daemon.start()
    app.state.trigger_daemon = trigger_daemon
"""

from __future__ import annotations

import asyncio
import copy
import json
import time
import uuid
from typing import Any

from llmos_bridge.events.bus import EventBus, NullEventBus
from llmos_bridge.events.models import EventPriority, UniversalEvent
from llmos_bridge.events.session import SessionContextPropagator
from llmos_bridge.logging import get_logger
from llmos_bridge.triggers.conflict import ConflictResolver
from llmos_bridge.triggers.models import (
    TriggerDefinition,
    TriggerFireEvent,
    TriggerPriority,
    TriggerState,
    TriggerType,
)
from llmos_bridge.triggers.scheduler import PriorityFireScheduler
from llmos_bridge.triggers.store import TriggerStore
from llmos_bridge.triggers.watchers.base import BaseWatcher, WatcherFactory
from llmos_bridge.triggers.watchers.composite import CompositeWatcher

log = get_logger(__name__)

# EventBus topic for trigger events
TOPIC_TRIGGERS = "llmos.triggers"


class TriggerDaemon:
    """Orchestrates all trigger watchers and plan submissions.

    This object is long-lived (same lifetime as the FastAPI app).  It must
    be started with ``await daemon.start()`` and stopped with
    ``await daemon.stop()`` during the FastAPI lifespan events.
    """

    def __init__(
        self,
        store: TriggerStore,
        event_bus: EventBus | None = None,
        executor: Any | None = None,  # PlanExecutor (avoid circular import)
        session_propagator: SessionContextPropagator | None = None,
        max_concurrent_plans: int = 5,
    ) -> None:
        self._store = store
        self._bus = event_bus or NullEventBus()
        self._executor = executor
        self._propagator = session_propagator or SessionContextPropagator()
        self._max_concurrent = max_concurrent_plans

        # trigger_id → running watcher
        self._watchers: dict[str, BaseWatcher] = {}

        # trigger_id → TriggerDefinition (in-memory cache)
        self._triggers: dict[str, TriggerDefinition] = {}

        self._conflict = ConflictResolver()
        self._scheduler: PriorityFireScheduler | None = None
        self._health_task: asyncio.Task[None] | None = None
        self._started = False

    # ---------------------------------------------------------------------------
    # Lifecycle
    # ---------------------------------------------------------------------------

    async def start(self) -> None:
        """Initialise the store, load active triggers, and start all watchers."""
        if self._started:
            return
        self._started = True

        self._scheduler = PriorityFireScheduler(
            submit_callback=self._submit_plan,
            cancel_callback=self._cancel_plan,
            max_concurrent=self._max_concurrent,
        )
        await self._scheduler.start()

        active_triggers = await self._store.load_active()
        for trigger in active_triggers:
            self._triggers[trigger.trigger_id] = trigger
            await self._arm(trigger)

        self._health_task = asyncio.create_task(self._health_loop(), name="trigger_health_monitor")
        log.info("trigger_daemon_started", active_triggers=len(active_triggers))

    async def stop(self) -> None:
        """Stop all watchers and the scheduler."""
        if not self._started:
            return
        self._started = False

        if self._health_task and not self._health_task.done():
            self._health_task.cancel()
            try:
                await self._health_task
            except asyncio.CancelledError:
                pass

        # Stop all watchers concurrently
        await asyncio.gather(
            *(w.stop() for w in self._watchers.values()),
            return_exceptions=True,
        )
        self._watchers.clear()

        if self._scheduler:
            await self._scheduler.stop()

        log.info("trigger_daemon_stopped")

    # ---------------------------------------------------------------------------
    # Trigger registration API
    # ---------------------------------------------------------------------------

    async def register(self, trigger: TriggerDefinition) -> TriggerDefinition:
        """Register and optionally activate a new trigger.

        If ``trigger.enabled`` is True, the trigger is armed immediately.
        """
        # Chain depth guard
        if trigger.chain_depth > trigger.max_chain_depth:
            raise ValueError(
                f"Trigger chain depth {trigger.chain_depth} exceeds max {trigger.max_chain_depth}"
            )

        trigger.state = TriggerState.REGISTERED
        self._triggers[trigger.trigger_id] = trigger
        await self._store.save(trigger)

        if trigger.enabled:
            await self.activate(trigger.trigger_id)

        await self._emit_trigger_event("trigger.registered", trigger, {})
        log.info("trigger_registered", trigger_id=trigger.trigger_id, name=trigger.name)
        return trigger

    async def activate(self, trigger_id: str) -> None:
        """Enable and arm a trigger."""
        trigger = await self._get_or_load(trigger_id)
        if trigger is None:
            raise KeyError(f"Trigger not found: {trigger_id}")
        trigger.enabled = True
        trigger.state = TriggerState.ACTIVE
        await self._store.save(trigger)
        await self._arm(trigger)
        await self._emit_trigger_event("trigger.activated", trigger, {})

    async def deactivate(self, trigger_id: str) -> None:
        """Disarm a trigger without deleting it."""
        trigger = await self._get_or_load(trigger_id)
        if trigger is None:
            raise KeyError(f"Trigger not found: {trigger_id}")
        await self._disarm(trigger_id)
        trigger.enabled = False
        trigger.state = TriggerState.INACTIVE
        await self._store.save(trigger)
        await self._emit_trigger_event("trigger.deactivated", trigger, {})

    async def delete(self, trigger_id: str) -> bool:
        """Disarm and permanently delete a trigger."""
        await self._disarm(trigger_id)
        self._triggers.pop(trigger_id, None)
        deleted = await self._store.delete(trigger_id)
        if deleted:
            log.info("trigger_deleted", trigger_id=trigger_id)
        return deleted

    async def get(self, trigger_id: str) -> TriggerDefinition | None:
        return await self._get_or_load(trigger_id)

    async def list_all(self) -> list[TriggerDefinition]:
        return await self._store.list_all()

    async def list_active(self) -> list[TriggerDefinition]:
        return [t for t in self._triggers.values() if t.state in (TriggerState.ACTIVE, TriggerState.WATCHING)]

    # ---------------------------------------------------------------------------
    # Watcher management
    # ---------------------------------------------------------------------------

    async def _arm(self, trigger: TriggerDefinition) -> None:
        """Create and start the watcher for *trigger*."""
        if trigger.trigger_id in self._watchers:
            await self._disarm(trigger.trigger_id)
        try:
            watcher = WatcherFactory.create(
                trigger_id=trigger.trigger_id,
                condition=trigger.condition,
                fire_callback=self._on_watcher_fire,
            )
            self._watchers[trigger.trigger_id] = watcher
            await watcher.start()
            log.debug("trigger_armed", trigger_id=trigger.trigger_id, type=trigger.condition.type.value)
        except Exception as exc:
            log.error("trigger_arm_failed", trigger_id=trigger.trigger_id, error=str(exc))
            trigger.state = TriggerState.FAILED
            trigger.health.record_fail(str(exc))
            await self._store.save(trigger)

    async def _disarm(self, trigger_id: str) -> None:
        """Stop and remove the watcher for *trigger_id*."""
        watcher = self._watchers.pop(trigger_id, None)
        if watcher is not None:
            await watcher.stop()

    # ---------------------------------------------------------------------------
    # Fire callback (called by watchers)
    # ---------------------------------------------------------------------------

    async def _on_watcher_fire(
        self, trigger_id: str, event_type: str, payload: dict[str, Any]
    ) -> None:
        """Handle a watcher fire notification."""
        trigger = self._triggers.get(trigger_id)
        if trigger is None:
            log.warning("watcher_fire_unknown_trigger", trigger_id=trigger_id)
            return

        if not trigger.can_fire():
            trigger.health.record_throttle()
            await self._store.update_state(trigger_id, TriggerState.THROTTLED)
            log.debug("trigger_fire_throttled", trigger_id=trigger_id)
            return

        fire_event = TriggerFireEvent(
            trigger_id=trigger_id,
            trigger_name=trigger.name,
            event_type=event_type,
            payload=payload,
        )

        # For COMPOSITE triggers with sub-triggers, notify other composite watchers
        await self._notify_composite_watchers(trigger_id, event_type, payload)

        if self._scheduler:
            await self._scheduler.enqueue(trigger, fire_event)

        trigger.state = TriggerState.FIRED
        await self._store.update_state(trigger_id, TriggerState.FIRED)

    async def _notify_composite_watchers(
        self, sub_trigger_id: str, event_type: str, payload: dict[str, Any]
    ) -> None:
        """Notify composite watchers that a sub-trigger fired."""
        for watcher in self._watchers.values():
            if isinstance(watcher, CompositeWatcher):
                await watcher.notify_sub_fire(sub_trigger_id, event_type, payload)

    # ---------------------------------------------------------------------------
    # Plan submission (called by scheduler)
    # ---------------------------------------------------------------------------

    async def _submit_plan(
        self, trigger: TriggerDefinition, fire_event: TriggerFireEvent
    ) -> str | None:
        """Build and submit the IML plan for a trigger fire."""
        if self._executor is None:
            log.warning("trigger_no_executor", trigger_id=trigger.trigger_id)
            return None

        start = time.time()
        plan_id = trigger.generate_plan_id()
        fire_event.plan_id = plan_id

        # Resolve resource conflict
        if trigger.resource_lock:
            acquired, holder = await self._conflict.try_acquire(
                trigger.resource_lock, plan_id, trigger.conflict_policy
            )
            if not acquired:
                if trigger.conflict_policy == "queue":
                    free = await self._conflict.wait_for_resource(trigger.resource_lock, timeout=60.0)
                    if not free:
                        log.warning("resource_wait_timeout", trigger_id=trigger.trigger_id)
                        return None
                    await self._conflict.try_acquire(trigger.resource_lock, plan_id, trigger.conflict_policy)
                elif trigger.conflict_policy == "reject":
                    log.info("trigger_fire_rejected_conflict", trigger_id=trigger.trigger_id, holder=holder)
                    return None

        # Build the plan from template + context
        plan = self._build_plan(trigger, fire_event, plan_id)

        # Bind session context for template resolution
        if self._propagator:
            await self._propagator.bind(plan_id, fire_event.as_template_context())

        # Submit to executor
        try:
            await self._executor.submit_plan(plan)
            trigger.health.record_fire(latency_ms=(time.time() - start) * 1000)
            trigger.state = TriggerState.ACTIVE  # re-arm
            await self._store.save(trigger)

            await self._emit_trigger_event("trigger.plan_submitted", trigger, fire_event.to_dict())
            log.info(
                "trigger_plan_submitted",
                trigger_id=trigger.trigger_id,
                plan_id=plan_id,
                latency_ms=round(trigger.health.avg_latency_ms, 1),
            )
            return plan_id
        except Exception as exc:
            trigger.health.record_fail(str(exc))
            await self._store.save(trigger)
            if trigger.resource_lock:
                self._conflict.release(trigger.resource_lock, plan_id)
            log.error("trigger_submit_failed", trigger_id=trigger.trigger_id, error=str(exc))
            return None

    async def _cancel_plan(self, plan_id: str) -> None:
        """Cancel a running plan (used for preemption)."""
        if self._executor and hasattr(self._executor, "cancel_plan"):
            try:
                await self._executor.cancel_plan(plan_id)
            except Exception as exc:
                log.warning("trigger_cancel_error", plan_id=plan_id, error=str(exc))

    def _build_plan(
        self,
        trigger: TriggerDefinition,
        fire_event: TriggerFireEvent,
        plan_id: str,
    ) -> dict[str, Any]:
        """Deep-copy the plan template and inject the plan_id + trigger context."""
        plan = copy.deepcopy(trigger.plan_template)
        plan["plan_id"] = plan_id
        plan.setdefault("protocol_version", "2.0")
        plan.setdefault("execution_mode", "reactive")
        plan.setdefault("metadata", {})
        plan["metadata"]["trigger_id"] = trigger.trigger_id
        plan["metadata"]["trigger_name"] = trigger.name
        plan["metadata"]["event_type"] = fire_event.event_type
        plan["metadata"]["fired_at"] = fire_event.fired_at
        return plan

    # ---------------------------------------------------------------------------
    # Health monitoring
    # ---------------------------------------------------------------------------

    async def _health_loop(self) -> None:
        """Periodically check watcher health and purge expired triggers."""
        while True:
            try:
                await asyncio.sleep(30)
                await self._check_health()
                await self._store.purge_expired()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.error("trigger_health_loop_error", error=str(exc))

    async def _check_health(self) -> None:
        """Transition triggers with crashed watchers to FAILED state."""
        for trigger_id, watcher in list(self._watchers.items()):
            if watcher.error is not None:
                trigger = self._triggers.get(trigger_id)
                if trigger and trigger.state != TriggerState.FAILED:
                    trigger.state = TriggerState.FAILED
                    trigger.health.record_fail(watcher.error)
                    await self._store.save(trigger)
                    await self._emit_trigger_event("trigger.failed", trigger, {"error": watcher.error})
                    log.warning("trigger_marked_failed", trigger_id=trigger_id, error=watcher.error)

    # ---------------------------------------------------------------------------
    # Helpers
    # ---------------------------------------------------------------------------

    async def _get_or_load(self, trigger_id: str) -> TriggerDefinition | None:
        trigger = self._triggers.get(trigger_id)
        if trigger is None:
            trigger = await self._store.get(trigger_id)
            if trigger:
                self._triggers[trigger_id] = trigger
        return trigger

    async def _emit_trigger_event(
        self,
        event_type: str,
        trigger: TriggerDefinition,
        extra: dict[str, Any],
    ) -> None:
        event = UniversalEvent(
            type=event_type,
            topic=TOPIC_TRIGGERS,
            source="trigger_daemon",
            payload={
                "trigger_id": trigger.trigger_id,
                "trigger_name": trigger.name,
                "state": trigger.state.value,
                **extra,
            },
            priority=EventPriority(max(0, 4 - int(trigger.priority))),  # map TriggerPriority → EventPriority
        )
        await self._bus.emit(TOPIC_TRIGGERS, event.to_dict())
