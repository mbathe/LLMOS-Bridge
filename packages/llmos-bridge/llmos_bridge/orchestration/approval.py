"""Orchestration layer — Approval gate.

The ApprovalGate coordinates asynchronous approval decisions between the
executor (which waits) and the API layer (which signals).  Each pending
approval is tracked by an ``asyncio.Event`` that the executor awaits and
the API sets when a decision arrives.

Usage (executor side)::

    gate = ApprovalGate(default_timeout=300)
    response = await gate.request_approval(request, timeout=60)
    if response.decision == ApprovalDecision.APPROVE:
        # proceed with execution

Usage (API side)::

    gate.submit_decision(plan_id, action_id, response)
    # This wakes up the executor coroutine waiting on the event.

The gate also maintains a session-scoped auto-approve list: when a user
sends ``APPROVE_ALWAYS``, subsequent requests for the same ``module.action``
pair are automatically approved without waiting.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ApprovalDecision(str, Enum):
    """Decision types for action approval."""

    APPROVE = "approve"
    REJECT = "reject"
    SKIP = "skip"
    MODIFY = "modify"
    APPROVE_ALWAYS = "approve_always"


@dataclass
class ApprovalRequest:
    """Describes an action awaiting user approval."""

    plan_id: str
    action_id: str
    module: str
    action_name: str
    params: dict[str, Any]
    risk_level: str = "medium"
    description: str = ""
    requires_approval_reason: str = "config_rule"
    clarification_options: list[str] = field(default_factory=list)
    requested_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "plan_id": self.plan_id,
            "action_id": self.action_id,
            "module": self.module,
            "action": self.action_name,
            "params": self.params,
            "risk_level": self.risk_level,
            "description": self.description,
            "requires_approval_reason": self.requires_approval_reason,
            "requested_at": self.requested_at,
        }
        if self.clarification_options:
            d["clarification_options"] = self.clarification_options
        return d


@dataclass
class ApprovalResponse:
    """The user's decision on a pending approval request."""

    decision: ApprovalDecision
    modified_params: dict[str, Any] | None = None
    reason: str | None = None
    approved_by: str | None = None
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "modified_params": self.modified_params,
            "reason": self.reason,
            "approved_by": self.approved_by,
            "timestamp": self.timestamp,
        }


class _PendingEntry:
    """Internal tracking for a single pending approval."""

    __slots__ = ("request", "event", "response")

    def __init__(self, request: ApprovalRequest) -> None:
        self.request = request
        self.event = asyncio.Event()
        self.response: ApprovalResponse | None = None


class ApprovalGate:
    """Coordinates approval decisions between executor and API.

    Thread-safety: All methods are designed for single-event-loop use
    (the standard asyncio model).  The gate is NOT thread-safe and must
    only be accessed from the event loop that created it.
    """

    def __init__(
        self,
        default_timeout: float = 300.0,
        default_timeout_behavior: str = "reject",
    ) -> None:
        self._default_timeout = default_timeout
        self._default_timeout_behavior = default_timeout_behavior
        # (plan_id, action_id) → _PendingEntry
        self._pending: dict[tuple[str, str], _PendingEntry] = {}
        # "module.action" keys that have been auto-approved via APPROVE_ALWAYS
        self._auto_approve: set[str] = set()

    # ------------------------------------------------------------------
    # Executor side
    # ------------------------------------------------------------------

    async def request_approval(
        self,
        request: ApprovalRequest,
        timeout: float | None = None,
        timeout_behavior: str | None = None,
    ) -> ApprovalResponse:
        """Block until a decision arrives or the timeout expires.

        Args:
            request: The approval request details.
            timeout: Seconds to wait.  None = use default_timeout.
            timeout_behavior: What to do on timeout.  None = use default.

        Returns:
            The user's decision (or a synthetic REJECT/SKIP on timeout).
        """
        effective_timeout = timeout if timeout is not None else self._default_timeout
        effective_behavior = timeout_behavior or self._default_timeout_behavior

        key = (request.plan_id, request.action_id)
        entry = _PendingEntry(request)
        self._pending[key] = entry

        try:
            await asyncio.wait_for(entry.event.wait(), timeout=effective_timeout)
        except asyncio.TimeoutError:
            # Build a synthetic response based on timeout_behavior.
            if effective_behavior == "skip":
                decision = ApprovalDecision.SKIP
            else:
                decision = ApprovalDecision.REJECT
            entry.response = ApprovalResponse(
                decision=decision,
                reason=f"Approval timed out after {effective_timeout}s",
            )
        finally:
            self._pending.pop(key, None)

        assert entry.response is not None
        return entry.response

    # ------------------------------------------------------------------
    # API side
    # ------------------------------------------------------------------

    def submit_decision(
        self,
        plan_id: str,
        action_id: str,
        response: ApprovalResponse,
    ) -> bool:
        """Submit a decision for a pending approval.

        Returns:
            True if the decision was applied (a matching pending request was
            found); False if no matching request exists.
        """
        key = (plan_id, action_id)
        entry = self._pending.get(key)
        if entry is None:
            return False

        # Handle APPROVE_ALWAYS: add to auto-approve set.
        if response.decision == ApprovalDecision.APPROVE_ALWAYS:
            action_key = f"{entry.request.module}.{entry.request.action_name}"
            self._auto_approve.add(action_key)

        entry.response = response
        entry.event.set()
        return True

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get_pending(self, plan_id: str | None = None) -> list[ApprovalRequest]:
        """Return all pending approval requests, optionally filtered by plan."""
        if plan_id is None:
            return [e.request for e in self._pending.values()]
        return [
            e.request
            for (pid, _), e in self._pending.items()
            if pid == plan_id
        ]

    def is_auto_approved(self, module: str, action: str) -> bool:
        """Check if this module.action has been auto-approved (APPROVE_ALWAYS)."""
        return f"{module}.{action}" in self._auto_approve

    def clear_auto_approvals(self) -> None:
        """Reset the session auto-approve list."""
        self._auto_approve.clear()

    @property
    def pending_count(self) -> int:
        return len(self._pending)
