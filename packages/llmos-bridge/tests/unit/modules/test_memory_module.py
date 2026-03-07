"""Tests for the memory module and its backends."""

import asyncio
import json
import pytest
from pathlib import Path

from llmos_bridge.modules.memory.module import MemoryModule
from llmos_bridge.modules.memory.backends.base import BaseMemoryBackend, MemoryEntry
from llmos_bridge.modules.memory.backends.kv_backend import KVMemoryBackend
from llmos_bridge.modules.memory.backends.file_backend import FileMemoryBackend
from llmos_bridge.modules.memory.backends.cognitive_backend import (
    CognitiveMemoryBackend, Objective,
)


# ─── Mock KV Store ──────────────────────────────────────────────────

class MockKVStore:
    def __init__(self):
        self._data: dict[str, str] = {}

    async def get(self, key: str):
        raw = self._data.get(key)
        if raw is None:
            return None
        return json.loads(raw)

    async def set(self, key: str, value, session_id=None, ttl_seconds=None):
        self._data[key] = json.dumps(value, default=str)

    async def delete(self, key: str):
        self._data.pop(key, None)

    async def list_keys(self, session_id=None):
        return list(self._data.keys())

    async def init(self):
        pass

    async def close(self):
        pass


# ─── BaseMemoryBackend Tests ────────────────────────────────────────

class TestBaseMemoryBackend:
    def test_abstract_methods(self):
        """BaseMemoryBackend cannot be instantiated directly."""
        with pytest.raises(TypeError):
            BaseMemoryBackend()

    def test_info_method(self):
        """Custom backend reports its info correctly."""
        class DummyBackend(BaseMemoryBackend):
            BACKEND_ID = "dummy"
            DESCRIPTION = "A test backend"
            async def init(self): pass
            async def close(self): pass
            async def store(self, key, value, **kw): return MemoryEntry(key=key, value=value)
            async def recall(self, key): return None
            async def delete(self, key): return False
            async def list_keys(self, **kw): return []

        b = DummyBackend()
        info = b.info()
        assert info["backend_id"] == "dummy"
        assert info["supports_search"] is False

    def test_search_support_detected(self):
        """Backend with custom search reports supports_search=True."""
        class SearchBackend(BaseMemoryBackend):
            BACKEND_ID = "search"
            DESCRIPTION = "Searchable"
            async def init(self): pass
            async def close(self): pass
            async def store(self, key, value, **kw): return MemoryEntry(key=key, value=value)
            async def recall(self, key): return None
            async def delete(self, key): return False
            async def list_keys(self, **kw): return []
            async def search(self, query, **kw): return []

        b = SearchBackend()
        assert b.info()["supports_search"] is True


# ─── KV Backend Tests ───────────────────────────────────────────────

