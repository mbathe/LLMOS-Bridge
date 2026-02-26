"""Session Context Propagator — maps trigger events to active LLM sessions.

When TriggerDaemon fires a plan in response to an external event, the LLM
needs to know *why* the plan was launched, not just *what* to execute.
The SessionContextPropagator bridges that gap.

Flow
----
1. TriggerDaemon detects a matching event (e.g. filesystem.changed).
2. Before submitting the plan to PlanExecutor, TriggerDaemon calls
   ``propagator.bind(plan_id, trigger_context)``.
3. PlanExecutor (or TemplateResolver) retrieves the context via
   ``propagator.get(plan_id)`` and makes it available as template variables:
       {{trigger.event_type}}          → "filesystem.changed"
       {{trigger.payload.path}}        → "/home/user/doc.txt"
       {{trigger.trigger_id}}          → "my-filesystem-watcher"
       {{trigger.fired_at}}            → 1234567890.123
4. When the plan finishes (success or failure), TriggerDaemon calls
   ``propagator.unbind(plan_id)`` to free memory.

Thread safety
-------------
All mutations go through ``asyncio.Lock``.  ``get()`` is lock-free (read-only
dict lookup) and safe to call from executor synchronous code paths.
"""

from __future__ import annotations

import asyncio
from typing import Any

from llmos_bridge.logging import get_logger

log = get_logger(__name__)


class SessionContextPropagator:
    """Maps plan_id → trigger context for the duration of a triggered plan.

    Usage::

        propagator = SessionContextPropagator()

        # TriggerDaemon — before submitting the plan
        await propagator.bind("plan_xyz", {
            "trigger_id": "my_trigger",
            "trigger_name": "Watch Docs Folder",
            "event_type": "filesystem.changed",
            "payload": {"path": "/home/user/doc.txt", "change": "modified"},
            "fired_at": 1_700_000_000.0,
            "session_id": "sess_abc",
        })

        # PlanExecutor / TemplateResolver — during execution
        ctx = propagator.get("plan_xyz")
        # ctx["event_type"] == "filesystem.changed"

        # TriggerDaemon — after plan completes
        await propagator.unbind("plan_xyz")
    """

    def __init__(self) -> None:
        self._contexts: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    # ---------------------------------------------------------------------------
    # Lifecycle
    # ---------------------------------------------------------------------------

    async def bind(self, plan_id: str, trigger_context: dict[str, Any]) -> None:
        """Associate a trigger context with *plan_id*.

        Overwrites any previous context for the same plan_id (safe to call
        if a plan is re-triggered while still running).

        Args:
            plan_id:         The IML plan_id that was submitted.
            trigger_context: Arbitrary dict; recommended keys:
                             trigger_id, trigger_name, event_type,
                             payload, fired_at, session_id.
        """
        async with self._lock:
            self._contexts[plan_id] = trigger_context
        log.debug(
            "session_context_bound",
            plan_id=plan_id,
            trigger_id=trigger_context.get("trigger_id"),
        )

    def get(self, plan_id: str) -> dict[str, Any] | None:
        """Return the trigger context for *plan_id*, or None if not found.

        Intentionally synchronous — safe to call from non-async executor code.
        """
        return self._contexts.get(plan_id)

    async def unbind(self, plan_id: str) -> None:
        """Remove the context for *plan_id* (called when plan finishes)."""
        async with self._lock:
            removed = self._contexts.pop(plan_id, None)
        if removed is not None:
            log.debug("session_context_unbound", plan_id=plan_id)

    # ---------------------------------------------------------------------------
    # Introspection
    # ---------------------------------------------------------------------------

    @property
    def active_count(self) -> int:
        """Number of plans currently bound to a trigger context."""
        return len(self._contexts)

    def active_plan_ids(self) -> list[str]:
        """Return a snapshot of currently bound plan IDs."""
        return list(self._contexts.keys())
