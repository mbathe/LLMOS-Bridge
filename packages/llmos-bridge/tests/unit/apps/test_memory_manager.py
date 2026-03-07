"""Tests for AppMemoryManager — multi-level memory."""

import json
import pytest
from pathlib import Path

from llmos_bridge.apps.expression import ExpressionContext, ExpressionEngine
from llmos_bridge.apps.memory_manager import AppMemoryManager
from llmos_bridge.apps.models import (
    EpisodicMemoryConfig,
    EpisodicRecallConfig,
    MemoryConfig,
    ProjectMemoryConfig,
)


# ─── Mock stores ──────────────────────────────────────────────────────


class MockKVStore:
    def __init__(self):
        self._data = {}

    async def set(self, key, value, session_id=None, ttl_seconds=None):
        self._data[key] = value

    async def get(self, key):
        return self._data.get(key)

    async def get_many(self, keys):
        return {k: self._data[k] for k in keys if k in self._data}

    async def delete(self, key):
        self._data.pop(key, None)


class MockVectorStore:
    def __init__(self):
        self._docs = {}

    async def add(self, doc_id, text, metadata=None):
        self._docs[doc_id] = _MockEntry(doc_id, text, metadata or {})

    async def search(self, query, top_k=3):
        results = list(self._docs.values())[:top_k]
        return results

    async def count(self):
        return len(self._docs)


class _MockEntry:
    def __init__(self, id, text, metadata, distance=0.1):
        self.id = id
        self.text = text
        self.metadata = metadata
        self.distance = distance


# ─── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def kv():
    return MockKVStore()


@pytest.fixture
def vector():
    return MockVectorStore()


@pytest.fixture
def ctx():
    return ExpressionContext(variables={"workspace": "/test", "data_dir": "/test/.data"})


@pytest.fixture
def mgr(kv, vector, ctx):
    config = MemoryConfig()
    return AppMemoryManager(config, kv_store=kv, vector_store=vector, expr_context=ctx)


# ─── Tests ────────────────────────────────────────────────────────────


class TestWorkingMemory:
    def test_set_get(self, mgr):
        mgr.set_working("key1", "value1")
        assert mgr.get_working("key1") == "value1"

    def test_get_default(self, mgr):
        assert mgr.get_working("missing", "default") == "default"

    def test_get_none(self, mgr):
        assert mgr.get_working("missing") is None

    def test_clear(self, mgr):
        mgr.set_working("a", 1)
        mgr.set_working("b", 2)
        mgr.clear_working()
        assert mgr.working == {}

    def test_working_dict(self, mgr):
        mgr.set_working("x", 42)
        assert mgr.working == {"x": 42}

    def test_overwrite(self, mgr):
        mgr.set_working("k", "v1")
        mgr.set_working("k", "v2")
        assert mgr.get_working("k") == "v2"


