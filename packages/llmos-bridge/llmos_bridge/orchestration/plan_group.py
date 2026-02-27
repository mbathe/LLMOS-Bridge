"""Plan Group executor — fan-out / fan-in for parallel plan execution.

Submit N independent plans and get aggregated results back.

Usage::

    group_exec = PlanGroupExecutor(executor)
    result = await group_exec.execute(plans, max_concurrent=5)
    print(result.status)  # "completed" | "partial_failure" | "failed"
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from llmos_bridge.orchestration.executor import PlanExecutor
from llmos_bridge.orchestration.state import ExecutionState
from llmos_bridge.protocol.models import IMLPlan


@dataclass
class PlanGroupResult:
    """Aggregated result of a plan group execution."""

    group_id: str
    status: str  # "completed" | "partial_failure" | "failed"
    plan_results: dict[str, ExecutionState] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    started_at: float = 0.0
    finished_at: float = 0.0

    @property
    def duration(self) -> float:
        return self.finished_at - self.started_at

    @property
    def summary(self) -> dict[str, int]:
        total = len(self.plan_results) + len(self.errors)
        completed = sum(
            1 for s in self.plan_results.values()
            if s.plan_status.value == "completed"
        )
        failed = total - completed
        return {"total": total, "completed": completed, "failed": failed}


class PlanGroupExecutor:
    """Fan-out N plans in parallel with bounded concurrency."""

    def __init__(self, executor: PlanExecutor) -> None:
        self._executor = executor

    async def execute(
        self,
        plans: list[IMLPlan],
        group_id: str | None = None,
        max_concurrent: int = 10,
        timeout: float = 300.0,
    ) -> PlanGroupResult:
        """Execute all plans in parallel, returning aggregated results.

        Args:
            plans: List of IML plans to execute concurrently.
            group_id: Optional group identifier (generated if None).
            max_concurrent: Maximum plans running at the same time.
            timeout: Total timeout for the entire group (seconds).

        Returns:
            PlanGroupResult with per-plan results and overall status.
        """
        gid = group_id or f"group_{uuid.uuid4().hex[:12]}"
        result = PlanGroupResult(group_id=gid, status="running", started_at=time.time())

        semaphore = asyncio.Semaphore(max_concurrent)

        async def _run_one(plan: IMLPlan) -> tuple[str, ExecutionState | None, str | None]:
            async with semaphore:
                try:
                    state = await self._executor.run(plan)
                    return (plan.plan_id, state, None)
                except Exception as exc:
                    return (plan.plan_id, None, str(exc))

        try:
            tasks = [_run_one(plan) for plan in plans]
            outcomes = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=False),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            result.status = "failed"
            result.errors["_group"] = f"Group timed out after {timeout}s"
            result.finished_at = time.time()
            return result

        for plan_id, state, error in outcomes:
            if error:
                result.errors[plan_id] = error
            elif state:
                result.plan_results[plan_id] = state

        # Determine overall status.
        total = len(plans)
        errored = len(result.errors)
        if errored == 0:
            result.status = "completed"
        elif errored < total:
            result.status = "partial_failure"
        else:
            result.status = "failed"

        result.finished_at = time.time()
        return result
