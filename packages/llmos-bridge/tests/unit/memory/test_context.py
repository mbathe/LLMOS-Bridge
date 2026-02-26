"""Unit tests — ContextBuilder and BuiltContext."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from llmos_bridge.memory.context import BuiltContext, ContextBuilder
from llmos_bridge.memory.store import KeyValueStore
from llmos_bridge.modules.manifest import ActionSpec, ModuleManifest, ParamSpec


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_manifest(module_id: str = "filesystem", version: str = "1.0.0") -> ModuleManifest:
    return ModuleManifest(
        module_id=module_id,
        version=version,
        description=f"The {module_id} module.",
        platforms=["all"],
        tags=["test"],
        actions=[
            ActionSpec(
                name="read_file",
                description="Read a file.",
                params=[ParamSpec("path", "string", "File path.")],
                returns="object",
            ),
            ActionSpec(
                name="write_file",
                description="Write a file.",
                params=[
                    ParamSpec("path", "string", "File path."),
                    ParamSpec("content", "string", "Content."),
                ],
                returns="object",
            ),
        ],
    )


# ---------------------------------------------------------------------------
# BuiltContext
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuiltContext:
    def test_to_prompt_section_minimal(self) -> None:
        ctx = BuiltContext(capability_summary="No modules loaded.\n")
        section = ctx.to_prompt_section()
        assert "LLMOS Bridge" in section
        assert "No modules loaded." in section

    def test_to_prompt_section_with_memory(self) -> None:
        ctx = BuiltContext(
            capability_summary="summary",
            memory_entries=[{"key": "user_name", "value": "Alice"}],
        )
        section = ctx.to_prompt_section()
        assert "Memory" in section
        assert "user_name" in section
        assert "Alice" in section

    def test_to_prompt_section_with_semantic_results(self) -> None:
        ctx = BuiltContext(
            capability_summary="summary",
            semantic_results=["Previous task: wrote report.txt", "Created archive.zip"],
        )
        section = ctx.to_prompt_section()
        assert "Relevant Past Context" in section
        assert "wrote report.txt" in section

    def test_total_chars_set_after_build(self) -> None:
        ctx = BuiltContext(capability_summary="short summary", total_chars=42)
        assert ctx.total_chars == 42

    def test_to_prompt_section_no_memory_no_semantic(self) -> None:
        ctx = BuiltContext(capability_summary="caps")
        section = ctx.to_prompt_section()
        assert "Memory" not in section
        assert "Relevant Past Context" not in section


# ---------------------------------------------------------------------------
# ContextBuilder — no stores
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestContextBuilderNoStores:
    async def test_build_no_manifests(self) -> None:
        builder = ContextBuilder()
        ctx = await builder.build()
        assert "No modules loaded" in ctx.capability_summary

    async def test_build_with_manifests(self) -> None:
        manifest = make_manifest()
        builder = ContextBuilder(manifests=[manifest])
        ctx = await builder.build()
        assert "filesystem" in ctx.capability_summary
        assert "read_file" in ctx.capability_summary

    async def test_build_capability_summary_multiple_modules(self) -> None:
        m1 = make_manifest("filesystem", "1.0.0")
        m2 = make_manifest("excel", "2.0.0")
        builder = ContextBuilder(manifests=[m1, m2])
        ctx = await builder.build()
        assert "filesystem" in ctx.capability_summary
        assert "excel" in ctx.capability_summary

    async def test_total_chars_computed(self) -> None:
        builder = ContextBuilder()
        ctx = await builder.build()
        assert ctx.total_chars > 0

    async def test_no_memory_entries_when_no_kv(self) -> None:
        builder = ContextBuilder()
        ctx = await builder.build(memory_keys=["some_key"])
        assert ctx.memory_entries == []

    async def test_no_semantic_results_when_no_vector(self) -> None:
        builder = ContextBuilder()
        ctx = await builder.build(query="test query")
        assert ctx.semantic_results == []


# ---------------------------------------------------------------------------
# ContextBuilder — with KV store
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestContextBuilderWithKVStore:
    @pytest_asyncio.fixture
    async def kv_store(self, tmp_path: Path) -> KeyValueStore:
        store = KeyValueStore(tmp_path / "kv.db")
        await store.init()
        yield store
        await store.close()

    async def test_build_loads_memory_keys(self, kv_store: KeyValueStore) -> None:
        await kv_store.set("user_name", "Alice")
        await kv_store.set("session_id", "abc123")

        builder = ContextBuilder(kv_store=kv_store)
        ctx = await builder.build(memory_keys=["user_name", "session_id"])
        keys = [e["key"] for e in ctx.memory_entries]
        assert "user_name" in keys
        assert "session_id" in keys

    async def test_build_skips_missing_keys(self, kv_store: KeyValueStore) -> None:
        builder = ContextBuilder(kv_store=kv_store)
        ctx = await builder.build(memory_keys=["nonexistent_key"])
        # get_many returns only existing keys
        assert ctx.memory_entries == []

    async def test_memory_entries_appear_in_prompt(self, kv_store: KeyValueStore) -> None:
        await kv_store.set("project", "LLMOS")
        builder = ContextBuilder(kv_store=kv_store)
        ctx = await builder.build(memory_keys=["project"])
        section = ctx.to_prompt_section()
        assert "project" in section
        assert "LLMOS" in section


# ---------------------------------------------------------------------------
# ContextBuilder — with vector store mock
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestContextBuilderWithVectorStore:
    async def test_build_performs_semantic_search(self) -> None:
        vector_store = MagicMock()
        search_result = MagicMock()
        search_result.text = "Previously wrote report.txt"
        vector_store.search = AsyncMock(return_value=[search_result])

        builder = ContextBuilder(vector_store=vector_store)
        ctx = await builder.build(query="write a report", top_k=1)

        vector_store.search.assert_called_once_with("write a report", top_k=1)
        assert "Previously wrote report.txt" in ctx.semantic_results

    async def test_build_no_search_when_no_query(self) -> None:
        vector_store = MagicMock()
        vector_store.search = AsyncMock(return_value=[])

        builder = ContextBuilder(vector_store=vector_store)
        ctx = await builder.build(query=None)
        vector_store.search.assert_not_called()
        assert ctx.semantic_results == []


# ---------------------------------------------------------------------------
# ContextBuilder — update_manifests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestContextBuilderUpdateManifests:
    async def test_update_manifests_replaces_existing(self) -> None:
        m1 = make_manifest("filesystem")
        builder = ContextBuilder(manifests=[m1])

        ctx_before = await builder.build()
        assert "filesystem" in ctx_before.capability_summary

        m2 = make_manifest("excel")
        builder.update_manifests([m2])
        ctx_after = await builder.build()
        assert "excel" in ctx_after.capability_summary
        assert "filesystem" not in ctx_after.capability_summary

    async def test_update_manifests_to_empty(self) -> None:
        m1 = make_manifest("filesystem")
        builder = ContextBuilder(manifests=[m1])
        builder.update_manifests([])
        ctx = await builder.build()
        assert "No modules loaded" in ctx.capability_summary


# ---------------------------------------------------------------------------
# ContextBuilder — truncation warning
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestContextBuilderTruncationWarning:
    async def test_no_truncation_warning_for_small_context(self) -> None:
        builder = ContextBuilder(max_chars=100_000)
        ctx = await builder.build()
        # Just ensure it runs without error and returns a context
        assert ctx.total_chars > 0

    async def test_truncation_triggered_when_max_chars_exceeded(self) -> None:
        # Set max_chars very small to force truncation warning
        builder = ContextBuilder(
            manifests=[make_manifest()],
            max_chars=5,  # absurdly small
        )
        ctx = await builder.build()
        # Context is still returned (truncation is a warning, not an error)
        assert ctx.total_chars > 5
