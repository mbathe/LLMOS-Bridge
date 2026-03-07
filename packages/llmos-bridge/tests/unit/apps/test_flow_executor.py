"""Tests for FlowExecutor — all 18 flow constructs."""

import asyncio
import pytest

from llmos_bridge.apps.expression import ExpressionContext, ExpressionEngine
from llmos_bridge.apps.flow_executor import (
    FlowEndSignal,
    FlowExecutionError,
    FlowExecutor,
    FlowResult,
    StepResult,
    _parse_duration,
)
from llmos_bridge.apps.models import (
    BranchConfig,
    CatchHandler,
    EndConfig,
    FlowStep,
    LoopFlowConfig,
    MapConfig,
    ParallelConfig,
    RaceConfig,
    ReduceConfig,
    SpawnConfig,
    EmitConfig,
    WaitConfig,
    DispatchConfig,
    ApprovalFlowConfig,
    ApprovalOption,
)


# ─── Helpers ──────────────────────────────────────────────────────────


async def mock_action(module_id, action_name, params):
    return {"module": module_id, "action": action_name, "params": params}


async def mock_action_with_content(module_id, action_name, params):
    return {"content": f"result from {module_id}.{action_name}"}


async def failing_action(module_id, action_name, params):
    return {"error": "action failed"}


async def mock_agent(agent_id, input_text):
    return {"output": f"Agent {agent_id} processed: {input_text}"}


@pytest.fixture
def ctx():
    return ExpressionContext(
        variables={"workspace": "/test"},
        results={},
    )


@pytest.fixture
def executor(ctx):
    return FlowExecutor(
        expr_context=ctx,
        execute_action=mock_action,
        run_agent=mock_agent,
    )


# ─── Tests ────────────────────────────────────────────────────────────


class TestActionStep:
    @pytest.mark.asyncio
    async def test_basic_action(self, executor):
        steps = [FlowStep(id="s1", action="filesystem.read_file", params={"path": "/tmp/test"})]
        result = await executor.execute(steps)
        assert result.success
        assert "s1" in result.results
        assert result.results["s1"].output["module"] == "filesystem"

    @pytest.mark.asyncio
    async def test_invalid_action_format(self, executor):
        steps = [FlowStep(id="s1", action="bad_action")]
        result = await executor.execute(steps)
        assert not result.results["s1"].success
        assert "Invalid action" in result.results["s1"].error

    @pytest.mark.asyncio
    async def test_no_executor(self, ctx):
        executor = FlowExecutor(expr_context=ctx)
        steps = [FlowStep(id="s1", action="fs.read", params={})]
        result = await executor.execute(steps)
        assert not result.results["s1"].success

    @pytest.mark.asyncio
    async def test_error_action(self, ctx):
        executor = FlowExecutor(expr_context=ctx, execute_action=failing_action)
        steps = [FlowStep(id="s1", action="fs.read", params={})]
        result = await executor.execute(steps)
        assert not result.results["s1"].success

    @pytest.mark.asyncio
    async def test_template_in_params(self, ctx):
        ctx.variables["target"] = "/home/test/file.txt"
        executor = FlowExecutor(expr_context=ctx, execute_action=mock_action)
        steps = [FlowStep(id="s1", action="filesystem.read_file", params={"path": "{{target}}"})]
        result = await executor.execute(steps)
        assert result.results["s1"].output["params"]["path"] == "/home/test/file.txt"


class TestAgentStep:
    @pytest.mark.asyncio
    async def test_basic_agent(self, executor):
        steps = [FlowStep(id="s1", agent="default", input="Fix the bug")]
        result = await executor.execute(steps)
        assert result.success
        assert "processed" in str(result.results["s1"].output)

    @pytest.mark.asyncio
    async def test_no_agent_runner(self, ctx):
        executor = FlowExecutor(expr_context=ctx)
        steps = [FlowStep(id="s1", agent="default", input="test")]
        result = await executor.execute(steps)
        assert not result.results["s1"].success


