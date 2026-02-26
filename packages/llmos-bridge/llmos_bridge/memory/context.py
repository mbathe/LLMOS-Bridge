"""Memory layer — Context builder.

Assembles LLM-friendly context by combining:
  - Available module capabilities (Capability Manifest)
  - Key-value memory entries
  - Semantic memory search results
  - Recent plan history
  - Few-shot examples

The context is injected into the LLM system prompt or user message
by the SDK layer (langchain-llmos).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from llmos_bridge.logging import get_logger
from llmos_bridge.memory.store import KeyValueStore
from llmos_bridge.memory.vector import VectorStore
from llmos_bridge.modules.manifest import ModuleManifest

log = get_logger(__name__)

_MAX_CONTEXT_CHARS = 12_000


@dataclass
class BuiltContext:
    """Assembled context ready for injection into an LLM prompt."""

    capability_summary: str
    memory_entries: list[dict[str, Any]] = field(default_factory=list)
    semantic_results: list[str] = field(default_factory=list)
    total_chars: int = 0

    def to_prompt_section(self) -> str:
        parts = [
            "## LLMOS Bridge — Available Capabilities\n",
            self.capability_summary,
        ]
        if self.memory_entries:
            parts.append("\n## Memory\n")
            for entry in self.memory_entries:
                parts.append(f"- {entry['key']}: {json.dumps(entry['value'])}\n")
        if self.semantic_results:
            parts.append("\n## Relevant Past Context\n")
            for result in self.semantic_results:
                parts.append(f"- {result}\n")
        return "".join(parts)


class ContextBuilder:
    """Builds LLM context from available modules and memory.

    Usage::

        builder = ContextBuilder(
            manifests=[filesystem_manifest, os_exec_manifest],
            kv_store=kv_store,
            vector_store=vector_store,
        )
        context = await builder.build(query="create a report from CSV files")
    """

    def __init__(
        self,
        manifests: list[ModuleManifest] | None = None,
        kv_store: KeyValueStore | None = None,
        vector_store: VectorStore | None = None,
        max_chars: int = _MAX_CONTEXT_CHARS,
    ) -> None:
        self._manifests = manifests or []
        self._kv = kv_store
        self._vector = vector_store
        self._max_chars = max_chars

    async def build(
        self,
        query: str | None = None,
        memory_keys: list[str] | None = None,
        top_k: int = 3,
    ) -> BuiltContext:
        capability_summary = self._build_capability_summary()
        memory_entries: list[dict[str, Any]] = []
        semantic_results: list[str] = []

        if self._kv and memory_keys:
            kv_data = await self._kv.get_many(memory_keys)
            memory_entries = [{"key": k, "value": v} for k, v in kv_data.items()]

        if self._vector and query:
            results = await self._vector.search(query, top_k=top_k)
            semantic_results = [r.text for r in results]

        context = BuiltContext(
            capability_summary=capability_summary,
            memory_entries=memory_entries,
            semantic_results=semantic_results,
        )
        context.total_chars = len(context.to_prompt_section())

        if context.total_chars > self._max_chars:
            log.warning(
                "context_truncated",
                total_chars=context.total_chars,
                max_chars=self._max_chars,
            )

        return context

    def _build_capability_summary(self) -> str:
        if not self._manifests:
            return "No modules loaded.\n"

        lines = []
        for manifest in self._manifests:
            lines.append(f"### {manifest.module_id} (v{manifest.version})\n")
            lines.append(f"{manifest.description}\n\n")
            lines.append("Actions:\n")
            for action in manifest.actions:
                lines.append(f"  - `{action.name}`: {action.description}\n")
            lines.append("\n")

        return "".join(lines)

    def update_manifests(self, manifests: list[ModuleManifest]) -> None:
        self._manifests = manifests
