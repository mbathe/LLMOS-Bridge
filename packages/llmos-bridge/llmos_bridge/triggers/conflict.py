"""ConflictResolver — prevents simultaneous execution of conflicting triggers.

Two triggers conflict when:
1. They share the same ``resource_lock`` string, OR
2. They have the same ``trigger_id`` and ``conflict_policy != "queue"``

Policies
--------
queue   (default) — wait until the resource is free, then run
preempt           — cancel lower-priority plan holding the resource, run now
reject            — discard the incoming fire if resource is locked

The ConflictResolver maintains a lock table mapping resource names to the
currently running plan_id.  It is consulted by PriorityFireScheduler before
submitting each plan.

This is a simple in-memory lock table.  For multi-process deployments
(future Phase 5), this should be backed by Redis or a shared SQLite table.
"""

from __future__ import annotations

import asyncio
from typing import Literal

from llmos_bridge.logging import get_logger

log = get_logger(__name__)

ConflictPolicy = Literal["queue", "preempt", "reject"]


class ConflictResolver:
    """In-memory resource lock table for triggered plans.

    Usage::

        resolver = ConflictResolver()

        # Before submitting a plan:
        async with resolver.acquire("my_resource", plan_id="plan_xyz", policy="queue"):
            await executor.submit(plan)

        # The lock is released when the context manager exits.
    """

    def __init__(self) -> None:
        self._locks: dict[str, str] = {}   # resource_name → plan_id
        self._waiters: dict[str, asyncio.Event] = {}  # resource_name → release event
        self._mutex = asyncio.Lock()

    # ---------------------------------------------------------------------------
    # Lock / unlock API
    # ---------------------------------------------------------------------------

    async def try_acquire(
        self, resource: str, plan_id: str, policy: ConflictPolicy = "queue"
    ) -> tuple[bool, str | None]:
        """Attempt to acquire *resource* for *plan_id*.

        Returns:
            (acquired, existing_plan_id)
            - (True, None)  — lock acquired
            - (False, pid)  — lock held by pid; action depends on policy
        """
        async with self._mutex:
            holder = self._locks.get(resource)
            if holder is None:
                self._locks[resource] = plan_id
                log.debug("resource_locked", resource=resource, plan_id=plan_id)
                return True, None
            return False, holder

    async def wait_for_resource(self, resource: str, timeout: float = 300.0) -> bool:
        """Block until *resource* is released or *timeout* elapses.

        Returns True if the resource became free within *timeout*.
        """
        event = self._waiters.setdefault(resource, asyncio.Event())
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    def release(self, resource: str, plan_id: str) -> None:
        """Release *resource* held by *plan_id*.

        Notifies any waiters so they can re-attempt acquisition.
        """
        holder = self._locks.get(resource)
        if holder == plan_id:
            del self._locks[resource]
            log.debug("resource_released", resource=resource, plan_id=plan_id)
            event = self._waiters.pop(resource, None)
            if event is not None:
                event.set()

    def is_locked(self, resource: str) -> bool:
        """Return True if *resource* is currently locked."""
        return resource in self._locks

    def holder_of(self, resource: str) -> str | None:
        """Return the plan_id currently holding *resource*, or None."""
        return self._locks.get(resource)

    @property
    def locked_resources(self) -> dict[str, str]:
        """Snapshot of all currently locked resources."""
        return dict(self._locks)