class TestKVBackend:
    @pytest.mark.asyncio
    async def test_store_and_recall(self):
        kv = MockKVStore()
        backend = KVMemoryBackend(namespace="test")
        backend.set_store(kv)

        entry = await backend.store("lang", "Python")
        assert entry.key == "lang"
        assert entry.value == "Python"
        assert entry.backend == "kv"

        result = await backend.recall("lang")
        assert result is not None
        assert result.value == "Python"

    @pytest.mark.asyncio
    async def test_recall_missing_key(self):
        kv = MockKVStore()
        backend = KVMemoryBackend(namespace="test")
        backend.set_store(kv)

        result = await backend.recall("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_delete(self):
        kv = MockKVStore()
        backend = KVMemoryBackend(namespace="test")
        backend.set_store(kv)

        await backend.store("x", "1")
        assert await backend.delete("x") is True
        assert await backend.recall("x") is None
        assert await backend.delete("x") is False

    @pytest.mark.asyncio
    async def test_list_keys(self):
        kv = MockKVStore()
        backend = KVMemoryBackend(namespace="test")
        backend.set_store(kv)

        await backend.store("a", "1")
        await backend.store("b", "2")
        keys = await backend.list_keys()
        assert set(keys) == {"a", "b"}

    @pytest.mark.asyncio
    async def test_list_keys_with_prefix(self):
        kv = MockKVStore()
        backend = KVMemoryBackend(namespace="test")
        backend.set_store(kv)

        await backend.store("user.name", "Alice")
        await backend.store("user.age", "30")
        await backend.store("system.os", "Linux")

        keys = await backend.list_keys(prefix="user.")
        assert len(keys) == 2
        assert all(k.startswith("user.") for k in keys)

    @pytest.mark.asyncio
    async def test_clear(self):
        kv = MockKVStore()
        backend = KVMemoryBackend(namespace="test")
        backend.set_store(kv)

        await backend.store("a", "1")
        await backend.store("b", "2")
        count = await backend.clear()
        assert count == 2
        assert await backend.list_keys() == []


# ─── File Backend Tests ─────────────────────────────────────────────

class TestFileBackend:
    @pytest.mark.asyncio
    async def test_store_and_recall(self, tmp_path):
        f = tmp_path / "mem.md"
        backend = FileMemoryBackend(file_path=f)
        await backend.init()

        entry = await backend.store("Architecture", "Layered design with 3 tiers")
        assert entry.key == "Architecture"

        result = await backend.recall("Architecture")
        assert result is not None
        assert "Layered design" in result.value

    @pytest.mark.asyncio
    async def test_recall_missing(self, tmp_path):
        f = tmp_path / "mem.md"
        backend = FileMemoryBackend(file_path=f)
        await backend.init()

        assert await backend.recall("nope") is None

    @pytest.mark.asyncio
    async def test_delete(self, tmp_path):
        f = tmp_path / "mem.md"
        backend = FileMemoryBackend(file_path=f)
        await backend.init()

        await backend.store("x", "val")
        assert await backend.delete("x") is True
        assert await backend.recall("x") is None

    @pytest.mark.asyncio
    async def test_list_keys(self, tmp_path):
        f = tmp_path / "mem.md"
        backend = FileMemoryBackend(file_path=f)
        await backend.init()

        await backend.store("A", "1")
        await backend.store("B", "2")
        keys = await backend.list_keys()
        assert set(keys) == {"A", "B"}

    @pytest.mark.asyncio
    async def test_search(self, tmp_path):
        f = tmp_path / "mem.md"
        backend = FileMemoryBackend(file_path=f)
        await backend.init()

        await backend.store("Python", "A programming language")
        await backend.store("JavaScript", "A web language")

        results = await backend.search("programming")
        assert len(results) == 1
        assert results[0].key == "Python"

    @pytest.mark.asyncio
    async def test_read_all(self, tmp_path):
        f = tmp_path / "mem.md"
        backend = FileMemoryBackend(file_path=f)
        await backend.init()

        await backend.store("Key", "Value")
        text = await backend.read_all()
        assert "Key" in text
        assert "Value" in text


# ─── Cognitive Backend Tests ────────────────────────────────────────

class TestCognitiveBackend:
    @pytest.mark.asyncio
    async def test_set_objective(self):
        backend = CognitiveMemoryBackend()
        await backend.init()

        obj = backend.set_objective(
            "Build a fitness app",
            sub_goals=["Design UI", "Build API", "Write tests"],
        )
        assert obj.goal == "Build a fitness app"
        assert len(obj.sub_goals) == 3
        assert obj.progress == 0.0
        assert not obj.completed

    @pytest.mark.asyncio
    async def test_get_objective(self):
        backend = CognitiveMemoryBackend()
        await backend.init()

        assert backend.get_objective() is None
        backend.set_objective("Test goal")
        obj = backend.get_objective()
        assert obj is not None
        assert obj.goal == "Test goal"

    @pytest.mark.asyncio
    async def test_update_progress(self):
        backend = CognitiveMemoryBackend()
        await backend.init()
        backend.set_objective("Goal")

        backend.update_progress(0.5)
        assert backend.get_objective().progress == 0.5

        backend.update_progress(1.0)
        assert backend.get_objective().completed is True

    @pytest.mark.asyncio
    async def test_complete_objective(self):
        backend = CognitiveMemoryBackend()
        await backend.init()
        backend.set_objective("Goal")

        result = backend.complete_objective()
        assert result["goal"] == "Goal"
        assert backend.get_objective() is None

    @pytest.mark.asyncio
    async def test_complete_no_objective(self):
        backend = CognitiveMemoryBackend()
        await backend.init()

        result = backend.complete_objective()
        assert "error" in result

    @pytest.mark.asyncio
    async def test_record_decision(self):
        backend = CognitiveMemoryBackend()
        await backend.init()
        backend.set_objective("Build app")

        backend.record_decision("Read API docs", "Need to understand endpoints", "directly related")
        assert len(backend._recent_decisions) == 1

    @pytest.mark.asyncio
    async def test_get_objective_context(self):
        backend = CognitiveMemoryBackend()
        await backend.init()
        backend.set_objective("Build app", sub_goals=["Design", "Implement"])

        ctx = backend.get_objective_context()
        assert "objective" in ctx
        assert ctx["objective"]["goal"] == "Build app"
        assert "0%" in ctx["objective"]["progress"]

    @pytest.mark.asyncio
    async def test_format_for_prompt(self):
        backend = CognitiveMemoryBackend()
        await backend.init()
        backend.set_objective("Build app")

        text = backend.format_for_prompt()
        assert "Build app" in text
        assert "ACTIVE OBJECTIVE" in text

    @pytest.mark.asyncio
    async def test_format_for_prompt_no_objective(self):
        backend = CognitiveMemoryBackend()
        await backend.init()

        assert backend.format_for_prompt() == ""

    @pytest.mark.asyncio
    async def test_store_objective_via_interface(self):
        backend = CognitiveMemoryBackend()
        await backend.init()

        entry = await backend.store("__objective__", "Build fitness app", metadata={"sub_goals": ["UI", "API"]})
        assert entry.key == "__objective__"
        obj = backend.get_objective()
        assert obj.goal == "Build fitness app"

    @pytest.mark.asyncio
    async def test_recall_objective_via_interface(self):
        backend = CognitiveMemoryBackend()
        await backend.init()
        backend.set_objective("Test")

        entry = await backend.recall("__objective__")
        assert entry is not None
        assert entry.value["goal"] == "Test"

    @pytest.mark.asyncio
    async def test_store_and_recall_regular_key(self):
        backend = CognitiveMemoryBackend()
        await backend.init()

        await backend.store("language", "Python")
        entry = await backend.recall("language")
        assert entry is not None
        assert entry.value == "Python"

    @pytest.mark.asyncio
    async def test_search(self):
        backend = CognitiveMemoryBackend()
        await backend.init()
        backend.set_objective("Build fitness app")
        await backend.store("tech_stack", "React Native + Node.js")

        results = await backend.search("fitness")
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_persistence(self, tmp_path):
        path = tmp_path / "cognitive.json"

        # Save state
        b1 = CognitiveMemoryBackend(persistence_path=path)
        await b1.init()
        b1.set_objective("Persist me")
        await b1.store("key1", "val1")
        await b1.close()

        # Reload state
        b2 = CognitiveMemoryBackend(persistence_path=path)
        await b2.init()
        obj = b2.get_objective()
        assert obj is not None
        assert obj.goal == "Persist me"
        entry = await b2.recall("key1")
        assert entry is not None
        assert entry.value == "val1"

    @pytest.mark.asyncio
    async def test_extract_tags(self):
        tags = CognitiveMemoryBackend._extract_tags("Build a complete fitness mobile application")
        assert "fitness" in tags
        assert "mobile" in tags
        assert "application" in tags
        assert "a" not in tags

    @pytest.mark.asyncio
    async def test_objective_history(self):
        backend = CognitiveMemoryBackend()
        await backend.init()
        backend.set_objective("Goal 1")
        backend.set_objective("Goal 2")  # archives Goal 1

        assert len(backend._objective_history) == 1
        assert backend._objective_history[0]["goal"] == "Goal 1"
        assert backend.get_objective().goal == "Goal 2"


# ─── MemoryModule Tests ─────────────────────────────────────────────

class TestMemoryModule:
    def _make_module(self) -> MemoryModule:
        """Create a module with mock backends."""
        mod = MemoryModule()

        kv = KVMemoryBackend(namespace="test")
        kv.set_store(MockKVStore())
        mod.register_backend(kv)

        cog = CognitiveMemoryBackend()
        mod.register_backend(cog)

        return mod

    def test_module_id(self):
        mod = MemoryModule()
        assert mod.MODULE_ID == "memory"
        assert mod.MODULE_TYPE == "system"

    def test_register_backend(self):
        mod = MemoryModule()
        kv = KVMemoryBackend()
        mod.register_backend(kv)
        assert "kv" in mod.backends
        assert mod.get_backend("kv") is kv

    def test_unregister_backend(self):
        mod = MemoryModule()
        kv = KVMemoryBackend()
        mod.register_backend(kv)
        mod.unregister_backend("kv")
        assert "kv" not in mod.backends

    def test_default_backend(self):
        mod = MemoryModule()
        assert mod.get_backend() is None  # no backends registered

        kv = KVMemoryBackend()
        mod.register_backend(kv)
        assert mod.get_backend() is kv  # default is "kv"

    def test_manifest(self):
        mod = self._make_module()
        manifest = mod.get_manifest()
        assert manifest.module_id == "memory"
        action_names = [a.name for a in manifest.actions]
        assert "store" in action_names
        assert "recall" in action_names
        assert "search" in action_names
        assert "set_objective" in action_names
        assert "get_context" in action_names
        assert "update_progress" in action_names
        assert "list_backends" in action_names

    @pytest.mark.asyncio
    async def test_action_store_and_recall(self):
        mod = self._make_module()
        await mod.on_start()

        result = await mod.execute("store", {"key": "x", "value": "42", "backend": "kv"})
        assert result["stored"] is True

        result = await mod.execute("recall", {"key": "x", "backend": "kv"})
        assert result["found"] is True
        assert result["value"] == "42"

    @pytest.mark.asyncio
    async def test_action_recall_missing(self):
        mod = self._make_module()
        await mod.on_start()

        result = await mod.execute("recall", {"key": "missing", "backend": "kv"})
        assert result["found"] is False

    @pytest.mark.asyncio
    async def test_action_store_missing_key(self):
        mod = self._make_module()
        await mod.on_start()

        result = await mod.execute("store", {"value": "x"})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_action_unknown_backend(self):
        mod = self._make_module()
        await mod.on_start()

        result = await mod.execute("store", {"key": "x", "value": "y", "backend": "redis"})
        assert "error" in result
        assert "redis" in result["error"]

    @pytest.mark.asyncio
    async def test_action_delete(self):
        mod = self._make_module()
        await mod.on_start()

        await mod.execute("store", {"key": "del_me", "value": "1", "backend": "kv"})
        result = await mod.execute("delete", {"key": "del_me", "backend": "kv"})
        assert result["deleted"] is True

    @pytest.mark.asyncio
    async def test_action_list_keys(self):
        mod = self._make_module()
        await mod.on_start()

        await mod.execute("store", {"key": "lk_a", "value": "1", "backend": "kv"})
        await mod.execute("store", {"key": "lk_b", "value": "2", "backend": "kv"})

        result = await mod.execute("list_keys", {"backend": "kv", "prefix": "lk_"})
        assert set(result["keys"]) == {"lk_a", "lk_b"}

    @pytest.mark.asyncio
    async def test_action_list_backends(self):
        mod = self._make_module()
        await mod.on_start()

        result = await mod.execute("list_backends", {})
        assert result["count"] == 2
        ids = [b["backend_id"] for b in result["backends"]]
        assert "kv" in ids
        assert "cognitive" in ids

    @pytest.mark.asyncio
    async def test_action_set_objective(self):
        mod = self._make_module()
        await mod.on_start()

        result = await mod.execute("set_objective", {
            "goal": "Build a fitness app",
            "sub_goals": ["Design", "Implement", "Test"],
        })
        assert result["objective"]["goal"] == "Build a fitness app"

    @pytest.mark.asyncio
    async def test_action_get_context(self):
        mod = self._make_module()
        await mod.on_start()

        await mod.execute("set_objective", {"goal": "Test goal"})
        result = await mod.execute("get_context", {})
        assert "objective" in result
        assert result["objective"]["goal"] == "Test goal"

    @pytest.mark.asyncio
    async def test_action_update_progress(self):
        mod = self._make_module()
        await mod.on_start()

        await mod.execute("set_objective", {"goal": "Goal"})
        result = await mod.execute("update_progress", {"progress": 0.75})
        assert result["progress"] == 0.75

    @pytest.mark.asyncio
    async def test_action_complete_objective(self):
        mod = self._make_module()
        await mod.on_start()

        await mod.execute("set_objective", {"goal": "Goal"})
        result = await mod.execute("update_progress", {"progress": 1.0, "complete": True})
        assert "goal" in result  # returns the completed objective

    @pytest.mark.asyncio
    async def test_action_set_objective_no_cognitive_backend(self):
        mod = MemoryModule()
        kv = KVMemoryBackend(namespace="test")
        kv.set_store(MockKVStore())
        mod.register_backend(kv)
        await mod.on_start()

        result = await mod.execute("set_objective", {"goal": "fail"})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_search_across_backends(self):
        mod = self._make_module()
        await mod.on_start()

        # Store in cognitive backend
        await mod.execute("set_objective", {"goal": "fitness app"})
        await mod.execute("store", {"key": "tech", "value": "React Native", "backend": "cognitive"})

        result = await mod.execute("search", {"query": "fitness"})
        assert result["count"] >= 1

    @pytest.mark.asyncio
    async def test_health_check(self):
        mod = self._make_module()
        await mod.on_start()

        health = await mod.health_check()
        assert health["status"] == "ok"
        assert "kv" in health["backends"]
        assert "cognitive" in health["backends"]

    @pytest.mark.asyncio
    async def test_metrics(self):
        mod = self._make_module()
        m = mod.metrics()
        assert m["backends_registered"] == 2

    @pytest.mark.asyncio
    async def test_cognitive_prompt(self):
        mod = self._make_module()
        await mod.on_start()

        # No objective = empty prompt
        assert mod.get_cognitive_prompt() == ""

        # With objective
        await mod.execute("set_objective", {"goal": "Build app"})
        prompt = mod.get_cognitive_prompt()
        assert "Build app" in prompt
        assert "ACTIVE OBJECTIVE" in prompt


# ─── Integration with tool_executor ─────────────────────────────────

class TestStandaloneMemoryIntegration:
    def test_standalone_module_info_includes_memory(self):
        from llmos_bridge.apps.tool_executor import StandaloneToolExecutor
        executor = StandaloneToolExecutor()
        info = executor.get_module_info()
        assert "memory" in info
        action_names = [a["name"] for a in info["memory"]["actions"]]
        assert "store" in action_names
        assert "set_objective" in action_names

    @pytest.mark.asyncio
    async def test_standalone_memory_execution(self):
        from llmos_bridge.apps.tool_executor import StandaloneToolExecutor

        executor = StandaloneToolExecutor()
        mod = MemoryModule()
        kv = KVMemoryBackend(namespace="test")
        kv.set_store(MockKVStore())
        mod.register_backend(kv)
        cog = CognitiveMemoryBackend()
        mod.register_backend(cog)
        await mod.on_start()
        executor.set_memory_module(mod)

        result = await executor.execute("memory", "store", {"key": "x", "value": "1"})
        assert result.get("stored") is True

        result = await executor.execute("memory", "recall", {"key": "x"})
        assert result.get("found") is True
