"""Tests for the agent_spawn system module."""

import asyncio
import pytest

from llmos_bridge.modules.agent_spawn.module import (
    AgentSpawnModule,
    SpawnStatus,
)


# ─── Fixtures ────────────────────────────────────────────────────────


def _make_agent_factory(*, delay: float = 0.1, success: bool = True, output: str = "Task done."):
    """Create a mock agent factory that simulates agent execution."""
    async def factory(
        system_prompt: str,
        input_text: str,
        tools: list[str],
        model: str,
        provider: str,
        max_turns: int,
        execute_tool=None,
        message_queue=None,
        event_callback=None,
    ) -> dict:
        await asyncio.sleep(delay)
        if success:
            return {
                "success": True,
                "output": output,
                "turns": 3,
                "error": None,
                "stop_reason": "task_complete",
            }
        else:
            return {
                "success": False,
                "output": "",
                "turns": 1,
                "error": "Agent failed to complete task",
                "stop_reason": "error",
            }
    return factory


@pytest.fixture
def module():
    m = AgentSpawnModule()
    m.set_agent_factory(_make_agent_factory())
    return m


@pytest.fixture
def failing_module():
    m = AgentSpawnModule()
    m.set_agent_factory(_make_agent_factory(success=False))
    return m


@pytest.fixture
def slow_module():
    m = AgentSpawnModule()
    m.set_agent_factory(_make_agent_factory(delay=5.0))
    return m


# ─── Manifest ────────────────────────────────────────────────────────


class TestManifest:
    def test_manifest_has_all_actions(self):
        m = AgentSpawnModule()
        manifest = m.get_manifest()
        action_names = {a.name for a in manifest.actions}
        assert action_names == {
            "spawn_agent", "check_agent", "get_result",
            "list_agents", "cancel_agent", "wait_agent", "send_message",
        }

    def test_manifest_module_id(self):
        m = AgentSpawnModule()
        manifest = m.get_manifest()
        assert manifest.module_id == "agent_spawn"

    def test_spawn_agent_params(self):
        m = AgentSpawnModule()
        manifest = m.get_manifest()
        spawn = next(a for a in manifest.actions if a.name == "spawn_agent")
        param_names = {p.name for p in spawn.params}
        assert "name" in param_names
        assert "objective" in param_names
        assert "system_prompt" in param_names
        assert "tools" in param_names
        assert "model" in param_names
        assert "max_turns" in param_names
        assert "context" in param_names


# ─── Spawn & Check ───────────────────────────────────────────────────


class TestSpawnAgent:
    @pytest.mark.asyncio
    async def test_spawn_returns_spawn_id(self, module):
        result = await module.execute("spawn_agent", {
            "name": "researcher",
            "objective": "Find info about Python async",
        })
        assert "spawn_id" in result
        assert result["status"] == "running"
        assert result["name"] == "researcher"

    @pytest.mark.asyncio
    async def test_spawn_without_factory(self):
        m = AgentSpawnModule()
        result = await m.execute("spawn_agent", {
            "name": "test",
            "objective": "test",
        })
        assert "error" in result

    @pytest.mark.asyncio
    async def test_spawn_without_objective(self, module):
        result = await module.execute("spawn_agent", {"name": "test"})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_spawn_completes_successfully(self, module):
        result = await module.execute("spawn_agent", {
            "name": "coder",
            "objective": "Write hello world",
            "tools": ["filesystem.write_file"],
        })
        spawn_id = result["spawn_id"]

        # Wait for completion
        wait_result = await module.execute("wait_agent", {
            "spawn_id": spawn_id,
            "timeout": 5,
        })
        assert wait_result["status"] == "completed"
        assert wait_result["output"] == "Task done."
        assert wait_result["turns"] == 3

    @pytest.mark.asyncio
    async def test_spawn_failure_captured(self, failing_module):
        result = await failing_module.execute("spawn_agent", {
            "name": "doomed",
            "objective": "This will fail",
        })
        spawn_id = result["spawn_id"]

        wait_result = await failing_module.execute("wait_agent", {
            "spawn_id": spawn_id,
            "timeout": 5,
        })
        assert wait_result["status"] == "failed"
        assert "error" in wait_result


# ─── Check Agent ─────────────────────────────────────────────────────


class TestCheckAgent:
    @pytest.mark.asyncio
    async def test_check_running_agent(self, module):
        # Use slow factory
        m = AgentSpawnModule()
        m.set_agent_factory(_make_agent_factory(delay=5.0))

        result = await m.execute("spawn_agent", {
            "name": "slow",
            "objective": "Take your time",
        })
        spawn_id = result["spawn_id"]

        check = await m.execute("check_agent", {"spawn_id": spawn_id})
        assert check["status"] == "running"
        assert check["name"] == "slow"
        assert "elapsed_seconds" in check

        # Clean up
        await m.execute("cancel_agent", {"spawn_id": spawn_id})

    @pytest.mark.asyncio
    async def test_check_nonexistent(self, module):
        result = await module.execute("check_agent", {"spawn_id": "nope"})
        assert "error" in result


# ─── Get Result ──────────────────────────────────────────────────────


class TestGetResult:
    @pytest.mark.asyncio
    async def test_get_result_when_running(self, slow_module):
        result = await slow_module.execute("spawn_agent", {
            "name": "busy",
            "objective": "Working...",
        })
        spawn_id = result["spawn_id"]

        get = await slow_module.execute("get_result", {"spawn_id": spawn_id})
        assert get["status"] == "running"
        assert "message" in get

        await slow_module.execute("cancel_agent", {"spawn_id": spawn_id})

    @pytest.mark.asyncio
    async def test_get_result_after_completion(self, module):
        result = await module.execute("spawn_agent", {
            "name": "quick",
            "objective": "Do it fast",
        })
        spawn_id = result["spawn_id"]
        await module.execute("wait_agent", {"spawn_id": spawn_id, "timeout": 5})

        get = await module.execute("get_result", {"spawn_id": spawn_id})
        assert get["status"] == "completed"
        assert get["output"] == "Task done."


