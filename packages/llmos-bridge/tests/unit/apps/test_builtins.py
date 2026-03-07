"""Tests for BuiltinToolExecutor — ask_user, todo, delegate, emit."""

import pytest

from llmos_bridge.apps.builtins import BuiltinToolExecutor, TodoItem


@pytest.fixture
def executor():
    return BuiltinToolExecutor()


class TestIsBuiltin:
    def test_known_builtins(self, executor):
        assert executor.is_builtin("ask_user") is True
        assert executor.is_builtin("todo") is True
        assert executor.is_builtin("delegate") is True
        assert executor.is_builtin("emit") is True

    def test_unknown(self, executor):
        assert executor.is_builtin("read_file") is False
        assert executor.is_builtin("unknown") is False


class TestAskUser:
    @pytest.mark.asyncio
    async def test_with_handler(self):
        async def handler(q):
            return f"answer to: {q}"

        exec_ = BuiltinToolExecutor(input_handler=handler)
        result = await exec_.execute("ask_user", {"question": "What color?"})
        assert result["response"] == "answer to: What color?"

    @pytest.mark.asyncio
    async def test_without_handler(self, executor):
        result = await executor.execute("ask_user", {"question": "hello"})
        assert result["response"] == ""
        assert "note" in result

    @pytest.mark.asyncio
    async def test_empty_question(self, executor):
        result = await executor.execute("ask_user", {})
        assert "response" in result


class TestTodo:
    @pytest.mark.asyncio
    async def test_add_task(self, executor):
        result = await executor.execute("todo", {"action": "add", "task": "Fix bug"})
        assert result["task"] == "Fix bug"
        assert result["status"] == "pending"
        assert "id" in result

    @pytest.mark.asyncio
    async def test_add_requires_task(self, executor):
        result = await executor.execute("todo", {"action": "add"})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_list_empty(self, executor):
        result = await executor.execute("todo", {"action": "list"})
        assert result["tasks"] == []

    @pytest.mark.asyncio
    async def test_list_after_add(self, executor):
        await executor.execute("todo", {"action": "add", "task": "Task 1"})
        await executor.execute("todo", {"action": "add", "task": "Task 2"})
        result = await executor.execute("todo", {"action": "list"})
        assert len(result["tasks"]) == 2

    @pytest.mark.asyncio
    async def test_complete_task(self, executor):
        added = await executor.execute("todo", {"action": "add", "task": "Do thing"})
        task_id = added["id"]
        result = await executor.execute("todo", {"action": "complete", "task_id": task_id})
        assert result["status"] == "completed"

    @pytest.mark.asyncio
    async def test_complete_not_found(self, executor):
        result = await executor.execute("todo", {"action": "complete", "task_id": "nonexistent"})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_update_task(self, executor):
        added = await executor.execute("todo", {"action": "add", "task": "Old"})
        task_id = added["id"]
        result = await executor.execute("todo", {"action": "update", "task_id": task_id, "task": "New"})
        assert result["task"] == "New"

    @pytest.mark.asyncio
    async def test_update_not_found(self, executor):
        result = await executor.execute("todo", {"action": "update", "task_id": "bad"})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_default_action_is_list(self, executor):
        result = await executor.execute("todo", {})
        assert "tasks" in result

    @pytest.mark.asyncio
    async def test_unknown_action(self, executor):
        result = await executor.execute("todo", {"action": "destroy"})
        assert "error" in result


class TestDelegate:
    @pytest.mark.asyncio
    async def test_with_handler(self):
        async def handler(agent_id, task):
            return {"done": True}

        exec_ = BuiltinToolExecutor(delegate_handler=handler)
        result = await exec_.execute("delegate", {"agent_id": "agent-2", "task": "Summarize"})
        assert result["agent_id"] == "agent-2"
        assert result["result"]["done"] is True

    @pytest.mark.asyncio
    async def test_without_handler(self, executor):
        result = await executor.execute("delegate", {"agent_id": "a", "task": "b"})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_missing_params(self, executor):
        result = await executor.execute("delegate", {})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_missing_task(self, executor):
        result = await executor.execute("delegate", {"agent_id": "a"})
        assert "error" in result


class TestEmit:
    @pytest.mark.asyncio
    async def test_with_handler(self):
        events = []

        async def handler(topic, data):
            events.append((topic, data))

        exec_ = BuiltinToolExecutor(emit_handler=handler)
        result = await exec_.execute("emit", {"topic": "task.done", "data": {"id": "1"}})
        assert result["published"] is True
        assert events[0] == ("task.done", {"id": "1"})

    @pytest.mark.asyncio
    async def test_without_handler(self, executor):
        result = await executor.execute("emit", {"topic": "test"})
        assert result["published"] is False

    @pytest.mark.asyncio
    async def test_missing_topic(self, executor):
        result = await executor.execute("emit", {})
        assert "error" in result


class TestUnknownTool:
    @pytest.mark.asyncio
    async def test_unknown(self, executor):
        result = await executor.execute("nonexistent_tool", {})
        assert "error" in result
        assert "Unknown" in result["error"]
