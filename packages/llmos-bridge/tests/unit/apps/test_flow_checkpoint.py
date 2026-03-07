"""Tests for flow executor checkpoint/resume system."""

import asyncio
import json
import pytest

from llmos_bridge.apps.flow_executor import FlowCheckpoint, FlowExecutor, StepResult
from llmos_bridge.apps.models import FlowStep


class MockKVStore:
    def __init__(self):
        self._data: dict[str, str] = {}

    async def get(self, key: str):
        return self._data.get(key)

    async def set(self, key: str, value: str, ttl_seconds=None):
        self._data[key] = value

    async def delete(self, key: str):
        self._data.pop(key, None)


def _make_action_step(step_id: str, module: str = "filesystem", action: str = "read_file"):
    return FlowStep(id=step_id, action=f"{module}.{action}", params={"path": "/tmp/test"})


class TestFlowCheckpoint:
    def test_to_dict_and_from_dict(self):
        cp = FlowCheckpoint(
            flow_id="flow-123",
            completed_steps={"step1": {"output": "ok"}, "step2": "done"},
            current_step_index=2,
        )
        d = cp.to_dict()
        restored = FlowCheckpoint.from_dict(d)

        assert restored.flow_id == "flow-123"
        assert restored.completed_steps == cp.completed_steps
        assert restored.current_step_index == 2

    def test_empty_checkpoint(self):
        cp = FlowCheckpoint(flow_id="empty")
        d = cp.to_dict()
        restored = FlowCheckpoint.from_dict(d)
        assert restored.completed_steps == {}
        assert restored.current_step_index == 0


class TestFlowCheckpointPersistence:
    @pytest.mark.asyncio
    async def test_checkpoint_saved_after_each_step(self):
        kv = MockKVStore()
        call_count = 0

        async def mock_action(module, action, params):
            nonlocal call_count
            call_count += 1
            return {"result": f"step_{call_count}"}

        steps = [
            _make_action_step("s1"),
            _make_action_step("s2"),
            _make_action_step("s3"),
        ]

        executor = FlowExecutor(
            execute_action=mock_action,
            kv_store=kv,
            flow_id="test-flow",
        )
        result = await executor.execute(steps)

        assert result.success
        assert call_count == 3

        # Checkpoint should be cleared after success
        key = f"llmos:flow:checkpoint:test-flow"
        assert await kv.get(key) is None

    @pytest.mark.asyncio
    async def test_checkpoint_not_cleared_on_failure(self):
        kv = MockKVStore()
        call_count = 0

        async def failing_action(module, action, params):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("Step 2 failed!")
            return {"result": f"step_{call_count}"}

        steps = [
            _make_action_step("s1"),
            FlowStep(id="s2", action="os_exec.run_command", params={"command": "fail"}, on_error="fail"),
            _make_action_step("s3"),
        ]

        executor = FlowExecutor(
            execute_action=failing_action,
            kv_store=kv,
            flow_id="fail-flow",
        )
        result = await executor.execute(steps)

        assert not result.success
        # Checkpoint should still exist for retry
        key = "llmos:flow:checkpoint:fail-flow"
        raw = await kv.get(key)
        assert raw is not None
        cp = json.loads(raw)
        assert "s1" in cp["completed_steps"]

    @pytest.mark.asyncio
    async def test_resume_skips_completed_steps(self):
        kv = MockKVStore()
        executed_steps = []

        async def tracking_action(module, action, params):
            step_info = f"{module}.{action}"
            executed_steps.append(step_info)
            return {"done": step_info}

        steps = [
            _make_action_step("s1"),
            _make_action_step("s2"),
            _make_action_step("s3"),
        ]

        # Pre-populate checkpoint: s1 already done, resume from index 1
        checkpoint = FlowCheckpoint(
            flow_id="resume-flow",
            completed_steps={"s1": {"done": "filesystem.read_file"}},
            current_step_index=1,
        )
        key = "llmos:flow:checkpoint:resume-flow"
        await kv.set(key, json.dumps(checkpoint.to_dict()))

        executor = FlowExecutor(
            execute_action=tracking_action,
            kv_store=kv,
            flow_id="resume-flow",
        )
        result = await executor.execute(steps, resume=True)

        assert result.success
        # Only s2 and s3 should have been executed
        assert len(executed_steps) == 2
        # s1 result should still be available
        assert "s1" in result.results

    @pytest.mark.asyncio
    async def test_resume_without_checkpoint_runs_all(self):
        kv = MockKVStore()
        count = 0

        async def counting_action(module, action, params):
            nonlocal count
            count += 1
            return {"n": count}

        steps = [_make_action_step("a"), _make_action_step("b")]

        executor = FlowExecutor(
            execute_action=counting_action,
            kv_store=kv,
            flow_id="no-cp",
        )
        result = await executor.execute(steps, resume=True)

        assert result.success
        assert count == 2

    @pytest.mark.asyncio
    async def test_no_kv_store_runs_normally(self):
        """Without KV store, checkpoint is just skipped."""
        count = 0

        async def counting_action(module, action, params):
            nonlocal count
            count += 1
            return {"n": count}

        steps = [_make_action_step("x"), _make_action_step("y")]
        executor = FlowExecutor(execute_action=counting_action)
        result = await executor.execute(steps)

        assert result.success
        assert count == 2