# ─── List Agents ─────────────────────────────────────────────────────


class TestListAgents:
    @pytest.mark.asyncio
    async def test_list_all(self, module):
        await module.execute("spawn_agent", {"name": "a", "objective": "task a"})
        await module.execute("spawn_agent", {"name": "b", "objective": "task b"})

        # Wait for both to complete
        await asyncio.sleep(0.3)

        listing = await module.execute("list_agents", {})
        assert listing["total"] == 2
        names = {a["name"] for a in listing["agents"]}
        assert names == {"a", "b"}

    @pytest.mark.asyncio
    async def test_list_with_filter(self, module):
        result = await module.execute("spawn_agent", {"name": "x", "objective": "task"})
        spawn_id = result["spawn_id"]
        await module.execute("wait_agent", {"spawn_id": spawn_id, "timeout": 5})

        completed = await module.execute("list_agents", {"status_filter": "completed"})
        assert completed["total"] >= 1

        running = await module.execute("list_agents", {"status_filter": "running"})
        assert all(a["status"] == "running" for a in running["agents"])


# ─── Cancel Agent ────────────────────────────────────────────────────


class TestCancelAgent:
    @pytest.mark.asyncio
    async def test_cancel_running(self, slow_module):
        result = await slow_module.execute("spawn_agent", {
            "name": "cancellable",
            "objective": "Long task",
        })
        spawn_id = result["spawn_id"]

        cancel = await slow_module.execute("cancel_agent", {"spawn_id": spawn_id})
        assert cancel["cancelled"] is True

        check = await slow_module.execute("check_agent", {"spawn_id": spawn_id})
        assert check["status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_already_completed(self, module):
        result = await module.execute("spawn_agent", {"name": "done", "objective": "quick"})
        spawn_id = result["spawn_id"]
        await module.execute("wait_agent", {"spawn_id": spawn_id, "timeout": 5})

        cancel = await module.execute("cancel_agent", {"spawn_id": spawn_id})
        assert cancel["cancelled"] is False


# ─── Wait Agent ──────────────────────────────────────────────────────


class TestWaitAgent:
    @pytest.mark.asyncio
    async def test_wait_timeout(self, slow_module):
        result = await slow_module.execute("spawn_agent", {
            "name": "slow",
            "objective": "Very slow task",
        })
        spawn_id = result["spawn_id"]

        wait = await slow_module.execute("wait_agent", {
            "spawn_id": spawn_id,
            "timeout": 0.1,
        })
        assert wait["status"] == "running"
        assert "timeout" in wait.get("message", "").lower()

        await slow_module.execute("cancel_agent", {"spawn_id": spawn_id})

    @pytest.mark.asyncio
    async def test_wait_already_completed(self, module):
        result = await module.execute("spawn_agent", {"name": "fast", "objective": "quick"})
        spawn_id = result["spawn_id"]

        # Wait once
        await module.execute("wait_agent", {"spawn_id": spawn_id, "timeout": 5})

        # Wait again — should return immediately
        wait = await module.execute("wait_agent", {"spawn_id": spawn_id, "timeout": 1})
        assert wait["status"] == "completed"


# ─── Send Message ────────────────────────────────────────────────────


class TestSendMessage:
    @pytest.mark.asyncio
    async def test_send_to_running(self, slow_module):
        result = await slow_module.execute("spawn_agent", {
            "name": "receiver",
            "objective": "Wait for messages",
        })
        spawn_id = result["spawn_id"]

        send = await slow_module.execute("send_message", {
            "spawn_id": spawn_id,
            "message": "New instructions from parent",
        })
        assert send["delivered"] is True
        assert send["queue_size"] == 1

        await slow_module.execute("cancel_agent", {"spawn_id": spawn_id})

    @pytest.mark.asyncio
    async def test_send_to_completed(self, module):
        result = await module.execute("spawn_agent", {"name": "done", "objective": "quick"})
        spawn_id = result["spawn_id"]
        await module.execute("wait_agent", {"spawn_id": spawn_id, "timeout": 5})

        send = await module.execute("send_message", {
            "spawn_id": spawn_id,
            "message": "Too late",
        })
        assert send["delivered"] is False


# ─── Parallel Spawning ───────────────────────────────────────────────


class TestParallelSpawning:
    @pytest.mark.asyncio
    async def test_spawn_multiple_in_parallel(self, module):
        """Spawn 5 agents simultaneously — they should all run in parallel."""
        spawn_ids = []
        for i in range(5):
            result = await module.execute("spawn_agent", {
                "name": f"worker-{i}",
                "objective": f"Task {i}",
            })
            spawn_ids.append(result["spawn_id"])

        # Wait for all
        for sid in spawn_ids:
            await module.execute("wait_agent", {"spawn_id": sid, "timeout": 5})

        listing = await module.execute("list_agents", {"status_filter": "completed"})
        assert listing["completed"] == 5


# ─── Lifecycle ───────────────────────────────────────────────────────


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_on_stop_cancels_running(self, slow_module):
        await slow_module.execute("spawn_agent", {"name": "a", "objective": "task"})
        await slow_module.execute("spawn_agent", {"name": "b", "objective": "task"})

        await slow_module.on_stop()

        # All cleared
        listing = await slow_module.execute("list_agents", {})
        assert listing["total"] == 0