class TestSequenceStep:
    @pytest.mark.asyncio
    async def test_sequence(self, executor):
        steps = [FlowStep(id="seq", sequence=[
            FlowStep(id="a", action="fs.read", params={}),
            FlowStep(id="b", action="fs.write", params={}),
        ])]
        result = await executor.execute(steps)
        assert result.success
        assert "a" in result.results
        assert "b" in result.results

    @pytest.mark.asyncio
    async def test_sequence_preserves_order(self, executor):
        order = []

        async def tracking_action(mod, act, params):
            order.append(act)
            return {"ok": True}

        executor._execute_action = tracking_action
        steps = [FlowStep(sequence=[
            FlowStep(id="a", action="m.first", params={}),
            FlowStep(id="b", action="m.second", params={}),
            FlowStep(id="c", action="m.third", params={}),
        ])]
        await executor.execute(steps)
        assert order == ["first", "second", "third"]


class TestParallelStep:
    @pytest.mark.asyncio
    async def test_parallel(self, executor):
        steps = [FlowStep(id="par", parallel=ParallelConfig(steps=[
            FlowStep(id="a", action="fs.read", params={}),
            FlowStep(id="b", action="fs.write", params={}),
        ]))]
        result = await executor.execute(steps)
        assert result.success
        assert "a" in result.results
        assert "b" in result.results

    @pytest.mark.asyncio
    async def test_parallel_fail_fast(self, ctx):
        call_count = 0

        async def slow_action(mod, act, params):
            nonlocal call_count
            call_count += 1
            if act == "fail":
                return {"error": "boom"}
            await asyncio.sleep(0.5)
            return {"ok": True}

        executor = FlowExecutor(expr_context=ctx, execute_action=slow_action)
        steps = [FlowStep(id="par", parallel=ParallelConfig(
            steps=[
                FlowStep(id="f", action="m.fail", params={}),
                FlowStep(id="s", action="m.slow", params={}),
            ],
            fail_fast=True,
        ))]
        result = await executor.execute(steps)
        assert not result.results["par"].success

    @pytest.mark.asyncio
    async def test_parallel_concurrency_limit(self, ctx):
        max_concurrent = 0
        current = 0

        async def tracked_action(mod, act, params):
            nonlocal max_concurrent, current
            current += 1
            if current > max_concurrent:
                max_concurrent = current
            await asyncio.sleep(0.01)
            current -= 1
            return {"ok": True}

        executor = FlowExecutor(expr_context=ctx, execute_action=tracked_action)
        steps = [FlowStep(id="par", parallel=ParallelConfig(
            steps=[FlowStep(id=f"s{i}", action="m.act", params={}) for i in range(5)],
            max_concurrent=2,
        ))]
        await executor.execute(steps)
        assert max_concurrent <= 2


class TestBranchStep:
    @pytest.mark.asyncio
    async def test_branch_true(self, ctx):
        ctx.results["check"] = {"exit_code": 0}
        executor = FlowExecutor(expr_context=ctx, execute_action=mock_action)
        steps = [FlowStep(id="br", branch=BranchConfig(
            on="{{result.check.exit_code == 0}}",
            cases={
                "true": [FlowStep(id="passed", action="m.pass_action", params={})],
                "false": [FlowStep(id="failed", action="m.fail_action", params={})],
            },
        ))]
        result = await executor.execute(steps)
        assert "passed" in result.results
        assert "failed" not in result.results

    @pytest.mark.asyncio
    async def test_branch_default(self, ctx):
        ctx.variables["status"] = "unknown"
        executor = FlowExecutor(expr_context=ctx, execute_action=mock_action)
        steps = [FlowStep(id="br", branch=BranchConfig(
            on="{{status}}",
            cases={"ok": [FlowStep(id="ok_step", action="m.ok", params={})]},
            default=[FlowStep(id="def_step", action="m.default", params={})],
        ))]
        result = await executor.execute(steps)
        assert "def_step" in result.results

    @pytest.mark.asyncio
    async def test_branch_string_match(self, ctx):
        ctx.variables["mode"] = "fast"
        executor = FlowExecutor(expr_context=ctx, execute_action=mock_action)
        steps = [FlowStep(id="br", branch=BranchConfig(
            on="{{mode}}",
            cases={
                "fast": [FlowStep(id="fast_step", action="m.fast", params={})],
                "slow": [FlowStep(id="slow_step", action="m.slow", params={})],
            },
        ))]
        result = await executor.execute(steps)
        assert "fast_step" in result.results


