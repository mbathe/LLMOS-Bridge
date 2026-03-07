"""Built-in tools for the LLMOS App Language.

These tools are available to any app without importing a module:
- ask_user: Prompt the user for input
- todo: Task tracking (persistent via KV store when available)
- delegate: Delegate to another agent (multi-agent only)
- emit: Publish an event to the bus
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

logger = logging.getLogger(__name__)

_TODO_KV_KEY = "llmos:builtins:todos"


@dataclass
class TodoItem:
    """A tracked task."""
    id: str
    task: str
    status: str = "pending"        # pending | in_progress | completed

    def to_dict(self) -> dict[str, str]:
        return {"id": self.id, "task": self.task, "status": self.status}

    @classmethod
    def from_dict(cls, d: dict[str, str]) -> TodoItem:
        return cls(id=d["id"], task=d["task"], status=d.get("status", "pending"))


class BuiltinToolExecutor:
    """Executes built-in tools that don't map to module actions."""

    def __init__(
        self,
        *,
        input_handler: Callable[[str], Awaitable[str]] | None = None,
        output_handler: Callable[[str], Awaitable[None]] | None = None,
        delegate_handler: Callable[[str, str], Awaitable[Any]] | None = None,
        emit_handler: Callable[[str, dict], Awaitable[None]] | None = None,
        send_message_handler: Callable[[str, str], Awaitable[Any]] | None = None,
        kv_store: Any = None,
    ):
        self._input_handler = input_handler
        self._output_handler = output_handler
        self._delegate_handler = delegate_handler
        self._emit_handler = emit_handler
        self._send_message_handler = send_message_handler
        self._kv_store = kv_store
        self._todos: list[TodoItem] = []
        self._todos_loaded = False

    async def execute(self, tool_name: str, params: dict[str, Any]) -> Any:
        """Execute a built-in tool by name."""
        handler = _HANDLERS.get(tool_name)
        if handler is None:
            return {"error": f"Unknown built-in tool: {tool_name}"}
        return await handler(self, params)

    def is_builtin(self, tool_name: str) -> bool:
        """Check if a tool name is a built-in."""
        return tool_name in _HANDLERS

    # ─── Tool implementations ──────────────────────────────────────────

    async def _ask_user(self, params: dict[str, Any]) -> dict[str, Any]:
        """Ask the user a question and return their response."""
        question = params.get("question", "")
        if self._input_handler:
            response = await self._input_handler(question)
            return {"response": response}
        return {"response": "", "note": "No input handler configured"}

    async def _load_todos(self) -> None:
        """Load todos from KV store on first access."""
        if self._todos_loaded:
            return
        self._todos_loaded = True
        if self._kv_store is None:
            return
        try:
            raw = await self._kv_store.get(_TODO_KV_KEY)
            if raw:
                items = json.loads(raw) if isinstance(raw, str) else raw
                self._todos = [TodoItem.from_dict(d) for d in items]
        except Exception as e:
            logger.debug("Could not load todos from KV: %s", e)

    async def _save_todos(self) -> None:
        """Persist todos to KV store if available."""
        if self._kv_store is None:
            return
        try:
            data = json.dumps([t.to_dict() for t in self._todos])
            await self._kv_store.set(_TODO_KV_KEY, data)
        except Exception as e:
            logger.debug("Could not save todos to KV: %s", e)

    async def _todo(self, params: dict[str, Any]) -> dict[str, Any]:
        """Manage the task list (persistent via KV store when available)."""
        await self._load_todos()
        action = params.get("action", "list")

        if action == "add":
            task = params.get("task", "")
            if not task:
                return {"error": "Task description required"}
            item = TodoItem(id=str(uuid.uuid4())[:8], task=task)
            self._todos.append(item)
            await self._save_todos()
            return {"id": item.id, "task": item.task, "status": item.status}

        elif action == "update":
            task_id = params.get("task_id", "")
            for item in self._todos:
                if item.id == task_id:
                    if "task" in params:
                        item.task = params["task"]
                    if "status" in params:
                        item.status = params["status"]
                    await self._save_todos()
                    return {"id": item.id, "task": item.task, "status": item.status}
            return {"error": f"Task not found: {task_id}"}

        elif action == "complete":
            task_id = params.get("task_id", "")
            for item in self._todos:
                if item.id == task_id:
                    item.status = "completed"
                    await self._save_todos()
                    return {"id": item.id, "task": item.task, "status": "completed"}
            return {"error": f"Task not found: {task_id}"}

        elif action == "remove":
            task_id = params.get("task_id", "")
            for i, item in enumerate(self._todos):
                if item.id == task_id:
                    self._todos.pop(i)
                    await self._save_todos()
                    return {"removed": True, "task_id": task_id}
            return {"error": f"Task not found: {task_id}"}

        elif action == "clear_completed":
            before = len(self._todos)
            self._todos = [t for t in self._todos if t.status != "completed"]
            removed = before - len(self._todos)
            await self._save_todos()
            return {"cleared": removed, "remaining": len(self._todos)}

        elif action == "list":
            status_filter = params.get("status_filter", "all")
            tasks = self._todos
            if status_filter != "all":
                tasks = [t for t in tasks if t.status == status_filter]
            return {
                "tasks": [t.to_dict() for t in tasks],
                "total": len(self._todos),
                "pending": sum(1 for t in self._todos if t.status == "pending"),
                "in_progress": sum(1 for t in self._todos if t.status == "in_progress"),
                "completed": sum(1 for t in self._todos if t.status == "completed"),
            }

        return {"error": f"Unknown todo action: {action}"}

    async def _delegate(self, params: dict[str, Any]) -> dict[str, Any]:
        """Delegate a subtask to another agent."""
        agent_id = params.get("agent_id", "")
        task = params.get("task", "")
        if not agent_id or not task:
            return {"error": "agent_id and task are required"}
        if self._delegate_handler:
            result = await self._delegate_handler(agent_id, task)
            return {"agent_id": agent_id, "result": result}
        return {"error": "Delegation not available (single-agent mode)"}

    async def _emit(self, params: dict[str, Any]) -> dict[str, Any]:
        """Publish an event to the event bus."""
        topic = params.get("topic", "")
        data = params.get("data", {})
        if not topic:
            return {"error": "topic is required"}
        if self._emit_handler:
            await self._emit_handler(topic, data)
            return {"published": True, "topic": topic}
        return {"published": False, "note": "No event bus configured"}

    # ─── Memory tool ──────────────────────────────────────────────────

    def set_memory_manager(self, memory_manager: Any) -> None:
        """Inject the AppMemoryManager for memory operations."""
        self._memory_manager = memory_manager

    async def _memory(self, params: dict[str, Any]) -> dict[str, Any]:
        """Read/write to multi-level memory (working, conversation, project, episodic).

        This gives the agent active control over its own memory — it can
        store key facts, recall previous context, and search past episodes.
        """
        mgr = getattr(self, "_memory_manager", None)
        if mgr is None:
            return {"error": "No memory manager configured. Add a memory: block to your app YAML."}

        action = params.get("action", "recall")

        if action == "store":
            level = params.get("level", "working")
            key = params.get("key", "")
            value = params.get("value", "")
            if not key:
                return {"error": "key is required"}

            if level == "working":
                mgr.set_working(key, value)
                return {"stored": True, "level": "working", "key": key}
            elif level == "conversation":
                await mgr.set_conversation(f"llmos:app:memory:{key}", value)
                return {"stored": True, "level": "conversation", "key": key}
            elif level == "project":
                content = await mgr.load_project_memory()
                # Append to project memory
                new_content = content + f"\n## {key}\n{value}\n" if content else f"## {key}\n{value}\n"
                await mgr.save_project_memory(new_content)
                return {"stored": True, "level": "project", "key": key}
            elif level == "episodic":
                episode_id = f"ep-{uuid.uuid4().hex[:8]}"
                metadata = {"key": key}
                await mgr.record_episode(episode_id, value, metadata)
                return {"stored": True, "level": "episodic", "episode_id": episode_id}
            else:
                return {"error": f"Unknown memory level: {level}. Use: working, conversation, project, episodic"}

        elif action == "recall":
            level = params.get("level", "working")
            key = params.get("key", "")

            if level == "working":
                if key:
                    val = mgr.get_working(key)
                    return {"level": "working", "key": key, "value": val, "found": val is not None}
                else:
                    return {"level": "working", "entries": mgr.working}
            elif level == "conversation":
                if not key:
                    return {"error": "key is required for conversation recall"}
                val = await mgr.get_conversation(f"llmos:app:memory:{key}")
                return {"level": "conversation", "key": key, "value": val, "found": val is not None}
            elif level == "project":
                content = await mgr.load_project_memory()
                return {"level": "project", "content": content, "found": bool(content)}
            else:
                return {"error": f"Unknown level: {level}. Use: working, conversation, project"}

        elif action == "search":
            query = params.get("query", "")
            top_k = int(params.get("top_k", 5))
            if not query:
                return {"error": "query is required for search"}
            results = await mgr.recall_episodes(query, top_k=top_k)
            return {"results": results, "count": len(results)}

        elif action == "list":
            level = params.get("level", "working")
            if level == "working":
                return {"level": "working", "keys": list(mgr.working.keys())}
            else:
                return {"error": "list is only supported for working memory"}

        return {"error": f"Unknown memory action: {action}. Use: store, recall, search, list"}

    async def _send_message(self, params: dict[str, Any]) -> dict[str, Any]:
        """Send a message to another agent (peer-to-peer communication)."""
        target = params.get("target", "") or params.get("agent_id", "")
        message = params.get("message", "") or params.get("content", "")
        if not target or not message:
            return {"error": "target and message are required"}
        if self._send_message_handler:
            result = await self._send_message_handler(target, message)
            return result if isinstance(result, dict) else {"sent": True}
        return {"error": "P2P messaging not available (not in peer_to_peer mode)"}


# Handler registry
_HANDLERS: dict[str, Callable] = {
    "ask_user": BuiltinToolExecutor._ask_user,
    "todo": BuiltinToolExecutor._todo,
    "delegate": BuiltinToolExecutor._delegate,
    "emit": BuiltinToolExecutor._emit,
    "memory": BuiltinToolExecutor._memory,
    "send_message": BuiltinToolExecutor._send_message,
}
