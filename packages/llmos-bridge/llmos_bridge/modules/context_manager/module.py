"""Context Manager module — intelligent LLM context window management.

A BaseModule that gives the LLM (and the runtime) the ability to:
- Know how its context budget is allocated (get_budget)
- Compress conversation history via LLM summarization (compress_history)
- Fetch detailed context on demand when things were compressed (fetch_context)
- Get compact tool summaries filtered by application permissions (get_tools_summary)
- Inspect current context window state (get_state)

Design principles:
- OBJECTIVES ARE NEVER FORGOTTEN — cognitive state is always preserved at full fidelity
- Hybrid approach: compress what's compressible, give LLM on-demand fetch for the rest
- Application identity integration: only show tools/modules the app is allowed to use
- Token counting uses tiktoken when available, falls back to heuristic
"""

from __future__ import annotations

import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

from llmos_bridge.modules.base import BaseModule, Platform
from llmos_bridge.modules.manifest import ActionSpec, ModuleManifest, ParamSpec

logger = logging.getLogger(__name__)


# ─── Token Counting ────────────────────────────────────────────────────

_tokenizer: Any = None
_tokenizer_loaded = False


def count_tokens(text: str, model: str = "claude-sonnet-4-20250514") -> int:
    """Count tokens in text. Uses tiktoken if available, else heuristic.

    The heuristic uses 3.5 chars/token (better than the old 4.0 estimate
    for mixed code/prose content).
    """
    global _tokenizer, _tokenizer_loaded
    if not text:
        return 0

    if not _tokenizer_loaded:
        _tokenizer_loaded = True
        try:
            import tiktoken
            # cl100k_base works well for Claude approximation
            _tokenizer = tiktoken.get_encoding("cl100k_base")
        except ImportError:
            logger.debug("tiktoken not installed, using heuristic token counting")

    if _tokenizer is not None:
        return len(_tokenizer.encode(text))
    # Heuristic: ~3.5 chars per token for mixed content
    return max(1, len(text) * 10 // 35)


# ─── Budget Allocation ─────────────────────────────────────────────────

@dataclass
class BudgetAllocation:
    """How the context window is distributed across layers."""
    model_context_window: int      # Total model capacity
    output_reserved: int           # Reserved for generation
    tools_tokens: int              # Tool schemas
    system_prompt_tokens: int      # Base system prompt
    cognitive_tokens: int          # Objectives, progress, decisions
    memory_tokens: int             # KV/vector/file memory
    history_budget: int            # Remaining for conversation history
    history_used: int              # Currently used by history
    total_used: int                # Total tokens currently used
    utilization: float             # 0.0 to 1.0
    compression_needed: bool       # Whether history should be compressed

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_context_window": self.model_context_window,
            "output_reserved": self.output_reserved,
            "budget_breakdown": {
                "tools": self.tools_tokens,
                "system_prompt": self.system_prompt_tokens,
                "cognitive_state": self.cognitive_tokens,
                "memory": self.memory_tokens,
                "conversation_history": {
                    "budget": self.history_budget,
                    "used": self.history_used,
                },
            },
            "total_used": self.total_used,
            "utilization": f"{self.utilization:.1%}",
            "compression_needed": self.compression_needed,
        }


@dataclass
class CompressionRecord:
    """Record of a compression event."""
    timestamp: float
    messages_compressed: int
    tokens_before: int
    tokens_after: int
    summary_text: str


# ─── Configuration ──────────────────────────────────────────────────────

@dataclass
class ContextBudgetConfig:
    """Configuration for context budget allocation."""
    model_context_window: int = 200_000
    output_reserved: int = 8192
    cognitive_max_tokens: int = 1500       # Objectives NEVER truncated below this
    memory_max_tokens: int = 2000
    compression_trigger_ratio: float = 0.75  # Compress when history uses 75% of budget
    summarization_model: str = ""            # Empty = use same model
    min_recent_messages: int = 10            # Always keep last N messages uncompressed


# ─── Module ─────────────────────────────────────────────────────────────