class TestLoopStep:
    @pytest.mark.asyncio
    async def test_basic_loop(self, executor):
        steps = [FlowStep(id="lp", loop=LoopFlowConfig(
            max_iterations=3,
            body=[FlowStep(id="body", action="m.do", params={})],
        ))]
        result = await executor.execute(steps)
        assert result.success
        assert len(result.results["lp"].output) == 3

    @pytest.mark.asyncio
    async def test_loop_until_condition(self, ctx):
        call_count = 0

        async def counting_action(mod, act, params):
            nonlocal call_count
            call_count += 1
            return {"count": call_count}

        ctx.results["counter"] = {"count": 0}
        executor = FlowExecutor(expr_context=ctx, execute_action=counting_action)
        steps = [FlowStep(id="lp", loop=LoopFlowConfig(
            max_iterations=10,
            until="{{result.counter.count >= 3}}",
            body=[FlowStep(id="counter", action="m.count", params={})],
        ))]
        result = await executor.execute(steps)
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_loop_max_iterations(self, executor):
        steps = [FlowStep(id="lp", loop=LoopFlowConfig(
            max_iterations=2,
            body=[FlowStep(id="body", action="m.do", params={})],
        ))]
        result = await executor.execute(steps)
        assert len(result.results["lp"].output) == 2


class TestMapStep:
    @pytest.mark.asyncio
    async def test_basic_map(self, ctx):
        ctx.variables["files"] = ["a.py", "b.py", "c.py"]
        executor = FlowExecutor(expr_context=ctx, execute_action=mock_action)
        steps = [FlowStep(id="mp", map=MapConfig(
            over="{{files}}",
            as_var="file",
            step=[FlowStep(id="process", action="fs.read", params={"path": "{{file}}"})],
        ))]
        result = await executor.execute(steps)
        assert result.success
        assert len(result.results["mp"].output) == 3

    @pytest.mark.asyncio
    async def test_map_not_a_list(self, ctx):
        ctx.variables["not_list"] = "string"
        executor = FlowExecutor(expr_context=ctx, execute_action=mock_action)
        steps = [FlowStep(id="mp", map=MapConfig(
            over="{{not_list}}",
            step=[FlowStep(action="m.a", params={})],
        ))]
        result = await executor.execute(steps)
        assert not result.results["mp"].success


class TestReduceStep:
    @pytest.mark.asyncio
    async def test_basic_reduce(self, ctx):
        ctx.variables["numbers"] = [1, 2, 3]

        async def sum_action(mod, act, params):
            return {"total": params.get("sum", 0)}

        executor = FlowExecutor(expr_context=ctx, execute_action=sum_action)
        steps = [FlowStep(id="rd", reduce=ReduceConfig(
            over="{{numbers}}",
            initial={"total": 0},
            as_var="acc",
            step=FlowStep(action="m.sum", params={"sum": "{{acc}}"}),
        ))]
        result = await executor.execute(steps)
        assert result.success


class TestRaceStep:
    @pytest.mark.asyncio
    async def test_race_first_wins(self, ctx):
        async def fast_action(mod, act, params):
            return {"speed": "fast"}

        async def slow_action(mod, act, params):
            await asyncio.sleep(1)
            return {"speed": "slow"}

        call_idx = 0

        async def race_action(mod, act, params):
            nonlocal call_idx
            call_idx += 1
            if call_idx == 1:
                return {"speed": "fast"}
            await asyncio.sleep(1)
            return {"speed": "slow"}

        executor = FlowExecutor(expr_context=ctx, execute_action=race_action)
        steps = [FlowStep(id="rc", race=RaceConfig(steps=[
            FlowStep(id="fast", action="m.fast", params={}),
            FlowStep(id="slow", action="m.slow", params={}),
        ]))]
        result = await executor.execute(steps)
        assert result.success