class TestConversationMemory:
    @pytest.mark.asyncio
    async def test_set_get(self, mgr, kv):
        await mgr.set_conversation("user_pref", {"theme": "dark"})
        result = await mgr.get_conversation("user_pref")
        assert result == {"theme": "dark"}

    @pytest.mark.asyncio
    async def test_get_missing(self, mgr):
        result = await mgr.get_conversation("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_many(self, mgr):
        await mgr.set_conversation("a", 1)
        await mgr.set_conversation("b", 2)
        result = await mgr.get_many_conversation(["a", "b", "c"])
        assert result == {"a": 1, "b": 2}

    @pytest.mark.asyncio
    async def test_no_kv_store(self, ctx):
        mgr = AppMemoryManager(expr_context=ctx)
        await mgr.set_conversation("k", "v")  # no-op
        result = await mgr.get_conversation("k")
        assert result is None


class TestEpisodicMemory:
    @pytest.mark.asyncio
    async def test_record_and_recall(self, mgr):
        await mgr.record_episode("ep1", "Fixed a bug in auth module", {"type": "fix"})
        results = await mgr.recall_episodes("auth bug", top_k=1)
        assert len(results) == 1
        assert results[0]["id"] == "ep1"

    @pytest.mark.asyncio
    async def test_recall_empty(self, mgr):
        results = await mgr.recall_episodes("anything")
        assert results == []

    @pytest.mark.asyncio
    async def test_no_vector_store(self, ctx):
        mgr = AppMemoryManager(expr_context=ctx)
        await mgr.record_episode("ep1", "test")  # no-op
        results = await mgr.recall_episodes("test")
        assert results == []


class TestProjectMemory:
    @pytest.mark.asyncio
    async def test_load_project_memory(self, tmp_path, ctx):
        mem_file = tmp_path / "MEMORY.md"
        mem_file.write_text("# Project\nThis is a Python project\nLine 3\n")

        config = MemoryConfig(project=ProjectMemoryConfig(
            path=str(mem_file),
            max_lines=200,
        ))
        mgr = AppMemoryManager(config, expr_context=ctx)
        text = await mgr.load_project_memory()
        assert "Python project" in text

    @pytest.mark.asyncio
    async def test_load_missing_file(self, ctx):
        config = MemoryConfig(project=ProjectMemoryConfig(
            path="/nonexistent/MEMORY.md",
        ))
        mgr = AppMemoryManager(config, expr_context=ctx)
        text = await mgr.load_project_memory()
        assert text == ""

    @pytest.mark.asyncio
    async def test_load_max_lines(self, tmp_path, ctx):
        mem_file = tmp_path / "MEMORY.md"
        lines = [f"Line {i}" for i in range(100)]
        mem_file.write_text("\n".join(lines))

        config = MemoryConfig(project=ProjectMemoryConfig(
            path=str(mem_file),
            max_lines=10,
        ))
        mgr = AppMemoryManager(config, expr_context=ctx)
        text = await mgr.load_project_memory()
        assert text.count("\n") <= 10

    @pytest.mark.asyncio
    async def test_save_project_memory(self, tmp_path, ctx):
        mem_file = tmp_path / "MEMORY.md"

        config = MemoryConfig(project=ProjectMemoryConfig(
            path=str(mem_file),
            agent_writable=True,
        ))
        mgr = AppMemoryManager(config, expr_context=ctx)
        await mgr.save_project_memory("# Updated\nNew content")
        assert mem_file.read_text() == "# Updated\nNew content"

    @pytest.mark.asyncio
    async def test_save_not_writable(self, tmp_path, ctx):
        mem_file = tmp_path / "MEMORY.md"

        config = MemoryConfig(project=ProjectMemoryConfig(
            path=str(mem_file),
            agent_writable=False,
        ))
        mgr = AppMemoryManager(config, expr_context=ctx)
        await mgr.save_project_memory("content")
        assert not mem_file.exists()

    @pytest.mark.asyncio
    async def test_no_project_config(self, ctx):
        mgr = AppMemoryManager(expr_context=ctx)
        text = await mgr.load_project_memory()
        assert text == ""


class TestBuildMemoryContext:
    @pytest.mark.asyncio
    async def test_includes_working(self, mgr):
        mgr.set_working("task", "fix bug")
        ctx = await mgr.build_memory_context()
        assert ctx["working"]["task"] == "fix bug"

    @pytest.mark.asyncio
    async def test_empty_when_nothing_configured(self, ctx):
        mgr = AppMemoryManager(expr_context=ctx)
        result = await mgr.build_memory_context()
        assert result == {}

    @pytest.mark.asyncio
    async def test_includes_project(self, tmp_path, ctx):
        mem_file = tmp_path / "MEMORY.md"
        mem_file.write_text("Project info")

        config = MemoryConfig(project=ProjectMemoryConfig(path=str(mem_file)))
        mgr = AppMemoryManager(config, expr_context=ctx)
        result = await mgr.build_memory_context()
        assert "project" in result

    @pytest.mark.asyncio
    async def test_includes_episodic(self, vector, ctx):
        config = MemoryConfig(episodic=EpisodicMemoryConfig(
            auto_recall=EpisodicRecallConfig(enabled=True, top_k=2),
        ))
        mgr = AppMemoryManager(config, vector_store=vector, expr_context=ctx)
        await mgr.record_episode("ep1", "past experience")
        result = await mgr.build_memory_context("similar query")
        assert "episodic" in result


class TestFormatForPrompt:
    def test_format_working(self, mgr):
        mgr.set_working("status", "in_progress")
        ctx = {"working": mgr.working}
        text = mgr.format_for_prompt(ctx)
        assert "Working Memory" in text
        assert "status" in text

    def test_format_project(self, mgr):
        text = mgr.format_for_prompt({"project": "This is a Python project"})
        assert "Project Memory" in text

    def test_format_episodic(self, mgr):
        text = mgr.format_for_prompt({"episodic": [{"text": "Past fix", "metadata": {}}]})
        assert "Relevant Past Episodes" in text

    def test_format_empty(self, mgr):
        text = mgr.format_for_prompt({})
        assert text == ""
