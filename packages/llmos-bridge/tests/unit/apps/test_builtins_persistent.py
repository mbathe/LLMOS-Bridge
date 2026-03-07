"""Tests for persistent todo builtin and enhanced todo actions."""

import asyncio
import json
import pytest

from llmos_bridge.apps.builtins import BuiltinToolExecutor, _TODO_KV_KEY


# ─── Mock KV store ──────────────────────────────────────────────────


class MockKVStore:
    """In-memory KV store for testing."""

    def __init__(self):
        self._data: dict[str, str] = {}

    async def get(self, key: str):
        return self._data.get(key)

    async def set(self, key: str, value: str, ttl_seconds=None):
        self._data[key] = value

    async def delete(self, key: str):
        self._data.pop(key, None)


# ─── Tests ───────────────────────────────────────────────────────────


class TestTodoPersistence:
    @pytest.mark.asyncio
    async def test_todo_add_persists_to_kv(self):
        kv = MockKVStore()
        executor = BuiltinToolExecutor(kv_store=kv)

        await executor.execute("todo", {"action": "add", "task": "Fix bug #42"})
        await executor.execute("todo", {"action": "add", "task": "Write tests"})

        # Verify persisted
        raw = await kv.get(_TODO_KV_KEY)
        assert raw is not None
        todos = json.loads(raw)
        assert len(todos) == 2
        assert todos[0]["task"] == "Fix bug #42"
        assert todos[1]["task"] == "Write tests"

    @pytest.mark.asyncio
    async def test_todo_loads_from_kv_on_first_access(self):
        kv = MockKVStore()
        # Pre-populate KV store
        existing = [
            {"id": "abc", "task": "Existing task", "status": "pending"},
            {"id": "def", "task": "Done task", "status": "completed"},
        ]
        await kv.set(_TODO_KV_KEY, json.dumps(existing))

        executor = BuiltinToolExecutor(kv_store=kv)
        result = await executor.execute("todo", {"action": "list"})

        assert result["total"] == 2
        assert result["pending"] == 1
        assert result["completed"] == 1
        tasks = result["tasks"]
        assert tasks[0]["task"] == "Existing task"

    @pytest.mark.asyncio
    async def test_todo_survives_executor_recreation(self):
        """Simulate session restart — create new executor, todos should survive."""
        kv = MockKVStore()

        # Session 1
        exec1 = BuiltinToolExecutor(kv_store=kv)
        await exec1.execute("todo", {"action": "add", "task": "Task A"})
        await exec1.execute("todo", {"action": "add", "task": "Task B"})

        # Session 2 — new executor, same KV store
        exec2 = BuiltinToolExecutor(kv_store=kv)
        result = await exec2.execute("todo", {"action": "list"})

        assert result["total"] == 2
        assert result["tasks"][0]["task"] == "Task A"

    @pytest.mark.asyncio
    async def test_todo_complete_persists(self):
        kv = MockKVStore()
        executor = BuiltinToolExecutor(kv_store=kv)

        r = await executor.execute("todo", {"action": "add", "task": "Fix it"})
        task_id = r["id"]

        await executor.execute("todo", {"action": "complete", "task_id": task_id})

        # Reload from KV
        exec2 = BuiltinToolExecutor(kv_store=kv)
        result = await exec2.execute("todo", {"action": "list"})
        assert result["tasks"][0]["status"] == "completed"

    @pytest.mark.asyncio
    async def test_todo_without_kv_still_works(self):
        """Without KV store, todo works in-memory only."""
        executor = BuiltinToolExecutor()
        await executor.execute("todo", {"action": "add", "task": "Ephemeral"})
        result = await executor.execute("todo", {"action": "list"})
        assert result["total"] == 1


class TestTodoNewActions:
    @pytest.mark.asyncio
    async def test_remove_task(self):
        executor = BuiltinToolExecutor()
        r = await executor.execute("todo", {"action": "add", "task": "Temp"})
        task_id = r["id"]

        result = await executor.execute("todo", {"action": "remove", "task_id": task_id})
        assert result["removed"] is True

        listing = await executor.execute("todo", {"action": "list"})
        assert listing["total"] == 0

    @pytest.mark.asyncio
    async def test_clear_completed(self):
        executor = BuiltinToolExecutor()
        r1 = await executor.execute("todo", {"action": "add", "task": "A"})
        r2 = await executor.execute("todo", {"action": "add", "task": "B"})
        await executor.execute("todo", {"action": "complete", "task_id": r1["id"]})

        result = await executor.execute("todo", {"action": "clear_completed"})
        assert result["cleared"] == 1
        assert result["remaining"] == 1

    @pytest.mark.asyncio
    async def test_list_with_status_filter(self):
        executor = BuiltinToolExecutor()
        r1 = await executor.execute("todo", {"action": "add", "task": "Pending"})
        r2 = await executor.execute("todo", {"action": "add", "task": "Done"})
        await executor.execute("todo", {"action": "complete", "task_id": r2["id"]})

        pending = await executor.execute("todo", {"action": "list", "status_filter": "pending"})
        assert len(pending["tasks"]) == 1
        assert pending["tasks"][0]["task"] == "Pending"

        completed = await executor.execute("todo", {"action": "list", "status_filter": "completed"})
        assert len(completed["tasks"]) == 1
        assert completed["tasks"][0]["task"] == "Done"

    @pytest.mark.asyncio
    async def test_todo_update_persists(self):
        kv = MockKVStore()
        executor = BuiltinToolExecutor(kv_store=kv)

        r = await executor.execute("todo", {"action": "add", "task": "Original"})
        task_id = r["id"]

        await executor.execute("todo", {
            "action": "update",
            "task_id": task_id,
            "task": "Updated text",
            "status": "in_progress",
        })

        # Reload
        exec2 = BuiltinToolExecutor(kv_store=kv)
        result = await exec2.execute("todo", {"action": "list"})
        assert result["tasks"][0]["task"] == "Updated text"
        assert result["tasks"][0]["status"] == "in_progress"