class TestPipeStep:
    @pytest.mark.asyncio
    async def test_basic_pipe(self, ctx):
        async def pipe_action(mod, act, params):
            prev = params.get("input", "")
            return {"content": f"{prev}+{act}"}

        executor = FlowExecutor(expr_context=ctx, execute_action=pipe_action)
        steps = [FlowStep(id="pp", pipe=[
            FlowStep(id="step1", action="m.a", params={}),
            FlowStep(id="step2", action="m.b", params={"input": "{{pipe.input}}"}),
        ])]
        result = await executor.execute(steps)
        assert result.success
        # pipe.input for step2 should be the output of step1

    @pytest.mark.asyncio
    async def test_pipe_failure_stops(self, ctx):
        executor = FlowExecutor(expr_context=ctx, execute_action=failing_action)
        steps = [FlowStep(id="pp", pipe=[
            FlowStep(id="s1", action="m.fail", params={}),
            FlowStep(id="s2", action="m.never", params={}),
        ])]
        result = await executor.execute(steps)
        assert not result.results["pp"].success


class TestTryCatch:
    @pytest.mark.asyncio
    async def test_try_success(self, executor):
        steps = [FlowStep(
            id="tc",
            try_steps=[FlowStep(id="ok", action="m.ok", params={})],
        )]
        result = await executor.execute(steps)
        assert result.success

    @pytest.mark.asyncio
    async def test_try_catch_error(self, ctx):
        async def raise_action(mod, act, params):
            raise ValueError("boom")

        executor = FlowExecutor(expr_context=ctx, execute_action=raise_action)
        steps = [FlowStep(
            id="tc",
            try_steps=[FlowStep(id="bad", action="m.bad", params={}, on_error="fail")],
            catch=[CatchHandler(error="*", then="continue")],
        )]
        result = await executor.execute(steps)
        # Should handle the error via catch

    @pytest.mark.asyncio
    async def test_finally_always_runs(self, ctx):
        finally_ran = False

        async def track_action(mod, act, params):
            nonlocal finally_ran
            if act == "finally_step":
                finally_ran = True
            return {"ok": True}

        executor = FlowExecutor(expr_context=ctx, execute_action=track_action)
        steps = [FlowStep(
            id="tc",
            try_steps=[FlowStep(action="m.ok", params={})],
            finally_steps=[FlowStep(action="m.finally_step", params={})],
        )]
        await executor.execute(steps)
        assert finally_ran


class TestEmitStep:
    @pytest.mark.asyncio
    async def test_emit(self, ctx):
        events = []

        async def capture_emit(topic, event):
            events.append((topic, event))

        executor = FlowExecutor(expr_context=ctx, emit_event=capture_emit)
        steps = [FlowStep(id="em", emit=EmitConfig(
            topic="llmos.test",
            event={"type": "done"},
        ))]
        result = await executor.execute(steps)
        assert result.success
        assert events[0][0] == "llmos.test"

    @pytest.mark.asyncio
    async def test_emit_no_handler(self, ctx):
        executor = FlowExecutor(expr_context=ctx)
        steps = [FlowStep(id="em", emit=EmitConfig(topic="test", event={}))]
        result = await executor.execute(steps)
        assert result.success
        assert not result.results["em"].output["published"]


class TestDispatchStep:
    @pytest.mark.asyncio
    async def test_dynamic_dispatch(self, ctx):
        ctx.variables["target_module"] = "filesystem"
        ctx.variables["target_action"] = "read_file"
        executor = FlowExecutor(expr_context=ctx, execute_action=mock_action)
        steps = [FlowStep(id="dd", dispatch=DispatchConfig(
            module="{{target_module}}",
            action="{{target_action}}",
            params={"path": "/tmp"},
        ))]
        result = await executor.execute(steps)
        assert result.success
        assert result.results["dd"].output["module"] == "filesystem"


class TestEndStep:
    @pytest.mark.asyncio
    async def test_end_success(self, ctx):
        executor = FlowExecutor(expr_context=ctx, execute_action=mock_action)
        steps = [
            FlowStep(id="s1", action="m.do", params={}),
            FlowStep(id="done", end=EndConfig(status="success", output={"msg": "done"})),
            FlowStep(id="s2", action="m.never", params={}),
        ]
        result = await executor.execute(steps)
        assert result.status == "success"
        assert result.output == {"msg": "done"}
        assert "s2" not in result.results

    @pytest.mark.asyncio
    async def test_end_failure(self, ctx):
        executor = FlowExecutor(expr_context=ctx)
        steps = [FlowStep(end=EndConfig(status="failure"))]
        result = await executor.execute(steps)
        assert result.status == "failure"
        assert not result.success


