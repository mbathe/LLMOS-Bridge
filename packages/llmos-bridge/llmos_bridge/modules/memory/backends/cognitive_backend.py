"""Cognitive persistence backend — objective-driven memory.

Implements the human-like cognitive model where a core objective stays
permanently in "mental RAM" and ALL inputs/actions are automatically
filtered through it.

Architecture (layered by access speed):
  Layer 0 — HOT:  Core objective + active context (dict, 0ms)
  Layer 1 — WARM: Recent decisions + session state (deque, 0ms)
  Layer 2 — COLD: Objective history + learned patterns (KV store, ~1ms)

The key insight: the objective is NEVER forgotten. Every store/recall
automatically tags entries with relevance to the current objective.
When the agent asks "what color for a button?", the cognitive layer
automatically provides "you're building a fitness app — use motivating
colors (green/orange)" without the agent needing to explicitly recall.

Usage in YAML:
    memory:
      cognitive:
        backend: cognitive
        objective: "Build a complete fitness mobile app"
        auto_filter: true
"""

from __future__ import annotations

import json
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from llmos_bridge.modules.memory.backends.base import BaseMemoryBackend, MemoryEntry


@dataclass
class Objective:
    """A persistent objective that filters all cognitive processing."""
    goal: str
    sub_goals: list[str] = field(default_factory=list)
    success_criteria: list[str] = field(default_factory=list)
    progress: float = 0.0  # 0.0 to 1.0
    created_at: float = 0.0
    completed: bool = False
    context_tags: list[str] = field(default_factory=list)  # auto-extracted keywords

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "sub_goals": self.sub_goals,
            "success_criteria": self.success_criteria,
            "progress": self.progress,
            "created_at": self.created_at,
            "completed": self.completed,
            "context_tags": self.context_tags,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Objective:
        return cls(
            goal=d.get("goal", ""),
            sub_goals=d.get("sub_goals", []),
            success_criteria=d.get("success_criteria", []),
            progress=d.get("progress", 0.0),
            created_at=d.get("created_at", 0.0),
            completed=d.get("completed", False),
            context_tags=d.get("context_tags", []),
        )


@dataclass
class Decision:
    """A recorded decision with its objective relevance."""
    action: str
    reasoning: str
    relevance_to_objective: str
    timestamp: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "reasoning": self.reasoning,
            "relevance_to_objective": self.relevance_to_objective,
            "timestamp": self.timestamp,
        }


