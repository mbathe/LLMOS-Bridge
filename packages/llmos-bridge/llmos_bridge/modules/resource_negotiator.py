"""Module Spec v3 — Dynamic resource negotiation.

Before dispatching an expensive action, the executor can ask the module
to estimate its cost (via ``estimate_cost()``).  The ``ResourceNegotiator``
checks this estimate against the module's declared ``ResourceLimits`` and
the system's current load, then grants, denies, or defers the request.

Usage::

    negotiator = ResourceNegotiator(registry)
    decision = await negotiator.negotiate(module_id, action, params)
    if decision.granted:
        result = await module.execute(action, params)
    elif decision.defer:
        await asyncio.sleep(decision.retry_after)
    else:
        raise ResourceDeniedError(decision.reason)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from llmos_bridge.logging import get_logger
from llmos_bridge.modules.base import ResourceEstimate

if TYPE_CHECKING:
    from llmos_bridge.modules.registry import ModuleRegistry

log = get_logger(__name__)


@dataclass
class ResourceRequest:
    """A request from a module to acquire resources for an action."""

    module_id: str
    action: str
    estimate: ResourceEstimate
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class NegotiationResult:
    """Result of a resource negotiation."""

    granted: bool = True
    defer: bool = False
    retry_after: float = 0.0  # seconds to wait before retrying (if deferred)
    reason: str = ""
    adjusted_estimate: ResourceEstimate | None = None


class ResourceNegotiator:
    """Evaluates resource requests against module limits and system capacity.

    The negotiator:
    1. Calls ``module.estimate_cost(action, params)`` to get a cost estimate.
    2. Checks the estimate against the module's ``ResourceLimits`` from its manifest.
    3. Tracks current resource usage per module.
    4. Returns a ``NegotiationResult`` indicating whether to proceed.
    """

    def __init__(self, registry: "ModuleRegistry") -> None:
        self._registry = registry
        # Track active resource usage per module.
        self._active_usage: dict[str, float] = {}  # module_id → total memory MB
        self._active_duration: dict[str, float] = {}  # module_id → total estimated seconds

    async def negotiate(
        self,
        module_id: str,
        action: str,
        params: dict[str, Any],
    ) -> NegotiationResult:
        """Negotiate resource allocation for an action.

        Returns a NegotiationResult indicating whether the action should proceed.
        """
        try:
            module = self._registry.get(module_id)
        except Exception:
            return NegotiationResult(granted=True)

        # Get the cost estimate from the module.
        try:
            estimate = await module.estimate_cost(action, params)
        except Exception:
            return NegotiationResult(granted=True)

        # Check against resource limits from manifest.
        try:
            manifest = module.get_manifest()
        except Exception:
            return NegotiationResult(granted=True)

        limits = manifest.resource_limits
        if limits is None:
            return NegotiationResult(granted=True, adjusted_estimate=estimate)

        # Check memory limit.
        if limits.max_memory_mb > 0 and estimate.estimated_memory_mb > 0:
            current_memory = self._active_usage.get(module_id, 0.0)
            total = current_memory + estimate.estimated_memory_mb
            if total > limits.max_memory_mb:
                if estimate.confidence >= 0.7:
                    return NegotiationResult(
                        granted=False,
                        defer=True,
                        retry_after=estimate.estimated_duration_seconds or 5.0,
                        reason=(
                            f"Memory limit exceeded: {total:.1f}MB > "
                            f"{limits.max_memory_mb}MB limit"
                        ),
                    )

        # Check execution time limit.
        if limits.max_execution_seconds > 0 and estimate.estimated_duration_seconds > 0:
            if estimate.estimated_duration_seconds > limits.max_execution_seconds:
                return NegotiationResult(
                    granted=False,
                    reason=(
                        f"Estimated duration {estimate.estimated_duration_seconds:.1f}s "
                        f"exceeds limit {limits.max_execution_seconds:.1f}s"
                    ),
                )

        return NegotiationResult(granted=True, adjusted_estimate=estimate)

    def acquire(self, module_id: str, estimate: ResourceEstimate) -> None:
        """Track resource acquisition for a module."""
        self._active_usage[module_id] = (
            self._active_usage.get(module_id, 0.0) + estimate.estimated_memory_mb
        )
        self._active_duration[module_id] = (
            self._active_duration.get(module_id, 0.0) + estimate.estimated_duration_seconds
        )

    def release(self, module_id: str, estimate: ResourceEstimate) -> None:
        """Release resources after action completion."""
        self._active_usage[module_id] = max(
            0.0, self._active_usage.get(module_id, 0.0) - estimate.estimated_memory_mb
        )
        self._active_duration[module_id] = max(
            0.0,
            self._active_duration.get(module_id, 0.0)
            - estimate.estimated_duration_seconds,
        )

    def status(self) -> dict[str, dict[str, float]]:
        """Return current resource usage tracking per module."""
        result: dict[str, dict[str, float]] = {}
        for module_id in set(self._active_usage) | set(self._active_duration):
            result[module_id] = {
                "memory_mb": self._active_usage.get(module_id, 0.0),
                "duration_s": self._active_duration.get(module_id, 0.0),
            }
        return result
