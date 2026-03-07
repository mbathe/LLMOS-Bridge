"""Tests for memory builtin tool and auto-include behavior."""

import asyncio
import json
import pytest

from llmos_bridge.apps.builtins import BuiltinToolExecutor
from llmos_bridge.apps.memory_manager import AppMemoryManager
from llmos_bridge.apps.models import MemoryConfig, ConversationMemoryConfig, ProjectMemoryConfig


class MockKVStore:
    def __init__(self):
        self._data: dict[str, str] = {}

    async def get(self, key: str):
        return self._data.get(key)

    async def set(self, key: str, value: str, ttl_seconds=None):
        self._data[key] = value

    async def delete(self, key: str):
        self._data.pop(key, None)


class MockVectorStore:
    def __init__(self):
        self._entries: list[dict] = []

    async def add(self, doc_id: str, text: str, metadata: dict = None):
        self._entries.append({"id": doc_id, "text": text, "metadata": metadata or {}})

    async def search(self, query: str, top_k: int = 3):
        class Entry:
            def __init__(self, d):
                self.id = d["id"]
                self.text = d["text"]
                self.metadata = d["metadata"]
                self.distance = 0.1
        return [Entry(e) for e in self._entries[:top_k]]


# ─── Memory builtin tests ──────────────────────────────────────────


class TestMemoryBuiltin:
    @pytest.mark.asyncio
    async def test_memory_without_manager_returns_error(self):
        executor = BuiltinToolExecutor()
        result = await executor.execute("memory", {"action": "store", "key": "x", "value": "y"})
        assert "error" in result
        assert "memory manager" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_store_and_recall_working(self):
        kv = MockKVStore()
        config = MemoryConfig()
        mgr = AppMemoryManager(config, kv_store=kv)

        executor = BuiltinToolExecutor(kv_store=kv)
        executor.set_memory_manager(mgr)

        # Store
        result = await executor.execute("memory", {
            "action": "store", "level": "working", "key": "language", "value": "Python",
        })
        assert result["stored"] is True
        assert result["level"] == "working"

        # Recall
        result = await executor.execute("memory", {
            "action": "recall", "level": "working", "key": "language",
        })
        assert result["found"] is True
        assert result["value"] == "Python"

    @pytest.mark.asyncio
    async def test_recall_missing_key(self):
        mgr = AppMemoryManager(MemoryConfig())
        executor = BuiltinToolExecutor()
        executor.set_memory_manager(mgr)

        result = await executor.execute("memory", {
            "action": "recall", "level": "working", "key": "nonexistent",
        })
        assert result["found"] is False

    @pytest.mark.asyncio
    async def test_store_and_recall_conversation(self):
        kv = MockKVStore()
        mgr = AppMemoryManager(MemoryConfig(), kv_store=kv)
        executor = BuiltinToolExecutor(kv_store=kv)
        executor.set_memory_manager(mgr)

        await executor.execute("memory", {
            "action": "store", "level": "conversation", "key": "user_pref", "value": "dark_mode",
        })
        result = await executor.execute("memory", {
            "action": "recall", "level": "conversation", "key": "user_pref",
        })
        assert result["found"] is True

    @pytest.mark.asyncio
    async def test_store_episodic(self):
        vec = MockVectorStore()
        mgr = AppMemoryManager(MemoryConfig(), vector_store=vec)
        executor = BuiltinToolExecutor()
        executor.set_memory_manager(mgr)

        result = await executor.execute("memory", {
            "action": "store", "level": "episodic", "key": "fix", "value": "Fixed the auth bug by adding token refresh",
        })
        assert result["stored"] is True
        assert "episode_id" in result

    @pytest.mark.asyncio
    async def test_search_episodic(self):
        vec = MockVectorStore()
        mgr = AppMemoryManager(MemoryConfig(), vector_store=vec)
        executor = BuiltinToolExecutor()
        executor.set_memory_manager(mgr)

        # Store something first
        await executor.execute("memory", {
            "action": "store", "level": "episodic", "key": "fix", "value": "Fixed auth bug",
        })

        # Search
        result = await executor.execute("memory", {
            "action": "search", "query": "authentication issue",
        })
        assert result["count"] == 1
        assert result["results"][0]["text"] == "Fixed auth bug"

    @pytest.mark.asyncio
    async def test_list_working_memory(self):
        mgr = AppMemoryManager(MemoryConfig())
        executor = BuiltinToolExecutor()
        executor.set_memory_manager(mgr)

        await executor.execute("memory", {"action": "store", "level": "working", "key": "a", "value": "1"})
        await executor.execute("memory", {"action": "store", "level": "working", "key": "b", "value": "2"})

        result = await executor.execute("memory", {"action": "list", "level": "working"})
        assert set(result["keys"]) == {"a", "b"}

    @pytest.mark.asyncio
    async def test_store_requires_key(self):
        mgr = AppMemoryManager(MemoryConfig())
        executor = BuiltinToolExecutor()
        executor.set_memory_manager(mgr)

        result = await executor.execute("memory", {"action": "store", "level": "working", "value": "no key"})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_unknown_level_returns_error(self):
        mgr = AppMemoryManager(MemoryConfig())
        executor = BuiltinToolExecutor()
        executor.set_memory_manager(mgr)

        result = await executor.execute("memory", {"action": "store", "level": "quantum", "key": "x", "value": "y"})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_unknown_action_returns_error(self):
        mgr = AppMemoryManager(MemoryConfig())
        executor = BuiltinToolExecutor()
        executor.set_memory_manager(mgr)

        result = await executor.execute("memory", {"action": "destroy"})
        assert "error" in result