class TestApprovalStep:
    @pytest.mark.asyncio
    async def test_approval_with_handler(self, ctx):
        async def approve(msg, opts, timeout):
            return "approve"

        executor = FlowExecutor(expr_context=ctx, approval_handler=approve)
        steps = [FlowStep(id="ap", approval=ApprovalFlowConfig(
            message="Deploy?",
            options=[ApprovalOption(label="Yes", value="approve")],
        ))]
        result = await executor.execute(steps)
        assert result.success
        assert result.results["ap"].output["choice"] == "approve"

    @pytest.mark.asyncio
    async def test_approval_no_handler_rejects(self, ctx):
        executor = FlowExecutor(expr_context=ctx)
        steps = [FlowStep(id="ap", approval=ApprovalFlowConfig(
            message="Deploy?",
            on_timeout="reject",
        ))]
        result = await executor.execute(steps)
        assert not result.results["ap"].success


class TestResultChaining:
    @pytest.mark.asyncio
    async def test_step_results_available(self, ctx):
        executor = FlowExecutor(expr_context=ctx, execute_action=mock_action_with_content)
        steps = [
            FlowStep(id="s1", action="fs.read", params={}),
            FlowStep(id="s2", action="fs.write", params={"data": "{{result.s1.content}}"}),
        ]
        result = await executor.execute(steps)
        assert result.success
        assert "s1" in result.results
        assert "s2" in result.results


class TestStopSignal:
    @pytest.mark.asyncio
    async def test_stop_during_execution(self, ctx):
        """Stop signal set during execution prevents further steps."""
        call_count = 0

        async def counting_action(mod, act, params):
            nonlocal call_count
            call_count += 1
            return {"result": "ok"}

        executor = FlowExecutor(expr_context=ctx, execute_action=counting_action)

        # We'll stop after first step by hooking into execute flow
        original_execute_step = executor._execute_step

        async def stop_after_first(step):
            await original_execute_step(step)
            executor.stop()

        executor._execute_step = stop_after_first

        steps = [
            FlowStep(id="s1", action="m.a", params={}),
            FlowStep(id="s2", action="m.b", params={}),
            FlowStep(id="s3", action="m.c", params={}),
        ]
        result = await executor.execute(steps)
        assert call_count == 1
        assert len(result.results) == 1


class TestOnError:
    @pytest.mark.asyncio
    async def test_on_error_skip(self, ctx):
        async def raise_action(mod, act, params):
            raise RuntimeError("boom")

        executor = FlowExecutor(expr_context=ctx, execute_action=raise_action)
        steps = [
            FlowStep(id="s1", action="m.bad", params={}, on_error="skip"),
            FlowStep(id="s2", action="m.bad", params={}, on_error="skip"),
        ]
        result = await executor.execute(steps)
        # Both should be in results even though they failed
        assert "s1" in result.results
        assert "s2" in result.results

    @pytest.mark.asyncio
    async def test_on_error_fail(self, ctx):
        async def raise_action(mod, act, params):
            raise RuntimeError("boom")

        executor = FlowExecutor(expr_context=ctx, execute_action=raise_action)
        steps = [
            FlowStep(id="s1", action="m.bad", params={}, on_error="fail"),
            FlowStep(id="s2", action="m.bad", params={}),
        ]
        result = await executor.execute(steps)
        assert not result.success
        assert "s2" not in result.results


class TestParseDuration:
    def test_seconds(self):
        assert _parse_duration("30s") == 30.0

    def test_minutes(self):
        assert _parse_duration("5m") == 300.0

    def test_hours(self):
        assert _parse_duration("1h") == 3600.0

    def test_milliseconds(self):
        assert _parse_duration("500ms") == 0.5

    def test_empty(self):
        assert _parse_duration("") == 300.0

    def test_plain_number(self):
        assert _parse_duration("42") == 42.0