class CognitiveMemoryBackend(BaseMemoryBackend):
    """Objective-driven cognitive persistence.

    Unlike traditional memory that requires explicit recall, this backend
    keeps the core objective PERMANENTLY accessible and automatically
    enriches every memory operation with objective context.

    Three-layer architecture:
    - HOT (0ms):  core_objective, active_context — always in Python dict
    - WARM (0ms): recent_decisions (deque), session state
    - COLD (~1ms): objective history, learned patterns (optional KV persistence)
    """

    BACKEND_ID = "cognitive"
    DESCRIPTION = "Objective-driven cognitive persistence — keeps goals in permanent RAM, filters all actions through objectives"

    def __init__(
        self,
        *,
        max_decisions: int = 50,
        max_context_items: int = 100,
        persistence_path: Path | None = None,
    ):
        # HOT layer — always in memory
        self._objective: Objective | None = None
        self._active_context: dict[str, Any] = {}

        # WARM layer — recent history
        self._recent_decisions: deque[Decision] = deque(maxlen=max_decisions)
        self._session_state: dict[str, Any] = {}

        # COLD layer — persistence (optional)
        self._persistence_path = persistence_path
        self._objective_history: list[dict[str, Any]] = []

        self._max_context_items = max_context_items

    async def init(self) -> None:
        if self._persistence_path:
            self._persistence_path.parent.mkdir(parents=True, exist_ok=True)
            if self._persistence_path.exists():
                try:
                    data = json.loads(self._persistence_path.read_text(encoding="utf-8"))
                    if data.get("objective"):
                        self._objective = Objective.from_dict(data["objective"])
                    self._active_context = data.get("active_context", {})
                    self._objective_history = data.get("objective_history", [])
                    for d in data.get("recent_decisions", []):
                        self._recent_decisions.append(Decision(**d))
                except Exception:
                    pass

    async def close(self) -> None:
        await self._persist()

    async def _persist(self) -> None:
        """Persist state to disk if path is configured."""
        if not self._persistence_path:
            return
        data = {
            "objective": self._objective.to_dict() if self._objective else None,
            "active_context": self._active_context,
            "objective_history": self._objective_history,
            "recent_decisions": [d.to_dict() for d in self._recent_decisions],
        }
        try:
            self._persistence_path.parent.mkdir(parents=True, exist_ok=True)
            self._persistence_path.write_text(json.dumps(data, default=str), encoding="utf-8")
        except Exception:
            pass

    # ─── Core Objective Management ─────────────────────────────────

    def set_objective(self, goal: str, *, sub_goals: list[str] | None = None, success_criteria: list[str] | None = None) -> Objective:
        """Set the primary objective. This becomes PERMANENT until completed."""
        # Archive previous objective
        if self._objective and not self._objective.completed:
            self._objective_history.append({
                **self._objective.to_dict(),
                "archived_at": time.time(),
                "reason": "replaced",
            })

        self._objective = Objective(
            goal=goal,
            sub_goals=sub_goals or [],
            success_criteria=success_criteria or [],
            created_at=time.time(),
            context_tags=self._extract_tags(goal),
        )
        return self._objective

    def get_objective(self) -> Objective | None:
        """Get the current objective (always 0ms — it's in HOT memory)."""
        return self._objective

    def update_progress(self, progress: float, *, completed_sub_goal: str | None = None) -> None:
        """Update objective progress."""
        if self._objective:
            self._objective.progress = min(1.0, max(0.0, progress))
            if completed_sub_goal and completed_sub_goal in self._objective.sub_goals:
                pass  # Track which sub-goals are done via active_context
            if progress >= 1.0:
                self._objective.completed = True

    def complete_objective(self) -> dict[str, Any]:
        """Mark objective as completed and archive it."""
        if not self._objective:
            return {"error": "No active objective"}
        self._objective.completed = True
        self._objective.progress = 1.0
        result = self._objective.to_dict()
        self._objective_history.append({
            **result,
            "completed_at": time.time(),
        })
        self._objective = None
        return result

    # ─── Decision Recording ────────────────────────────────────────

    def record_decision(self, action: str, reasoning: str, relevance: str = "") -> None:
        """Record a decision with its relevance to the objective."""
        self._recent_decisions.append(Decision(
            action=action,
            reasoning=reasoning,
            relevance_to_objective=relevance or self._auto_relevance(action),
            timestamp=time.time(),
        ))

    # ─── Objective-Aware Context Building ──────────────────────────

    def get_objective_context(self) -> dict[str, Any]:
        """Build the full cognitive context for injection into the LLM.

        This is the key method — it returns everything the agent needs
        to stay objective-aware at ALL times.
        """
        ctx: dict[str, Any] = {}

        # HOT: Always present
        if self._objective:
            ctx["objective"] = {
                "goal": self._objective.goal,
                "sub_goals": self._objective.sub_goals,
                "progress": f"{self._objective.progress * 100:.0f}%",
                "context_tags": self._objective.context_tags,
            }

        # HOT: Active context
        if self._active_context:
            ctx["active_context"] = dict(self._active_context)

        # WARM: Recent decisions
        if self._recent_decisions:
            ctx["recent_decisions"] = [
                {"action": d.action, "relevance": d.relevance_to_objective}
                for d in list(self._recent_decisions)[-5:]
            ]

        return ctx

    def format_for_prompt(self) -> str:
        """Format cognitive context as text for system prompt injection.

        This text is AUTOMATICALLY prepended to every LLM call when
        a cognitive backend is active.
        """
        ctx = self.get_objective_context()
        if not ctx:
            return ""

        parts = ["## Cognitive Context (auto-injected)\n"]

        if "objective" in ctx:
            obj = ctx["objective"]
            parts.append(f"**ACTIVE OBJECTIVE**: {obj['goal']}")
            parts.append(f"Progress: {obj['progress']}")
            if obj.get("sub_goals"):
                parts.append("Sub-goals: " + ", ".join(obj["sub_goals"]))
            parts.append(f"Context tags: {', '.join(obj.get('context_tags', []))}")
            parts.append("")
            parts.append("IMPORTANT: Every response and action MUST serve this objective.")
            parts.append("Relate your answers to the objective context automatically.\n")

        if "active_context" in ctx:
            parts.append("**Active Context:**")
            for k, v in ctx["active_context"].items():
                parts.append(f"- {k}: {v}")
            parts.append("")

        if "recent_decisions" in ctx:
            parts.append("**Recent Decisions:**")
            for d in ctx["recent_decisions"]:
                parts.append(f"- {d['action']} (relevance: {d['relevance']})")

        return "\n".join(parts)

    # ─── BaseMemoryBackend Interface ───────────────────────────────

    async def store(self, key: str, value: Any, *, metadata: dict[str, Any] | None = None, ttl_seconds: float | None = None) -> MemoryEntry:
        """Store in active context with auto objective-tagging."""
        meta = metadata or {}

        # Special keys handled by cognitive layer
        if key == "__objective__":
            obj = self.set_objective(
                str(value),
                sub_goals=meta.get("sub_goals", []),
                success_criteria=meta.get("success_criteria", []),
            )
            return MemoryEntry(key=key, value=obj.to_dict(), metadata=meta, backend=self.BACKEND_ID)

        if key == "__decision__":
            self.record_decision(
                action=str(value),
                reasoning=meta.get("reasoning", ""),
                relevance=meta.get("relevance", ""),
            )
            return MemoryEntry(key=key, value=value, metadata=meta, backend=self.BACKEND_ID)

        if key == "__progress__":
            try:
                progress = float(value)
            except (ValueError, TypeError):
                progress = 0.0
            self.update_progress(progress, completed_sub_goal=meta.get("completed_sub_goal"))
            return MemoryEntry(key=key, value=progress, metadata=meta, backend=self.BACKEND_ID)

        # Regular store: add to active context
        if len(self._active_context) >= self._max_context_items:
            # Evict oldest entry
            oldest = next(iter(self._active_context))
            del self._active_context[oldest]

        self._active_context[key] = value
        await self._persist()
        return MemoryEntry(key=key, value=value, metadata=meta, backend=self.BACKEND_ID)

    async def recall(self, key: str) -> MemoryEntry | None:
        """Recall from cognitive context. __objective__ returns the current objective."""
        if key == "__objective__":
            if self._objective:
                return MemoryEntry(
                    key="__objective__",
                    value=self._objective.to_dict(),
                    metadata={},
                    backend=self.BACKEND_ID,
                )
            return None

        if key == "__context__":
            return MemoryEntry(
                key="__context__",
                value=self.get_objective_context(),
                metadata={},
                backend=self.BACKEND_ID,
            )

        if key in self._active_context:
            return MemoryEntry(key=key, value=self._active_context[key], metadata={}, backend=self.BACKEND_ID)

        # Check session state
        if key in self._session_state:
            return MemoryEntry(key=key, value=self._session_state[key], metadata={}, backend=self.BACKEND_ID)

        return None

    async def delete(self, key: str) -> bool:
        if key in self._active_context:
            del self._active_context[key]
            await self._persist()
            return True
        if key in self._session_state:
            del self._session_state[key]
            return True
        return False

    async def list_keys(self, *, prefix: str | None = None, limit: int = 100) -> list[str]:
        keys = list(self._active_context.keys()) + list(self._session_state.keys())
        special = ["__objective__", "__context__"]
        keys = special + keys
        if prefix:
            keys = [k for k in keys if k.startswith(prefix)]
        return keys[:limit]

    async def search(self, query: str, *, top_k: int = 5, filters: dict[str, Any] | None = None) -> list[MemoryEntry]:
        """Search across all cognitive layers (simple keyword match)."""
        query_lower = query.lower()
        results: list[MemoryEntry] = []

        # Search objective
        if self._objective and query_lower in self._objective.goal.lower():
            results.append(MemoryEntry(
                key="__objective__",
                value=self._objective.to_dict(),
                metadata={},
                score=1.0,
                backend=self.BACKEND_ID,
            ))

        # Search active context
        for k, v in self._active_context.items():
            text = f"{k} {v}".lower()
            if query_lower in text:
                results.append(MemoryEntry(key=k, value=v, metadata={}, score=0.8, backend=self.BACKEND_ID))

        # Search recent decisions
        for d in self._recent_decisions:
            text = f"{d.action} {d.reasoning} {d.relevance_to_objective}".lower()
            if query_lower in text:
                results.append(MemoryEntry(
                    key=f"decision:{d.action}",
                    value=d.to_dict(),
                    metadata={},
                    score=0.6,
                    backend=self.BACKEND_ID,
                ))

        return results[:top_k]

    async def health_check(self) -> dict[str, Any]:
        return {
            "backend": self.BACKEND_ID,
            "status": "ok",
            "has_objective": self._objective is not None,
            "objective": self._objective.goal if self._objective else None,
            "active_context_size": len(self._active_context),
            "decisions_count": len(self._recent_decisions),
        }

    # ─── Private Helpers ───────────────────────────────────────────

    @staticmethod
    def _extract_tags(text: str) -> list[str]:
        """Extract keyword tags from text for fast matching."""
        stop_words = {"a", "an", "the", "is", "are", "was", "were", "be", "been",
                      "being", "have", "has", "had", "do", "does", "did", "will",
                      "would", "could", "should", "may", "might", "shall", "can",
                      "for", "and", "nor", "but", "or", "yet", "so", "in", "on",
                      "at", "to", "of", "with", "by", "from", "as", "into", "un",
                      "une", "le", "la", "les", "de", "du", "des", "et", "ou",
                      "pour", "dans", "avec", "par", "sur", "en", "est", "sont",
                      "qui", "que", "ce", "cette"}
        words = text.lower().split()
        return [w.strip(".,!?;:'\"()") for w in words if len(w) > 2 and w.lower() not in stop_words]

    def _auto_relevance(self, action: str) -> str:
        """Auto-compute relevance of an action to the objective."""
        if not self._objective:
            return "no active objective"
        tags = self._objective.context_tags
        action_lower = action.lower()
        matching = [t for t in tags if t in action_lower]
        if matching:
            return f"directly related ({', '.join(matching)})"
        return "indirect — may support objective"
