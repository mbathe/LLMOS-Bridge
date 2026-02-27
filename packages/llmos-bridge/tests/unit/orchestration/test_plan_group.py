"""Unit tests for PlanGroupExecutor — fan-out/fan-in parallel execution."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from llmos_bridge.orchestration.plan_group import PlanGroupExecutor, PlanGroupResult
from llmos_bridge.orchestration.state import ExecutionState
from llmos_bridge.protocol.models import IMLPlan, PlanStatus


def _make_plan(plan_id: str = "p1") -> IMLPlan:
    """Create a minimal valid IML plan for testing."""
    return IMLPlan(
        plan_id=plan_id,
        protocol_version="2.0",
        description="test",
        actions=[
            {
                "id": "a1",
                "module": "filesystem",
                "action": "list_directory",
                "params": {"path": "/tmp"},
            }
        ],
    )


def _make_state(status: str = "completed") -> ExecutionState:
    """Create a mock ExecutionState."""
    state = MagicMock(spec=ExecutionState)
    state.plan_status = MagicMock()
    state.plan_status.value = status
    state.actions = {}
    return state


class TestPlanGroupResult:
    """Test PlanGroupResult dataclass."""

    def test_summary_all_completed(self) -> None:
        r = PlanGroupResult(
            group_id="g1",
            status="completed",
            plan_results={"p1": _make_state(), "p2": _make_state()},
        )
        assert r.summary == {"total": 2, "completed": 2, "failed": 0}

    def test_summary_partial_failure(self) -> None:
        r = PlanGroupResult(
            group_id="g1",
            status="partial_failure",
            plan_results={"p1": _make_state()},
            errors={"p2": "boom"},
        )
        assert r.summary == {"total": 2, "completed": 1, "failed": 1}

    def test_summary_all_failed(self) -> None:
        r = PlanGroupResult(
            group_id="g1",
            status="failed",
            errors={"p1": "err1", "p2": "err2"},
        )
        assert r.summary == {"total": 2, "completed": 0, "failed": 2}

    def test_duration(self) -> None:
        r = PlanGroupResult(group_id="g1", status="completed", started_at=100.0, finished_at=105.5)
        assert r.duration == pytest.approx(5.5)


class TestPlanGroupExecutor:
    """Test PlanGroupExecutor.execute()."""

    @pytest.mark.asyncio
    async def test_all_complete(self) -> None:
        executor = AsyncMock()
        executor.run = AsyncMock(return_value=_make_state())
        ge = PlanGroupExecutor(executor)
        plans = [_make_plan("p1"), _make_plan("p2"), _make_plan("p3")]

        result = await ge.execute(plans, group_id="test_group")

        assert result.status == "completed"
        assert result.group_id == "test_group"
        assert len(result.plan_results) == 3
        assert len(result.errors) == 0
        assert result.summary["total"] == 3
        assert result.summary["completed"] == 3

    @pytest.mark.asyncio
    async def test_partial_failure(self) -> None:
        async def side_effect(plan: IMLPlan) -> ExecutionState:
            if plan.plan_id == "fail":
                raise RuntimeError("plan failed")
            return _make_state()

        executor = AsyncMock()
        executor.run = AsyncMock(side_effect=side_effect)
        ge = PlanGroupExecutor(executor)

        plans = [_make_plan("ok1"), _make_plan("fail"), _make_plan("ok2")]
        result = await ge.execute(plans)

        assert result.status == "partial_failure"
        assert len(result.plan_results) == 2
        assert len(result.errors) == 1
        assert "fail" in result.errors

    @pytest.mark.asyncio
    async def test_all_fail(self) -> None:
        executor = AsyncMock()
        executor.run = AsyncMock(side_effect=RuntimeError("nope"))
        ge = PlanGroupExecutor(executor)

        plans = [_make_plan("p1"), _make_plan("p2")]
        result = await ge.execute(plans)

        assert result.status == "failed"
        assert len(result.errors) == 2

    @pytest.mark.asyncio
    async def test_group_timeout(self) -> None:
        async def slow_run(plan: IMLPlan) -> ExecutionState:
            await asyncio.sleep(10)
            return _make_state()

        executor = AsyncMock()
        executor.run = AsyncMock(side_effect=slow_run)
        ge = PlanGroupExecutor(executor)

        plans = [_make_plan("slow")]
        result = await ge.execute(plans, timeout=0.1)

        assert result.status == "failed"
        assert "_group" in result.errors

    @pytest.mark.asyncio
    async def test_max_concurrent(self) -> None:
        """Verify that max_concurrent limits parallelism."""
        running = 0
        max_running = 0

        async def tracked_run(plan: IMLPlan) -> ExecutionState:
            nonlocal running, max_running
            running += 1
            max_running = max(max_running, running)
            await asyncio.sleep(0.05)
            running -= 1
            return _make_state()

        executor = AsyncMock()
        executor.run = AsyncMock(side_effect=tracked_run)
        ge = PlanGroupExecutor(executor)

        plans = [_make_plan(f"p{i}") for i in range(10)]
        result = await ge.execute(plans, max_concurrent=3, timeout=10.0)

        assert result.status == "completed"
        assert max_running <= 3

    @pytest.mark.asyncio
    async def test_generated_group_id(self) -> None:
        executor = AsyncMock()
        executor.run = AsyncMock(return_value=_make_state())
        ge = PlanGroupExecutor(executor)

        result = await ge.execute([_make_plan("p1")])
        assert result.group_id.startswith("group_")
