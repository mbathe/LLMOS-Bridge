"""Memory module — pluggable multi-backend memory system.

A proper BaseModule that routes memory operations to pluggable backends.
Built-in backends: kv, vector, file, cognitive.
Users can register custom backends at runtime via register_backend().

Actions:
  - store:          Store a value in a specific backend
  - recall:         Recall a value by key from a backend
  - search:         Semantic/fuzzy search across backends
  - delete:         Delete a key from a backend
  - list_keys:      List keys in a backend
  - clear:          Clear all entries in a backend
  - list_backends:  List registered backends and their status
  - set_objective:  Set a cognitive objective (cognitive backend)
  - get_context:    Get the current cognitive context (cognitive backend)
  - update_progress: Update objective progress (cognitive backend)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from llmos_bridge.modules.base import BaseModule, Platform
from llmos_bridge.modules.manifest import ActionSpec, ModuleManifest, ParamSpec
from llmos_bridge.modules.memory.backends.base import BaseMemoryBackend

logger = logging.getLogger(__name__)


class MemoryModule(BaseModule):
    """Multi-backend memory module.

    Dispatches memory operations to pluggable backends. Each backend
    implements BaseMemoryBackend and is identified by its BACKEND_ID.
    """

    MODULE_ID = "memory"
    VERSION = "1.0.0"
    SUPPORTED_PLATFORMS = [Platform.ALL]
    MODULE_TYPE = "system"

    def __init__(self) -> None:
        super().__init__()
        self._backends: dict[str, BaseMemoryBackend] = {}
        self._default_backend: str = "kv"

    # ─── Backend Management ────────────────────────────────────────

    def register_backend(self, backend: BaseMemoryBackend) -> None:
        """Register a memory backend. Can be called at any time."""
        self._backends[backend.BACKEND_ID] = backend
        logger.info("Memory backend registered: %s", backend.BACKEND_ID)

    def unregister_backend(self, backend_id: str) -> None:
        """Remove a backend."""
        self._backends.pop(backend_id, None)

    def get_backend(self, backend_id: str | None = None) -> BaseMemoryBackend | None:
        """Get a backend by ID, or the default."""
        bid = backend_id or self._default_backend
        return self._backends.get(bid)

    def set_default_backend(self, backend_id: str) -> None:
        """Set which backend is used when none is specified."""
        if backend_id in self._backends:
            self._default_backend = backend_id

    @property
    def backends(self) -> dict[str, BaseMemoryBackend]:
        return self._backends

    # ─── Lifecycle ─────────────────────────────────────────────────

    async def on_start(self) -> None:
        for bid, backend in self._backends.items():
            try:
                await backend.init()
                logger.info("Memory backend initialized: %s", bid)
            except Exception as e:
                logger.warning("Failed to initialize memory backend %s: %s", bid, e)

    async def on_stop(self) -> None:
        for bid, backend in self._backends.items():
            try:
                await backend.close()
            except Exception as e:
                logger.warning("Failed to close memory backend %s: %s", bid, e)

    # ─── Manifest ──────────────────────────────────────────────────

    def get_manifest(self) -> ModuleManifest:
        backend_ids = list(self._backends.keys()) if self._backends else ["kv", "vector", "file", "cognitive"]
        return ModuleManifest(
            module_id=self.MODULE_ID,
            version=self.VERSION,
            description=(
                "Multi-backend memory system. Store, recall, and search across "
                "pluggable backends: kv (fast persistent), vector (semantic search), "
                "file (markdown), cognitive (objective-driven). "
                "Users can register custom backends."
            ),
            platforms=["all"],
            tags=["memory", "storage", "knowledge", "cognitive", "persistence"],
            declared_permissions=["data.memory.read", "data.memory.write"],
            actions=[
                ActionSpec(
                    name="store",
                    description="Store a key-value pair in a memory backend",
                    params=[
                        ParamSpec("key", "string", "Key to store under", required=True),
                        ParamSpec("value", "string", "Value to store", required=True),
                        ParamSpec("backend", "string", f"Backend to use (default: {self._default_backend})", required=False, enum=backend_ids),
                        ParamSpec("metadata", "object", "Optional metadata dict", required=False),
                        ParamSpec("ttl_seconds", "number", "Time-to-live in seconds (0 = forever)", required=False),
                    ],
                    returns="object",
                    returns_description='{"key": str, "value": any, "backend": str, "stored": true}',
                ),
                ActionSpec(
                    name="recall",
                    description="Recall a value by key from a memory backend",
                    params=[
                        ParamSpec("key", "string", "Key to recall", required=True),
                        ParamSpec("backend", "string", "Backend to query", required=False, enum=backend_ids),
                    ],
                    returns="object",
                    returns_description='{"key": str, "value": any, "found": bool, "backend": str}',
                ),
                ActionSpec(
                    name="search",
                    description="Semantic or fuzzy search across one or all backends",
                    params=[
                        ParamSpec("query", "string", "Search query", required=True),
                        ParamSpec("backend", "string", "Backend to search (omit for all)", required=False, enum=backend_ids),
                        ParamSpec("top_k", "integer", "Max results to return (default: 5)", required=False),
                    ],
                    returns="object",
                    returns_description='{"results": [{"key": str, "value": any, "score": float, "backend": str}], "count": int}',
                ),
                ActionSpec(
                    name="delete",
                    description="Delete a key from a memory backend",
                    params=[
                        ParamSpec("key", "string", "Key to delete", required=True),
                        ParamSpec("backend", "string", "Backend to delete from", required=False, enum=backend_ids),
                    ],
                    returns="object",
                    returns_description='{"deleted": bool, "key": str}',
                ),
                ActionSpec(
                    name="list_keys",
                    description="List stored keys in a backend",
                    params=[
                        ParamSpec("backend", "string", "Backend to list keys from", required=False, enum=backend_ids),
                        ParamSpec("prefix", "string", "Filter by key prefix", required=False),
                        ParamSpec("limit", "integer", "Max keys to return (default: 100)", required=False),
                    ],
                    returns="object",
                    returns_description='{"keys": [str], "count": int, "backend": str}',
                ),
                ActionSpec(
                    name="clear",
                    description="Clear all entries from a backend",
                    params=[
                        ParamSpec("backend", "string", "Backend to clear", required=True, enum=backend_ids),
                    ],
                    returns="object",
                    returns_description='{"cleared": int, "backend": str}',
                ),
                ActionSpec(
                    name="list_backends",
                    description="List all registered memory backends and their status",
                    params=[],
                    returns="object",
                    returns_description='{"backends": [{"id": str, "description": str, "supports_search": bool}]}',
                ),
                ActionSpec(
                    name="set_objective",
                    description="Set a cognitive objective that filters all subsequent actions. The objective stays in permanent memory until completed.",
                    params=[
                        ParamSpec("goal", "string", "The primary objective/goal", required=True),
                        ParamSpec("sub_goals", "array", "List of sub-goals to track", required=False),
                        ParamSpec("success_criteria", "array", "Criteria for completion", required=False),
                    ],
                    returns="object",
                    returns_description='{"objective": {"goal": str, "sub_goals": [], "progress": float}}',
                ),
                ActionSpec(
                    name="get_context",
                    description="Get the current cognitive context (objective + active state + recent decisions). Auto-injected into prompts when cognitive backend is active.",
                    params=[],
                    returns="object",
                    returns_description='{"objective": {...}, "active_context": {...}, "recent_decisions": [...]}',
                ),
                ActionSpec(
                    name="update_progress",
                    description="Update the progress of the current cognitive objective",
                    params=[
                        ParamSpec("progress", "number", "Progress from 0.0 to 1.0", required=True),
                        ParamSpec("completed_sub_goal", "string", "Name of sub-goal just completed", required=False),
                        ParamSpec("complete", "boolean", "Mark objective as fully completed", required=False),
                    ],
                    returns="object",
                    returns_description='{"progress": float, "completed": bool}',
                ),
                ActionSpec(
                    name="observe",
                    description=(
                        "Get a real-time snapshot of ALL memory state across ALL backends. "
                        "Returns a human-readable summary — the LLM knows everything without "
                        "looking up specific keys. Use this at the start of a conversation or "
                        "whenever you need to understand the full state."
                    ),
                    params=[],
                    returns="object",
                    returns_description=(
                        '{"cognitive": {...}, "backends": {...}, "summary": "human-readable text"}'
                    ),
                ),
            ],
        )

    # ─── Action Implementations ────────────────────────────────────

    async def _action_store(self, params: dict[str, Any]) -> dict[str, Any]:
        key = params.get("key", "")
        value = params.get("value", "")
        backend_id = params.get("backend", self._default_backend)
        metadata = params.get("metadata")
        ttl = params.get("ttl_seconds")

        if not key:
            return {"error": "key is required"}

        backend = self.get_backend(backend_id)
        if not backend:
            return {"error": f"Backend '{backend_id}' not registered. Available: {list(self._backends.keys())}"}

        entry = await backend.store(key, value, metadata=metadata, ttl_seconds=ttl)
        return {"stored": True, "key": entry.key, "value": entry.value, "backend": backend_id}

    async def _action_recall(self, params: dict[str, Any]) -> dict[str, Any]:
        key = params.get("key", "")
        backend_id = params.get("backend", self._default_backend)

        if not key:
            return {"error": "key is required"}

        backend = self.get_backend(backend_id)
        if not backend:
            return {"error": f"Backend '{backend_id}' not registered"}

        entry = await backend.recall(key)
        if entry is None:
            return {"found": False, "key": key, "backend": backend_id}
        return {"found": True, "key": entry.key, "value": entry.value, "metadata": entry.metadata, "backend": backend_id}

    async def _action_search(self, params: dict[str, Any]) -> dict[str, Any]:
        query = params.get("query", "")
        backend_id = params.get("backend")
        top_k = int(params.get("top_k", 5))

        if not query:
            return {"error": "query is required"}

        all_results = []
        if backend_id:
            backend = self.get_backend(backend_id)
            if not backend:
                return {"error": f"Backend '{backend_id}' not registered"}
            results = await backend.search(query, top_k=top_k)
            all_results.extend(results)
        else:
            # Search all backends
            for bid, backend in self._backends.items():
                try:
                    results = await backend.search(query, top_k=top_k)
                    all_results.extend(results)
                except Exception as e:
                    logger.debug("Search failed on backend %s: %s", bid, e)

        # Sort by score, highest first
        all_results.sort(key=lambda x: x.score or 0, reverse=True)
        all_results = all_results[:top_k]

        return {
            "results": [
                {"key": r.key, "value": r.value, "score": r.score, "backend": r.backend, "metadata": r.metadata}
                for r in all_results
            ],
            "count": len(all_results),
        }

    async def _action_delete(self, params: dict[str, Any]) -> dict[str, Any]:
        key = params.get("key", "")
        backend_id = params.get("backend", self._default_backend)

        if not key:
            return {"error": "key is required"}

        backend = self.get_backend(backend_id)
        if not backend:
            return {"error": f"Backend '{backend_id}' not registered"}

        deleted = await backend.delete(key)
        return {"deleted": deleted, "key": key, "backend": backend_id}

    async def _action_list_keys(self, params: dict[str, Any]) -> dict[str, Any]:
        backend_id = params.get("backend", self._default_backend)
        prefix = params.get("prefix")
        limit = int(params.get("limit", 100))

        backend = self.get_backend(backend_id)
        if not backend:
            return {"error": f"Backend '{backend_id}' not registered"}

        keys = await backend.list_keys(prefix=prefix, limit=limit)
        return {"keys": keys, "count": len(keys), "backend": backend_id}

    async def _action_clear(self, params: dict[str, Any]) -> dict[str, Any]:
        backend_id = params.get("backend", "")
        if not backend_id:
            return {"error": "backend is required"}

        backend = self.get_backend(backend_id)
        if not backend:
            return {"error": f"Backend '{backend_id}' not registered"}

        count = await backend.clear()
        return {"cleared": count, "backend": backend_id}

    async def _action_list_backends(self, params: dict[str, Any]) -> dict[str, Any]:
        backends = []
        for bid, backend in self._backends.items():
            info = backend.info()
            health = await backend.health_check()
            info.update(health)
            backends.append(info)
        return {"backends": backends, "count": len(backends), "default": self._default_backend}

    async def _action_set_objective(self, params: dict[str, Any]) -> dict[str, Any]:
        goal = params.get("goal", "")
        if not goal:
            return {"error": "goal is required"}

        cognitive = self._get_cognitive_backend()
        if not cognitive:
            return {"error": "Cognitive backend not registered. Add a 'cognitive' backend to use objectives."}

        obj = cognitive.set_objective(
            goal,
            sub_goals=params.get("sub_goals", []),
            success_criteria=params.get("success_criteria", []),
        )
        return {"objective": obj.to_dict()}

    async def _action_get_context(self, params: dict[str, Any]) -> dict[str, Any]:
        cognitive = self._get_cognitive_backend()
        if not cognitive:
            return {"error": "Cognitive backend not registered"}

        return cognitive.get_objective_context()

    async def _action_update_progress(self, params: dict[str, Any]) -> dict[str, Any]:
        cognitive = self._get_cognitive_backend()
        if not cognitive:
            return {"error": "Cognitive backend not registered"}

        progress = float(params.get("progress", 0))
        completed_sub_goal = params.get("completed_sub_goal")
        complete = params.get("complete", False)

        if complete:
            return cognitive.complete_objective()

        cognitive.update_progress(progress, completed_sub_goal=completed_sub_goal)
        obj = cognitive.get_objective()
        return {
            "progress": obj.progress if obj else 0,
            "completed": obj.completed if obj else False,
            "objective": obj.to_dict() if obj else None,
        }

    async def _action_observe(self, params: dict[str, Any]) -> dict[str, Any]:
        """Real-time snapshot of ALL memory state — the LLM gets full awareness.

        This is the key action for real-time state awareness. Instead of
        requiring the LLM to recall specific keys, observe() dumps everything
        it needs in one shot: objectives, stored facts, recent decisions,
        backend contents.
        """
        result: dict[str, Any] = {}
        summary_parts: list[str] = []

        # Cognitive state — always first, most important
        cognitive = self._get_cognitive_backend()
        if cognitive:
            obj = cognitive.get_objective()
            ctx = cognitive.get_objective_context()
            cog_info: dict[str, Any] = {
                "has_objective": obj is not None,
            }
            if obj:
                cog_info["goal"] = obj.goal
                cog_info["progress"] = f"{obj.progress * 100:.0f}%"
                cog_info["sub_goals"] = obj.sub_goals
                cog_info["completed"] = obj.completed
                cog_info["context_tags"] = obj.context_tags
                summary_parts.append(f"Active objective: {obj.goal} ({cog_info['progress']})")
            else:
                summary_parts.append("No active objective")

            if ctx.get("active_context"):
                cog_info["active_context"] = ctx["active_context"]
                summary_parts.append(f"{len(ctx['active_context'])} active context entries")

            if ctx.get("recent_decisions"):
                cog_info["recent_decisions"] = ctx["recent_decisions"]
                summary_parts.append(f"{len(ctx['recent_decisions'])} recent decisions")

            result["cognitive"] = cog_info

        # All backends — key counts and sample data
        backend_info: dict[str, Any] = {}
        for bid, backend in self._backends.items():
            if bid == "cognitive":
                continue  # Already covered above
            try:
                keys = await backend.list_keys(limit=20)
                info: dict[str, Any] = {
                    "key_count": len(keys),
                    "sample_keys": keys[:10],
                }
                # For small backends, include all values for full awareness
                if len(keys) <= 10:
                    entries: dict[str, Any] = {}
                    for k in keys:
                        entry = await backend.recall(k)
                        if entry:
                            val = entry.value
                            # Truncate long values
                            if isinstance(val, str) and len(val) > 200:
                                val = val[:200] + "..."
                            entries[k] = val
                    info["contents"] = entries
                backend_info[bid] = info
                summary_parts.append(f"{bid}: {len(keys)} entries")
            except Exception as e:
                backend_info[bid] = {"error": str(e)}

        result["backends"] = backend_info
        result["summary"] = ". ".join(summary_parts) + "."
        return result

    # ─── Introspection ─────────────────────────────────────────────

    async def health_check(self) -> dict[str, Any]:
        backend_health = {}
        for bid, backend in self._backends.items():
            try:
                backend_health[bid] = await backend.health_check()
            except Exception as e:
                backend_health[bid] = {"status": "error", "error": str(e)}

        return {
            "status": "ok",
            "module_id": self.MODULE_ID,
            "version": self.VERSION,
            "backends": backend_health,
            "default_backend": self._default_backend,
        }

    def metrics(self) -> dict[str, Any]:
        return {
            "backends_registered": len(self._backends),
            "backend_ids": list(self._backends.keys()),
            "default_backend": self._default_backend,
        }

    # ─── Helpers ───────────────────────────────────────────────────

    def _get_cognitive_backend(self) -> Any:
        """Get the cognitive backend if registered."""
        from llmos_bridge.modules.memory.backends.cognitive_backend import CognitiveMemoryBackend
        backend = self._backends.get("cognitive")
        if isinstance(backend, CognitiveMemoryBackend):
            return backend
        return None

    def get_cognitive_prompt(self) -> str:
        """Get cognitive context formatted for prompt injection.

        Called by the app runtime to auto-inject objective context.
        Returns empty string if no cognitive backend or no objective.
        """
        cognitive = self._get_cognitive_backend()
        if not cognitive:
            return ""
        return cognitive.format_for_prompt()
