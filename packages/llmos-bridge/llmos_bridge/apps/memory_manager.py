"""App-level memory manager — connects YAML memory config to backend stores.

Manages the 5 memory levels:
- working: In-memory dict (per-run, fast)
- conversation: SQLite KV (cross-turn, persistent)
- episodic: ChromaDB vector store (cross-session, semantic search)
- project: File-based (workspace-local, human-readable)
- procedural: SQLite (learned patterns, optional)

Each level is optional; if not configured in the YAML, it's skipped.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from .expression import ExpressionContext, ExpressionEngine
from .models import MemoryConfig

_SIZE_UNITS = {"b": 1, "kb": 1024, "mb": 1024**2, "gb": 1024**3, "tb": 1024**4}


def _parse_size(s: str) -> int:
    """Parse a human-readable size string (e.g. '100MB') to bytes."""
    match = re.match(r"^(\d+(?:\.\d+)?)\s*(B|KB|MB|GB|TB)$", s, re.IGNORECASE)
    if not match:
        return 0
    return int(float(match.group(1)) * _SIZE_UNITS.get(match.group(2).lower(), 1))

logger = logging.getLogger(__name__)


class AppMemoryManager:
    """Manages multi-level memory for an LLMOS application.

    Thin wrapper that connects the YAML memory configuration to
    the existing backend stores (KeyValueStore, VectorStore).
    """

    def __init__(
        self,
        config: MemoryConfig | None = None,
        *,
        kv_store: Any = None,       # KeyValueStore instance (conversation/procedural)
        vector_store: Any = None,    # VectorStore instance (episodic)
        expr_engine: ExpressionEngine | None = None,
        expr_context: ExpressionContext | None = None,
    ):
        self._config = config or MemoryConfig()
        self._kv = kv_store
        self._vector = vector_store
        self._expr = expr_engine or ExpressionEngine()
        self._ctx = expr_context or ExpressionContext()

        # Working memory (always available, in-memory)
        self._working: dict[str, Any] = {}

    @property
    def working(self) -> dict[str, Any]:
        """Get the working memory dict."""
        return self._working

    # ─── Working memory (in-memory, per-run) ──────────────────────────

    def set_working(self, key: str, value: Any) -> None:
        self._working[key] = value
        self._enforce_working_max_size()

    def get_working(self, key: str, default: Any = None) -> Any:
        return self._working.get(key, default)

    def clear_working(self) -> None:
        self._working.clear()

    def _enforce_working_max_size(self) -> None:
        """Evict oldest entries if working memory exceeds max_size."""
        max_bytes = _parse_size(self._config.working.max_size)
        if max_bytes <= 0:
            return
        while self._working:
            size = len(json.dumps(self._working, default=str).encode("utf-8"))
            if size <= max_bytes:
                break
            # Evict oldest entry (first key — insertion order in Python 3.7+)
            oldest = next(iter(self._working))
            del self._working[oldest]
            logger.debug("Working memory evicted key '%s' (size %d > max %d)", oldest, size, max_bytes)

    # ─── Conversation memory (SQLite KV, cross-turn) ─────────────────

    async def set_conversation(self, key: str, value: Any, *, ttl: float | None = None) -> None:
        if self._kv is None:
            logger.debug("No KV store configured; conversation memory set is a no-op")
            return
        await self._kv.set(key, value, ttl_seconds=ttl)

    async def get_conversation(self, key: str) -> Any:
        if self._kv is None:
            return None
        return await self._kv.get(key)

    async def get_many_conversation(self, keys: list[str]) -> dict[str, Any]:
        if self._kv is None:
            return {}
        return await self._kv.get_many(keys)

    # ─── Episodic memory (ChromaDB, semantic search) ──────────────────

    async def record_episode(
        self, episode_id: str, text: str, metadata: dict[str, Any] | None = None
    ) -> None:
        if self._vector is None:
            logger.debug("No vector store; episode recording is a no-op")
            return
        await self._vector.add(episode_id, text, metadata)

    async def recall_episodes(self, query: str, top_k: int = 3) -> list[dict[str, Any]]:
        if self._vector is None:
            return []
        entries = await self._vector.search(query, top_k=top_k)
        return [{"id": e.id, "text": e.text, "metadata": e.metadata, "distance": e.distance} for e in entries]

    # ─── Project memory (file-based) ─────────────────────────────────

    async def load_project_memory(self) -> str:
        if not self._config.project:
            return ""
        path_template = self._config.project.path
        path = str(self._expr.resolve(path_template, self._ctx) or path_template)
        p = Path(path)
        if not p.exists():
            return ""
        try:
            text = p.read_text(encoding="utf-8")
            max_lines = self._config.project.max_lines
            lines = text.split("\n")
            if len(lines) > max_lines:
                lines = lines[:max_lines]
            return "\n".join(lines)
        except Exception as e:
            logger.warning("Failed to load project memory from %s: %s", path, e)
            return ""

    async def save_project_memory(self, content: str) -> None:
        if not self._config.project or not self._config.project.agent_writable:
            return
        path_template = self._config.project.path
        path = str(self._expr.resolve(path_template, self._ctx) or path_template)
        p = Path(path)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        except Exception as e:
            logger.warning("Failed to save project memory to %s: %s", path, e)

    # ─── Procedural memory (learned patterns) ──────────────────────────

    async def learn_procedure(
        self,
        procedure_id: str,
        *,
        pattern: str,
        outcome: str,
        success: bool,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Record a learned procedure (from success or failure).

        Stores in KV store with a procedural: prefix. Each procedure
        has a pattern (what was attempted), outcome (what happened),
        and a success flag.
        """
        if self._kv is None:
            return
        cfg = self._config.procedural
        if cfg is None:
            return
        if success and not cfg.learn_from_successes:
            return
        if not success and not cfg.learn_from_failures:
            return

        entry = {
            "id": procedure_id,
            "pattern": pattern,
            "outcome": outcome,
            "success": success,
            "context": context or {},
        }
        key = f"procedural:{procedure_id}"
        await self._kv.set(key, json.dumps(entry, default=str))

        # Also maintain an index of all procedure IDs
        index_key = "procedural:__index__"
        raw = await self._kv.get(index_key)
        ids: list[str] = json.loads(raw) if raw else []
        if procedure_id not in ids:
            ids.append(procedure_id)
            await self._kv.set(index_key, json.dumps(ids))

    async def recall_procedures(
        self, *, success_only: bool = False, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Recall stored procedures."""
        if self._kv is None:
            return []
        if self._config.procedural is None:
            return []

        index_key = "procedural:__index__"
        raw = await self._kv.get(index_key)
        if not raw:
            return []
        ids: list[str] = json.loads(raw) if isinstance(raw, str) else raw

        results: list[dict[str, Any]] = []
        for pid in ids[-limit:]:
            entry_raw = await self._kv.get(f"procedural:{pid}")
            if entry_raw:
                entry = json.loads(entry_raw) if isinstance(entry_raw, str) else entry_raw
                if success_only and not entry.get("success"):
                    continue
                results.append(entry)
        return results

    async def suggest_procedures(self, input_text: str) -> list[dict[str, Any]]:
        """Suggest relevant procedures based on input text.

        Simple keyword matching against stored procedure patterns.
        If a vector store is available and episodic memory is configured,
        uses semantic search; otherwise falls back to substring matching.
        """
        if self._config.procedural is None or not self._config.procedural.auto_suggest:
            return []

        procedures = await self.recall_procedures()
        if not procedures:
            return []

        # Simple keyword matching: check if any word from input appears in pattern
        input_words = set(input_text.lower().split())
        scored: list[tuple[float, dict[str, Any]]] = []
        for proc in procedures:
            pattern_words = set(proc.get("pattern", "").lower().split())
            overlap = len(input_words & pattern_words)
            if overlap > 0:
                score = overlap / max(len(pattern_words), 1)
                scored.append((score, proc))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [proc for _, proc in scored[:5]]

    # ─── Aggregate: build memory context for agent prompt ────────────

    async def build_memory_context(self, input_text: str = "") -> dict[str, Any]:
        """Build a combined memory context dict for injection into the agent.

        Returns a dict with keys for each active memory level.
        """
        context: dict[str, Any] = {}

        # Working memory
        if self._working:
            context["working"] = dict(self._working)

        # Project memory — only auto-inject if configured
        if self._config.project and self._config.project.auto_inject:
            project_text = await self.load_project_memory()
            if project_text:
                context["project"] = project_text

        # Episodic recall (semantic search on input)
        if self._vector and self._config.episodic and self._config.episodic.auto_recall.on_start and input_text:
            recall_cfg = self._config.episodic.auto_recall
            # Use configured query template or fall back to raw input
            query = input_text
            if recall_cfg.query:
                resolved = self._expr.resolve(recall_cfg.query, self._ctx)
                if resolved:
                    query = str(resolved)
            top_k = recall_cfg.limit
            episodes = await self.recall_episodes(query, top_k=top_k)
            # Filter by minimum similarity (distance-based: lower = more similar)
            if episodes and recall_cfg.min_similarity > 0:
                threshold = 1.0 - recall_cfg.min_similarity
                episodes = [e for e in episodes if e.get("distance", 1.0) <= threshold]
            if episodes:
                context["episodic"] = episodes

        # Procedural memory — auto-suggest relevant learned patterns
        if self._config.procedural and self._config.procedural.auto_suggest and input_text:
            suggestions = await self.suggest_procedures(input_text)
            if suggestions:
                context["procedural"] = suggestions

        return context

    def format_for_prompt(self, memory_context: dict[str, Any]) -> str:
        """Format memory context as text for injection into system prompt."""
        parts: list[str] = []

        if "project" in memory_context:
            parts.append(f"## Project Memory\n{memory_context['project']}")

        if "working" in memory_context:
            working = memory_context["working"]
            items = [f"- {k}: {json.dumps(v, default=str)}" for k, v in working.items()]
            parts.append("## Working Memory\n" + "\n".join(items))

        if "episodic" in memory_context:
            episodes = memory_context["episodic"]
            ep_parts = []
            for ep in episodes[:5]:
                ep_parts.append(f"- {ep.get('text', '')[:200]}")
            parts.append("## Relevant Past Episodes\n" + "\n".join(ep_parts))

        if "procedural" in memory_context:
            procedures = memory_context["procedural"]
            proc_parts = []
            for proc in procedures[:5]:
                status = "SUCCESS" if proc.get("success") else "FAILURE"
                proc_parts.append(
                    f"- [{status}] {proc.get('pattern', '')[:150]} → {proc.get('outcome', '')[:150]}"
                )
            parts.append("## Learned Procedures\n" + "\n".join(proc_parts))

        return "\n\n".join(parts)