# ─── Auto-include tests ───────────────────────────────────────────


class TestAutoIncludeBuiltins:
    def test_todo_auto_included(self):
        """Todo builtin is auto-included even if not declared in tools."""
        from llmos_bridge.apps.runtime import AppRuntime
        from llmos_bridge.apps.tool_registry import AppToolRegistry, ResolvedTool
        from llmos_bridge.apps.models import AppDefinition, AppConfig

        app_def = AppDefinition(app=AppConfig(name="test"))
        registry = AppToolRegistry({})
        resolved = []

        result = AppRuntime._auto_include_builtins(resolved, app_def, registry)
        names = [t.name for t in result]
        assert "todo" in names

    def test_memory_auto_included_when_configured(self):
        """Memory builtin is auto-included when memory levels are configured."""
        from llmos_bridge.apps.runtime import AppRuntime
        from llmos_bridge.apps.tool_registry import AppToolRegistry
        from llmos_bridge.apps.models import (
            AppDefinition, AppConfig, MemoryConfig, ConversationMemoryConfig,
        )

        memory = MemoryConfig(conversation=ConversationMemoryConfig())
        app_def = AppDefinition(app=AppConfig(name="test"), memory=memory)
        registry = AppToolRegistry({})
        resolved = []

        result = AppRuntime._auto_include_builtins(resolved, app_def, registry)
        names = [t.name for t in result]
        assert "memory" in names

    def test_memory_not_included_without_config(self):
        """Memory builtin is NOT auto-included when no memory levels are configured."""
        from llmos_bridge.apps.runtime import AppRuntime
        from llmos_bridge.apps.tool_registry import AppToolRegistry
        from llmos_bridge.apps.models import AppDefinition, AppConfig

        app_def = AppDefinition(app=AppConfig(name="test"))
        registry = AppToolRegistry({})
        resolved = []

        result = AppRuntime._auto_include_builtins(resolved, app_def, registry)
        names = [t.name for t in result]
        assert "memory" not in names

    def test_no_duplicate_if_already_declared(self):
        """If todo is already in tools, don't add it again."""
        from llmos_bridge.apps.runtime import AppRuntime
        from llmos_bridge.apps.tool_registry import AppToolRegistry, ResolvedTool
        from llmos_bridge.apps.models import AppDefinition, AppConfig

        app_def = AppDefinition(app=AppConfig(name="test"))
        registry = AppToolRegistry({})
        existing = [ResolvedTool(name="todo", module="", action="", description="", parameters={}, is_builtin=True)]

        result = AppRuntime._auto_include_builtins(existing, app_def, registry)
        todo_count = sum(1 for t in result if t.name == "todo")
        assert todo_count == 1


# ─── Conversation history tests ──────────────────────────────────


class TestConversationHistory:
    def test_inject_and_extract_history(self):
        """AgentRuntime can inject and extract conversation messages."""
        from llmos_bridge.apps.agent_runtime import AgentRuntime
        from llmos_bridge.apps.models import AgentConfig

        config = AgentConfig()
        # Use a stub LLM
        from llmos_bridge.apps.runtime import _StubLLMProvider
        llm = _StubLLMProvider()

        agent = AgentRuntime(agent_config=config, llm=llm, tools=[])

        history = [
            {"role": "user", "content": "What is Python?"},
            {"role": "assistant", "content": "Python is a programming language."},
            {"role": "user", "content": "Show me an example"},
        ]
        agent.inject_conversation_history(history)

        messages = agent.get_conversation_messages()
        assert len(messages) == 3
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "What is Python?"
        assert messages[1]["role"] == "assistant"


class TestCheckpointYAML:
    def test_checkpoint_field_in_app_config(self):
        """The checkpoint field is available in AppConfig."""
        from llmos_bridge.apps.models import AppConfig

        config = AppConfig(name="test", checkpoint=True)
        assert config.checkpoint is True

        config2 = AppConfig(name="test2")
        assert config2.checkpoint is False

    def test_checkpoint_in_yaml(self):
        """checkpoint: true can be set in YAML."""
        from llmos_bridge.apps.compiler import AppCompiler

        yaml_text = """
app:
  name: checkpoint-test
  checkpoint: true
flow:
  - id: step1
    action: filesystem.read_file
    params:
      path: /tmp/test
"""
        compiler = AppCompiler()
        app_def = compiler.compile_string(yaml_text)
        assert app_def.app.checkpoint is True