class ContextManagerModule(BaseModule):
    """Intelligent context window management module.

    Provides the LLM with tools to understand and manage its own context window.
    Also provides the runtime with budget computation and compression capabilities.
    """

    MODULE_ID = "context_manager"
    VERSION = "1.0.0"
    SUPPORTED_PLATFORMS = [Platform.ALL]
    MODULE_TYPE = "system"

    def __init__(self) -> None:
        super().__init__()
        self._config = ContextBudgetConfig()
        self._manifests: list[Any] = []           # ModuleManifest list for tool summaries
        self._allowed_modules: list[str] = []     # From Application identity
        self._allowed_actions: dict[str, list[str]] = {}  # From Application identity
        self._compression_history: deque[CompressionRecord] = deque(maxlen=50)
        self._compressed_segments: list[dict[str, Any]] = []  # Full text of compressed segments
        self._summarizer: Callable[..., Awaitable[str]] | None = None
        self._current_system_prompt: str = ""
        self._current_tools_json: str = ""
        self._current_cognitive_text: str = ""
        self._current_memory_text: str = ""
        self._current_history_tokens: int = 0
        self._current_messages: list[dict[str, Any]] = []

    # ─── Lifecycle ────────────────────────────────────────────────

    async def on_start(self) -> None:
        """Pre-load tiktoken encoder so the first token count has zero cold-start."""
        count_tokens("warmup")  # triggers global _tokenizer init (tiktoken or heuristic)

    # ─── Configuration ────────────────────────────────────────────

    def configure(self, config: ContextBudgetConfig) -> None:
        """Set budget configuration."""
        self._config = config

    def set_manifests(self, manifests: list[Any]) -> None:
        """Set available module manifests for tool summary generation."""
        self._manifests = manifests

    def set_application_permissions(
        self,
        allowed_modules: list[str] | None = None,
        allowed_actions: dict[str, list[str]] | None = None,
    ) -> None:
        """Set application-level permissions to filter tools/modules.

        Connects to the Application identity system: only modules and actions
        the application is allowed to use will appear in tool summaries and
        context generation.
        """
        self._allowed_modules = allowed_modules or []
        self._allowed_actions = allowed_actions or {}

    def set_summarizer(self, fn: Callable[..., Awaitable[str]]) -> None:
        """Set the LLM summarization callback.

        The callback signature: async fn(text: str, instruction: str) -> str
        Used for compressing conversation history.
        """
        self._summarizer = fn

    def update_state(
        self,
        *,
        system_prompt: str = "",
        tools_json: str = "",
        cognitive_text: str = "",
        memory_text: str = "",
        messages: list[dict[str, Any]] | None = None,
    ) -> None:
        """Update current context state. Called by the runtime before each LLM call."""
        if system_prompt:
            self._current_system_prompt = system_prompt
        if tools_json:
            self._current_tools_json = tools_json
        if cognitive_text is not None:
            self._current_cognitive_text = cognitive_text
        if memory_text is not None:
            self._current_memory_text = memory_text
        if messages is not None:
            self._current_messages = messages
            self._current_history_tokens = sum(
                count_tokens(m.get("content", "")) for m in messages
            )

    # ─── Budget Computation ───────────────────────────────────────

    def compute_budget(self) -> BudgetAllocation:
        """Compute the current budget allocation."""
        cfg = self._config
        tools_tokens = count_tokens(self._current_tools_json)
        system_tokens = count_tokens(self._current_system_prompt)
        cognitive_tokens = count_tokens(self._current_cognitive_text)
        memory_tokens = count_tokens(self._current_memory_text)

        # Fixed allocations
        fixed = cfg.output_reserved + tools_tokens + system_tokens + cognitive_tokens + memory_tokens

        # History gets whatever remains
        history_budget = max(0, cfg.model_context_window - fixed)
        history_used = self._current_history_tokens
        total_used = fixed + history_used

        utilization = total_used / cfg.model_context_window if cfg.model_context_window > 0 else 0
        compression_needed = (
            history_budget > 0
            and history_used > history_budget * cfg.compression_trigger_ratio
        )

        return BudgetAllocation(
            model_context_window=cfg.model_context_window,
            output_reserved=cfg.output_reserved,
            tools_tokens=tools_tokens,
            system_prompt_tokens=system_tokens,
            cognitive_tokens=cognitive_tokens,
            memory_tokens=memory_tokens,
            history_budget=history_budget,
            history_used=history_used,
            total_used=total_used,
            utilization=utilization,
            compression_needed=compression_needed,
        )

    def bound_cognitive_text(self, text: str) -> str:
        """Bound cognitive text to budget, preserving objectives fully.

        Unlike other context, objectives are NEVER truncated. If the cognitive
        text exceeds the budget, we compress the active_context and recent_decisions
        but ALWAYS keep the full objective.

        Returns the bounded text.
        """
        max_tokens = self._config.cognitive_max_tokens
        tokens = count_tokens(text)
        if tokens <= max_tokens:
            return text

        # Parse sections — objective is ALWAYS kept
        lines = text.split("\n")
        objective_lines: list[str] = []
        other_lines: list[str] = []
        in_objective = False

        for line in lines:
            if "ACTIVE OBJECTIVE" in line or "Cognitive Context" in line:
                in_objective = True
            if in_objective and line.startswith("**Active Context") or line.startswith("**Recent Decisions"):
                in_objective = False

            if in_objective:
                objective_lines.append(line)
            else:
                other_lines.append(line)

        objective_text = "\n".join(objective_lines)
        objective_tokens = count_tokens(objective_text)
        remaining = max_tokens - objective_tokens

        if remaining <= 0:
            # Objective itself exceeds budget — keep it anyway (objectives are NEVER lost)
            return objective_text

        # Truncate other content to fit
        other_text = "\n".join(other_lines)
        if count_tokens(other_text) > remaining:
            # Keep only the most recent items
            chars_limit = remaining * 4  # Approximate
            other_text = other_text[:chars_limit] + "\n[... context truncated, use context_manager.fetch_context to retrieve details ...]"

        return objective_text + "\n" + other_text

    def get_compact_tools_summary(self) -> str:
        """Generate a compact tool summary, respecting application permissions.

        Instead of full JSON schemas, produces a concise markdown listing
        that uses far fewer tokens while giving the LLM enough info to call tools.
        """
        if not self._manifests:
            return "No modules available."

        lines: list[str] = []
        for manifest in self._manifests:
            # Filter by application permissions
            if self._allowed_modules and manifest.module_id not in self._allowed_modules:
                continue

            allowed_action_names = self._allowed_actions.get(manifest.module_id, [])

            action_lines: list[str] = []
            for action in manifest.actions:
                # Filter by allowed actions if specified
                if allowed_action_names and action.name not in allowed_action_names:
                    continue

                params_str = ""
                if action.params:
                    param_parts = []
                    for p in action.params:
                        req = "*" if p.required else ""
                        desc = p.description[:40] if p.description else ""
                        param_parts.append(f"{p.name}{req}:{p.type}")
                    params_str = f"({', '.join(param_parts)})"
                action_lines.append(f"  - {action.name}{params_str} — {action.description[:60]}")

            if action_lines:
                lines.append(f"### {manifest.module_id}")
                lines.extend(action_lines)

        return "\n".join(lines) if lines else "No permitted modules."

    # ─── Compression ──────────────────────────────────────────────

    async def compress_messages(
        self,
        messages: list[dict[str, Any]],
        keep_last_n: int | None = None,
    ) -> tuple[list[dict[str, Any]], str]:
        """Compress older messages into a summary, keeping recent ones intact.

        Returns (new_messages, summary_text).
        The summary is stored for later retrieval via fetch_context.
        """
        keep_n = keep_last_n or self._config.min_recent_messages
        if len(messages) <= keep_n:
            return messages, ""

        to_compress = messages[:-keep_n]
        to_keep = messages[-keep_n:]

        # Build text representation of messages to compress
        text_parts: list[str] = []
        for msg in to_compress:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if role == "tool":
                name = msg.get("name", "tool")
                text_parts.append(f"[Tool {name}]: {content[:300]}")
            elif role == "assistant":
                text_parts.append(f"[Assistant]: {content[:500]}")
                if msg.get("tool_calls"):
                    for tc in msg["tool_calls"]:
                        fn = tc.get("function", {})
                        text_parts.append(f"  -> Called {fn.get('name', '?')}")
            elif role == "user":
                text_parts.append(f"[User]: {content[:300]}")

        full_text = "\n".join(text_parts)
        tokens_before = count_tokens(full_text)

        # Summarize using LLM if available
        if self._summarizer:
            try:
                summary = await self._summarizer(
                    full_text,
                    "Summarize this conversation segment concisely. Preserve: "
                    "key decisions made, important facts discovered, tool results, "
                    "and any unresolved questions. Be factual and specific.",
                )
            except Exception as e:
                logger.warning("LLM summarization failed, using extractive fallback: %s", e)
                summary = self._extractive_summary(to_compress)
        else:
            summary = self._extractive_summary(to_compress)

        tokens_after = count_tokens(summary)

        # Store compression record
        record = CompressionRecord(
            timestamp=time.time(),
            messages_compressed=len(to_compress),
            tokens_before=tokens_before,
            tokens_after=tokens_after,
            summary_text=summary,
        )
        self._compression_history.append(record)

        # Store full compressed segment for on-demand retrieval
        self._compressed_segments.append({
            "timestamp": time.time(),
            "message_count": len(to_compress),
            "full_text": full_text,
            "summary": summary,
        })

        # Build new message list with summary
        summary_msg = {
            "role": "user",
            "content": (
                f"[Previous conversation summary — {len(to_compress)} messages compressed]\n\n"
                f"{summary}\n\n"
                "[Use context_manager.fetch_context to retrieve full details if needed]"
            ),
        }

        return [summary_msg] + to_keep, summary

    def _extractive_summary(self, messages: list[dict[str, Any]]) -> str:
        """Fallback summary when LLM is not available."""
        parts: list[str] = []
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "user":
                parts.append(f"- User asked: {content[:100]}")
            elif role == "assistant" and content:
                parts.append(f"- Assistant: {content[:100]}")
            elif role == "tool":
                name = msg.get("name", "tool")
                parts.append(f"- Tool {name} was called")
        return "Previous conversation:\n" + "\n".join(parts[-20:])

    # ─── Manifest ──────────────────────────────────────────────────

    def get_manifest(self) -> ModuleManifest:
        return ModuleManifest(
            module_id=self.MODULE_ID,
            version=self.VERSION,
            description=(
                "Intelligent LLM context window management. Computes token budgets, "
                "compresses conversation history via LLM summarization, provides "
                "on-demand context fetching. The LLM can use these tools to manage "
                "its own context and retrieve information that was compressed."
            ),
            platforms=["all"],
            tags=["context", "token-management", "summarization", "budget"],
            declared_permissions=["context.read", "context.write"],
            actions=[
                ActionSpec(
                    name="get_budget",
                    description=(
                        "Get the current context budget allocation: how tokens are "
                        "distributed across system prompt, cognitive state, memory, "
                        "conversation history, and tools."
                    ),
                    params=[],
                    returns="object",
                    returns_description='{"model_context_window": int, "budget_breakdown": {...}, "utilization": str, "compression_needed": bool}',
                ),
                ActionSpec(
                    name="compress_history",
                    description=(
                        "Compress conversation history by summarizing older messages. "
                        "Keeps the most recent messages intact. Use this when context "
                        "is getting large (check get_budget first)."
                    ),
                    params=[
                        ParamSpec("keep_last_n", "integer", "Number of recent messages to keep uncompressed (default: 10)", required=False),
                    ],
                    returns="object",
                    returns_description='{"compressed": int, "summary": str, "tokens_saved": int}',
                ),
                ActionSpec(
                    name="fetch_context",
                    description=(
                        "Fetch detailed context from compressed conversation segments. "
                        "When older messages were compressed, use this to retrieve the "
                        "full details about a specific topic or decision."
                    ),
                    params=[
                        ParamSpec("query", "string", "What to look for in compressed history", required=True),
                        ParamSpec("segment_index", "integer", "Specific compression segment to retrieve (0 = most recent)", required=False),
                    ],
                    returns="object",
                    returns_description='{"found": bool, "content": str, "segment_count": int}',
                ),
                ActionSpec(
                    name="get_tools_summary",
                    description=(
                        "Get a compact summary of all available tools/actions. "
                        "Filtered by application permissions. More compact than "
                        "full tool schemas — use when you need to check available capabilities."
                    ),
                    params=[
                        ParamSpec("module_filter", "string", "Only show tools from this module", required=False),
                    ],
                    returns="object",
                    returns_description='{"summary": str, "module_count": int, "action_count": int}',
                ),
                ActionSpec(
                    name="get_state",
                    description=(
                        "Get the current context window state: token usage, "
                        "budget utilization, compression history."
                    ),
                    params=[],
                    returns="object",
                    returns_description='{"budget": {...}, "compressions": int, "total_tokens_saved": int}',
                ),
            ],
        )

    # ─── Action Implementations ────────────────────────────────────

    async def _action_get_budget(self, params: dict[str, Any]) -> dict[str, Any]:
        budget = self.compute_budget()
        return budget.to_dict()

    async def _action_compress_history(self, params: dict[str, Any]) -> dict[str, Any]:
        keep_n = params.get("keep_last_n")
        if keep_n is not None:
            keep_n = int(keep_n)

        if not self._current_messages:
            return {"compressed": 0, "summary": "", "tokens_saved": 0, "message": "No messages to compress"}

        tokens_before = self._current_history_tokens
        new_messages, summary = await self.compress_messages(
            self._current_messages, keep_last_n=keep_n,
        )
        self._current_messages = new_messages
        self._current_history_tokens = sum(
            count_tokens(m.get("content", "")) for m in new_messages
        )
        tokens_after = self._current_history_tokens

        return {
            "compressed": tokens_before > tokens_after,
            "messages_before": len(self._current_messages) + (tokens_before - tokens_after),
            "messages_after": len(new_messages),
            "tokens_saved": tokens_before - tokens_after,
            "summary": summary[:500] if summary else "",
        }

    async def _action_fetch_context(self, params: dict[str, Any]) -> dict[str, Any]:
        query = params.get("query", "")
        segment_index = params.get("segment_index")

        if not self._compressed_segments:
            return {
                "found": False,
                "content": "No compressed segments available. History has not been compressed yet.",
                "segment_count": 0,
            }

        # If specific segment requested
        if segment_index is not None:
            idx = int(segment_index)
            # 0 = most recent, so reverse
            segments = list(reversed(self._compressed_segments))
            if 0 <= idx < len(segments):
                seg = segments[idx]
                return {
                    "found": True,
                    "content": seg["full_text"],
                    "summary": seg["summary"],
                    "message_count": seg["message_count"],
                    "segment_count": len(self._compressed_segments),
                }
            return {
                "found": False,
                "content": f"Segment {idx} not found. Available: 0-{len(segments)-1}",
                "segment_count": len(self._compressed_segments),
            }

        # Search all segments for the query
        if not query:
            # Return summaries of all segments
            summaries = []
            for i, seg in enumerate(reversed(self._compressed_segments)):
                summaries.append(f"[Segment {i}] ({seg['message_count']} messages): {seg['summary'][:200]}")
            return {
                "found": True,
                "content": "\n\n".join(summaries),
                "segment_count": len(self._compressed_segments),
            }

        # Search for query in full text of segments
        query_lower = query.lower()
        matches: list[str] = []
        for seg in reversed(self._compressed_segments):
            if query_lower in seg["full_text"].lower():
                # Extract relevant lines
                relevant = [
                    line for line in seg["full_text"].split("\n")
                    if query_lower in line.lower()
                ]
                matches.extend(relevant[:10])

        if matches:
            return {
                "found": True,
                "content": "\n".join(matches),
                "segment_count": len(self._compressed_segments),
            }

        return {
            "found": False,
            "content": f"No matches for '{query}' in compressed history.",
            "segment_count": len(self._compressed_segments),
        }

    async def _action_get_tools_summary(self, params: dict[str, Any]) -> dict[str, Any]:
        module_filter = params.get("module_filter")

        if module_filter:
            # Temporarily filter manifests
            filtered = [m for m in self._manifests if m.module_id == module_filter]
            old = self._manifests
            self._manifests = filtered
            summary = self.get_compact_tools_summary()
            self._manifests = old
        else:
            summary = self.get_compact_tools_summary()

        # Count modules and actions in the summary
        module_count = summary.count("###")
        action_count = summary.count("  - ")

        return {
            "summary": summary,
            "module_count": module_count,
            "action_count": action_count,
        }

    async def _action_get_state(self, params: dict[str, Any]) -> dict[str, Any]:
        budget = self.compute_budget()
        total_saved = sum(r.tokens_before - r.tokens_after for r in self._compression_history)

        compressions = [
            {
                "timestamp": r.timestamp,
                "messages_compressed": r.messages_compressed,
                "tokens_before": r.tokens_before,
                "tokens_after": r.tokens_after,
                "ratio": f"{r.tokens_after / r.tokens_before:.1%}" if r.tokens_before > 0 else "0%",
            }
            for r in list(self._compression_history)[-5:]  # Last 5
        ]

        return {
            "budget": budget.to_dict(),
            "compressions_total": len(self._compression_history),
            "compressions_recent": compressions,
            "total_tokens_saved": total_saved,
            "compressed_segments_available": len(self._compressed_segments),
        }

    # ─── Introspection ─────────────────────────────────────────────

    async def health_check(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "module_id": self.MODULE_ID,
            "version": self.VERSION,
            "compressions_total": len(self._compression_history),
            "has_summarizer": self._summarizer is not None,
        }

    def metrics(self) -> dict[str, Any]:
        return {
            "compressions_total": len(self._compression_history),
            "compressed_segments": len(self._compressed_segments),
            "has_summarizer": self._summarizer is not None,
            "manifests_loaded": len(self._manifests),
        }
