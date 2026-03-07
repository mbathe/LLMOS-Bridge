"""Tests for agent_spawn v2 features: message delivery, event propagation, persistence."""

import asyncio
import json
import pytest

from llmos_bridge.modules.agent_spawn.module import AgentSpawnModule, SpawnStatus


# ─── Mock helpers ────────────────────────────────────────────────────


class MockKVStore:
    def __init__(self):
        self._data: dict[str, str] = {}

    async def get(self, key: str):
        return self._data.get(key)

    async def set(self, key: str, value: str, ttl_seconds=None):
        self._data[key] = value

    async def delete(self, key: str):
        self._data.pop(key, None)


def _make_factory(*, delay: float = 0.1, success: bool = True, output: str = "Done."):
    """Create a mock agent factory that respects message_queue."""
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
        # Simulate checking messages during execution
        messages_received = []
        for _ in range(3):
            await asyncio.sleep(delay / 3)
            if message_queue:
                while not message_queue.empty():
                    msg = message_queue.get_nowait()
                    messages_received.append(msg.get("content", ""))

            if event_callback:
                # Simulate emitting events
                class FakeEvent:
                    type = "tool_call"
                    data = {"name": "test_tool", "step": _}
                await event_callback(FakeEvent())

        combined_output = output
        if messages_received:
            combined_output += f" [Messages: {', '.join(messages_received)}]"

        if success:
            return {
                "success": True,
                "output": combined_output,
                "turns": 3,
                "error": None,
            }
        return {
            "success": False,
            "output": "",
            "turns": 1,
            "error": "Failed",
        }
    return factory


# ─── Message delivery tests ────────────────────────────────────────


class TestMessageDelivery:
    @pytest.mark.asyncio
    async def test_send_message_uses_queue(self):
        m = AgentSpawnModule()
        m.set_agent_factory(_make_factory(delay=1.0))

        result = await m.execute("spawn_agent", {
            "name": "receiver",
            "objective": "Wait for messages",
        })
        spawn_id = result["spawn_id"]

        # Send a message
        send = await m.execute("send_message", {
            "spawn_id": spawn_id,
            "message": "New priority task",
        })
        assert send["delivered"] is True
        assert send["queue_size"] == 1

        # Send another
        send2 = await m.execute("send_message", {
            "spawn_id": spawn_id,
            "message": "Update: deadline moved",
        })
        assert send2["queue_size"] == 2

        await m.execute("cancel_agent", {"spawn_id": spawn_id})

    @pytest.mark.asyncio
    async def test_message_received_by_agent(self):
        """Agent factory receives messages via queue."""
        m = AgentSpawnModule()
        m.set_agent_factory(_make_factory(delay=0.3))

        result = await m.execute("spawn_agent", {
            "name": "msg-test",
            "objective": "Process messages",
        })
        spawn_id = result["spawn_id"]

        # Send message while agent is running
        await asyncio.sleep(0.05)
        await m.execute("send_message", {
            "spawn_id": spawn_id,
            "message": "Extra context here",
        })

        wait = await m.execute("wait_agent", {"spawn_id": spawn_id, "timeout": 5})
        assert wait["status"] == "completed"
        # The mock factory should include the message in output
        assert "Extra context here" in wait["output"]


# ─── Event propagation tests ───────────────────────────────────────


class TestEventPropagation:
    @pytest.mark.asyncio
    async def test_events_captured(self):
        m = AgentSpawnModule()
        m.set_agent_factory(_make_factory(delay=0.2))

        result = await m.execute("spawn_agent", {
            "name": "eventer",
            "objective": "Generate events",
        })
        spawn_id = result["spawn_id"]

        await m.execute("wait_agent", {"spawn_id": spawn_id, "timeout": 5})

        # Check events were captured
        spawned = m._spawned[spawn_id]
        assert len(spawned.events) > 0
        assert spawned.events[0]["type"] == "tool_call"

    @pytest.mark.asyncio
    async def test_event_callback_called(self):
        m = AgentSpawnModule()
        m.set_agent_factory(_make_factory(delay=0.2))

        received_events = []

        async def callback(sid, event):
            received_events.append((sid, event))

        m.set_event_callback(callback)

        result = await m.execute("spawn_agent", {
            "name": "cb-test",
            "objective": "Emit events",
        })
        spawn_id = result["spawn_id"]

        await m.execute("wait_agent", {"spawn_id": spawn_id, "timeout": 5})

        assert len(received_events) > 0
        assert received_events[0][0] == spawn_id


# ─── Persistence tests ─────────────────────────────────────────────


class TestAgentPersistence:
    @pytest.mark.asyncio
    async def test_result_persisted_to_kv(self):
        kv = MockKVStore()
        m = AgentSpawnModule()
        m.set_agent_factory(_make_factory(delay=0.1))
        m.set_kv_store(kv)

        result = await m.execute("spawn_agent", {
            "name": "persist-test",
            "objective": "Do something",
        })
        spawn_id = result["spawn_id"]

        await m.execute("wait_agent", {"spawn_id": spawn_id, "timeout": 5})

        # Check KV store has the result
        key = f"llmos:agent_spawn:result:{spawn_id}"
        raw = await kv.get(key)
        assert raw is not None
        persisted = json.loads(raw)
        assert persisted["name"] == "persist-test"
        assert persisted["status"] == "completed"
        assert "Done." in persisted["result"]

    @pytest.mark.asyncio
    async def test_history_index_updated(self):
        kv = MockKVStore()
        m = AgentSpawnModule()
        m.set_agent_factory(_make_factory(delay=0.05))
        m.set_kv_store(kv)

        ids = []
        for i in range(3):
            r = await m.execute("spawn_agent", {
                "name": f"h-{i}",
                "objective": f"Task {i}",
            })
            ids.append(r["spawn_id"])

        for sid in ids:
            await m.execute("wait_agent", {"spawn_id": sid, "timeout": 5})

        raw = await kv.get("llmos:agent_spawn:history")
        history = json.loads(raw)
        assert len(history) == 3
        for sid in ids:
            assert sid in history

    @pytest.mark.asyncio
    async def test_get_result_from_persisted(self):
        """After clearing memory, can still get result from KV."""
        kv = MockKVStore()
        m = AgentSpawnModule()
        m.set_agent_factory(_make_factory(delay=0.05))
        m.set_kv_store(kv)

        result = await m.execute("spawn_agent", {
            "name": "recall",
            "objective": "Remember me",
        })
        spawn_id = result["spawn_id"]
        await m.execute("wait_agent", {"spawn_id": spawn_id, "timeout": 5})

        # Clear in-memory state
        m._spawned.clear()

        # Should still find result via KV fallback
        get = await m.execute("get_result", {"spawn_id": spawn_id})
        assert get["name"] == "recall"
        assert get["status"] == "completed"

    @pytest.mark.asyncio
    async def test_no_kv_store_works_normally(self):
        """Without KV store, persistence is silently skipped."""
        m = AgentSpawnModule()
        m.set_agent_factory(_make_factory(delay=0.05))

        result = await m.execute("spawn_agent", {
            "name": "no-kv",
            "objective": "Works anyway",
        })
        spawn_id = result["spawn_id"]
        wait = await m.execute("wait_agent", {"spawn_id": spawn_id, "timeout": 5})
        assert wait["status"] == "completed"
