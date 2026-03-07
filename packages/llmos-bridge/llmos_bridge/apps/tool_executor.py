"""Standalone tool executor for LLMOS App Language (Agentique Mode fallback).

Executes module actions directly without the full daemon infrastructure.
This is used by ``llmos app run`` in CLI mode when no daemon is running.

Supports: filesystem, os_exec (the two most common modules for apps).
Other modules fall back to a "not available" error.

When the daemon IS running, DaemonToolExecutor is used instead — it routes
through the full security pipeline and gives access to all 18+ modules.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import shlex
import shutil
from pathlib import Path
from typing import Any

import logging

logger = logging.getLogger(__name__)


class StandaloneToolExecutor:
    """Executes module actions without the full daemon.

    Implements a minimal subset of module actions needed for app execution:
    - filesystem: read_file, write_file, list_directory, search_files, create_directory
    - os_exec: run_command, get_system_info, get_env_var
    - memory: store, recall, search, delete, list_keys, list_backends, set_objective, get_context, update_progress
    """

    def __init__(self, *, working_directory: str | None = None):
        self._cwd = working_directory or os.getcwd()
        self._memory_module: Any = None

    def get_module_info(self) -> dict[str, dict]:
        """Return module info dicts usable by AppToolRegistry.

        Without this, the registry creates placeholders with empty parameter
        schemas and the LLM doesn't know what arguments to pass.
        """
        return _STANDALONE_MODULE_INFO

    async def execute(self, module_id: str, action: str, params: dict[str, Any]) -> dict[str, Any]:
        """Execute a module action and return the result."""
        handler = self._get_handler(module_id, action)
        if handler is None:
            return {"error": f"Action '{module_id}.{action}' not available in standalone mode"}
        try:
            return await handler(params)
        except Exception as e:
            return {"error": str(e)}

    def set_memory_module(self, memory_module: Any) -> None:
        """Inject a MemoryModule for standalone memory operations."""
        self._memory_module = memory_module

    def set_agent_spawn_module(self, agent_spawn_module: Any) -> None:
        """Inject an AgentSpawnModule for standalone agent spawning."""
        self._agent_spawn_module = agent_spawn_module

    def set_context_manager_module(self, context_manager_module: Any) -> None:
        """Inject a ContextManagerModule for standalone context management."""
        self._context_manager_module = context_manager_module

    def _get_handler(self, module_id: str, action: str) -> Any:
        handlers = {
            ("filesystem", "read_file"): self._fs_read_file,
            ("filesystem", "write_file"): self._fs_write_file,
            ("filesystem", "list_directory"): self._fs_list_directory,
            ("filesystem", "create_directory"): self._fs_create_directory,
            ("filesystem", "search_files"): self._fs_search_files,
            ("filesystem", "delete_file"): self._fs_delete_file,
            ("filesystem", "get_file_info"): self._fs_get_file_info,
            ("os_exec", "run_command"): self._os_run_command,
            ("os_exec", "get_system_info"): self._os_get_system_info,
            ("os_exec", "get_env_var"): self._os_get_env_var,
        }
        # Memory module actions dispatch to the real module
        if module_id == "memory" and self._memory_module is not None:
            return lambda params: self._memory_module.execute(action, params)
        # Agent spawn module dispatch
        if module_id == "agent_spawn" and getattr(self, "_agent_spawn_module", None) is not None:
            return lambda params: self._agent_spawn_module.execute(action, params)
        # Context manager module dispatch
        if module_id == "context_manager" and getattr(self, "_context_manager_module", None) is not None:
            return lambda params: self._context_manager_module.execute(action, params)
        return handlers.get((module_id, action))

    # ─── Filesystem ───────────────────────────────────────────────────

    async def _fs_read_file(self, params: dict[str, Any]) -> dict[str, Any]:
        path = Path(params["path"]).expanduser()
        if not path.is_absolute():
            path = Path(self._cwd) / path

        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        if not path.is_file():
            raise IsADirectoryError(f"Path is a directory: {path}")

        encoding = params.get("encoding", "utf-8")
        content = await asyncio.to_thread(path.read_text, encoding)

        start = params.get("start_line")
        end = params.get("end_line")
        if start or end:
            lines = content.splitlines(keepends=True)
            s = (start - 1) if start else 0
            e = end if end else len(lines)
            content = "".join(lines[s:e])

        return {"path": str(path), "content": content, "size_bytes": len(content.encode(encoding))}

    async def _fs_write_file(self, params: dict[str, Any]) -> dict[str, Any]:
        path = Path(params["path"]).expanduser()
        if not path.is_absolute():
            path = Path(self._cwd) / path

        content = params.get("content", "")
        encoding = params.get("encoding", "utf-8")
        create_dirs = params.get("create_dirs", True)

        if create_dirs:
            path.parent.mkdir(parents=True, exist_ok=True)

        await asyncio.to_thread(path.write_text, content, encoding)
        return {"path": str(path), "bytes_written": len(content.encode(encoding))}

    async def _fs_list_directory(self, params: dict[str, Any]) -> dict[str, Any]:
        path = Path(params.get("path", self._cwd)).expanduser()
        if not path.is_absolute():
            path = Path(self._cwd) / path

        if not path.exists():
            raise FileNotFoundError(f"Directory not found: {path}")
        if not path.is_dir():
            raise NotADirectoryError(f"Not a directory: {path}")

        entries = []
        for entry in sorted(path.iterdir()):
            try:
                st = entry.stat()
                entries.append({
                    "name": entry.name,
                    "type": "directory" if entry.is_dir() else "file",
                    "size": st.st_size if entry.is_file() else 0,
                })
            except OSError:
                entries.append({"name": entry.name, "type": "unknown", "size": 0})

        return {"path": str(path), "entries": entries, "count": len(entries)}

    async def _fs_create_directory(self, params: dict[str, Any]) -> dict[str, Any]:
        path = Path(params["path"]).expanduser()
        if not path.is_absolute():
            path = Path(self._cwd) / path

        path.mkdir(parents=True, exist_ok=True)
        return {"path": str(path), "created": True}

    async def _fs_search_files(self, params: dict[str, Any]) -> dict[str, Any]:
        path = Path(params.get("path", self._cwd)).expanduser()
        pattern = params.get("pattern", "*")
        content_pattern = params.get("content_pattern", "")

        matches = []
        for match in path.rglob(pattern):
            if match.is_file():
                if content_pattern:
                    try:
                        text = match.read_text(errors="replace")
                        if content_pattern not in text:
                            continue
                    except Exception:
                        continue
                matches.append(str(match))
                if len(matches) >= 50:
                    break

        return {"matches": matches, "count": len(matches)}

    async def _fs_delete_file(self, params: dict[str, Any]) -> dict[str, Any]:
        path = Path(params["path"]).expanduser()
        if not path.is_absolute():
            path = Path(self._cwd) / path

        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
        else:
            raise FileNotFoundError(f"Not found: {path}")
        return {"path": str(path), "deleted": True}

    async def _fs_get_file_info(self, params: dict[str, Any]) -> dict[str, Any]:
        path = Path(params["path"]).expanduser()
        if not path.is_absolute():
            path = Path(self._cwd) / path

        st = path.stat()
        return {
            "path": str(path),
            "exists": path.exists(),
            "is_file": path.is_file(),
            "is_directory": path.is_dir(),
            "size": st.st_size,
            "modified": st.st_mtime,
        }

    # ─── OS Exec ──────────────────────────────────────────────────────

    async def _os_run_command(self, params: dict[str, Any]) -> dict[str, Any]:
        command = params.get("command")
        if isinstance(command, str):
            # App YAML often passes command as string — split safely
            command = shlex.split(command)
        if not command:
            raise ValueError("No command provided")

        cwd = params.get("working_directory") or self._cwd
        timeout = params.get("timeout", 30)
        env = os.environ.copy()
        if params.get("env"):
            env.update(params["env"])

        proc = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            raise TimeoutError(f"Command timed out after {timeout}s")

        return {
            "exit_code": proc.returncode,
            "stdout": stdout.decode(errors="replace") if stdout else "",
            "stderr": stderr.decode(errors="replace") if stderr else "",
            "success": proc.returncode == 0,
        }

    async def _os_get_system_info(self, params: dict[str, Any]) -> dict[str, Any]:
        import platform
        return {
            "os": platform.system(),
            "os_version": platform.version(),
            "architecture": platform.machine(),
            "hostname": platform.node(),
            "python_version": platform.python_version(),
        }

    async def _os_get_env_var(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name", "")
        value = os.environ.get(name, "")
        return {"name": name, "value": value, "exists": name in os.environ}


# ─── Parameter schemas for standalone tools ────────────────────────────
# These are injected into the LLM tool definitions so it knows what
# arguments each tool accepts.

_STANDALONE_MODULE_INFO: dict[str, dict] = {
    "filesystem": {
        "actions": [
            {
                "name": "read_file",
                "description": "Read the contents of a file",
                "params": {
                    "path": {"type": "string", "description": "Path to the file to read", "required": True},
                    "encoding": {"type": "string", "description": "File encoding (default: utf-8)"},
                    "start_line": {"type": "integer", "description": "Start line number (1-indexed)"},
                    "end_line": {"type": "integer", "description": "End line number (inclusive)"},
                },
            },
            {
                "name": "write_file",
                "description": "Write content to a file (creates parent directories)",
                "params": {
                    "path": {"type": "string", "description": "Path to the file to write", "required": True},
                    "content": {"type": "string", "description": "Content to write", "required": True},
                    "encoding": {"type": "string", "description": "File encoding (default: utf-8)"},
                },
            },
            {
                "name": "list_directory",
                "description": "List files and directories in a path",
                "params": {
                    "path": {"type": "string", "description": "Directory path to list (default: working directory)", "required": True},
                },
            },
            {
                "name": "search_files",
                "description": "Search for files matching a pattern, optionally with content matching",
                "params": {
                    "path": {"type": "string", "description": "Directory to search in"},
                    "pattern": {"type": "string", "description": "Glob pattern for file names (e.g. '*.py')", "required": True},
                    "content_pattern": {"type": "string", "description": "Text to search for inside files"},
                },
            },
            {
                "name": "create_directory",
                "description": "Create a directory (and parent directories)",
                "params": {
                    "path": {"type": "string", "description": "Directory path to create", "required": True},
                },
            },
            {
                "name": "delete_file",
                "description": "Delete a file or directory",
                "params": {
                    "path": {"type": "string", "description": "Path to delete", "required": True},
                },
            },
            {
                "name": "get_file_info",
                "description": "Get metadata about a file (size, type, modified time)",
                "params": {
                    "path": {"type": "string", "description": "Path to the file", "required": True},
                },
            },
        ],
    },
    "os_exec": {
        "actions": [
            {
                "name": "run_command",
                "description": "Run a shell command and return stdout/stderr/exit_code",
                "params": {
                    "command": {"type": "string", "description": "Command to run (string or shell command)", "required": True},
                    "working_directory": {"type": "string", "description": "Working directory for the command"},
                    "timeout": {"type": "integer", "description": "Timeout in seconds (default: 30)"},
                },
            },
            {
                "name": "get_system_info",
                "description": "Get OS, architecture, hostname, and Python version",
                "params": {},
            },
            {
                "name": "get_env_var",
                "description": "Get the value of an environment variable",
                "params": {
                    "name": {"type": "string", "description": "Environment variable name", "required": True},
                },
            },
        ],
    },
    "memory": {
        "actions": [
            {
                "name": "store",
                "description": "Store a key-value pair in a memory backend (kv, vector, file, cognitive)",
                "params": {
                    "key": {"type": "string", "description": "Key to store under", "required": True},
                    "value": {"type": "string", "description": "Value to store", "required": True},
                    "backend": {"type": "string", "description": "Backend: kv (default), vector, file, cognitive"},
                    "metadata": {"type": "object", "description": "Optional metadata"},
                    "ttl_seconds": {"type": "number", "description": "Time-to-live in seconds"},
                },
            },
            {
                "name": "recall",
                "description": "Recall a value by key from a memory backend",
                "params": {
                    "key": {"type": "string", "description": "Key to recall", "required": True},
                    "backend": {"type": "string", "description": "Backend to query (default: kv)"},
                },
            },
            {
                "name": "search",
                "description": "Semantic or fuzzy search across one or all memory backends",
                "params": {
                    "query": {"type": "string", "description": "Search query", "required": True},
                    "backend": {"type": "string", "description": "Backend to search (omit for all)"},
                    "top_k": {"type": "integer", "description": "Max results (default: 5)"},
                },
            },
            {
                "name": "delete",
                "description": "Delete a key from a memory backend",
                "params": {
                    "key": {"type": "string", "description": "Key to delete", "required": True},
                    "backend": {"type": "string", "description": "Backend to delete from"},
                },
            },
            {
                "name": "list_keys",
                "description": "List keys stored in a memory backend",
                "params": {
                    "backend": {"type": "string", "description": "Backend to list (default: kv)"},
                    "prefix": {"type": "string", "description": "Filter by key prefix"},
                    "limit": {"type": "integer", "description": "Max keys (default: 100)"},
                },
            },
            {
                "name": "list_backends",
                "description": "List all registered memory backends and their capabilities",
                "params": {},
            },
            {
                "name": "set_objective",
                "description": "Set a cognitive objective — stays in permanent memory, filters all actions through this goal",
                "params": {
                    "goal": {"type": "string", "description": "The primary objective/goal", "required": True},
                    "sub_goals": {"type": "array", "description": "Sub-goals to track"},
                    "success_criteria": {"type": "array", "description": "Success criteria"},
                },
            },
            {
                "name": "get_context",
                "description": "Get the full cognitive context (objective + active state + recent decisions)",
                "params": {},
            },
            {
                "name": "update_progress",
                "description": "Update the progress of the current cognitive objective (0.0 to 1.0)",
                "params": {
                    "progress": {"type": "number", "description": "Progress from 0.0 to 1.0", "required": True},
                    "completed_sub_goal": {"type": "string", "description": "Sub-goal just completed"},
                    "complete": {"type": "boolean", "description": "Mark objective as fully completed"},
                },
            },
            {
                "name": "observe",
                "description": "Get a real-time snapshot of ALL memory state across ALL backends. Returns a human-readable summary — no need to know specific keys.",
                "params": {},
            },
        ],
    },
    "agent_spawn": {
        "actions": [
            {
                "name": "spawn_agent",
                "description": "Create and launch an autonomous sub-agent with its own LLM loop and tools",
                "params": {
                    "name": {"type": "string", "description": "Human-readable name for the sub-agent", "required": True},
                    "objective": {"type": "string", "description": "The task/objective for the sub-agent", "required": True},
                    "system_prompt": {"type": "string", "description": "System prompt for the agent"},
                    "tools": {"type": "array", "description": 'Tools: ["filesystem.read_file", "os_exec.run_command"]'},
                    "model": {"type": "string", "description": "LLM model (default: same as parent)"},
                    "max_turns": {"type": "integer", "description": "Max turns (default: 15)"},
                    "context": {"type": "string", "description": "Additional context to pass"},
                },
            },
            {
                "name": "check_agent",
                "description": "Check the status of a spawned agent",
                "params": {
                    "spawn_id": {"type": "string", "description": "The spawn_id from spawn_agent", "required": True},
                },
            },
            {
                "name": "get_result",
                "description": "Get the final result of a completed agent",
                "params": {
                    "spawn_id": {"type": "string", "description": "The spawn_id from spawn_agent", "required": True},
                },
            },
            {
                "name": "list_agents",
                "description": "List all spawned agents and their statuses",
                "params": {
                    "status_filter": {"type": "string", "description": "Filter: running, completed, failed, cancelled, all"},
                },
            },
            {
                "name": "cancel_agent",
                "description": "Cancel a running agent",
                "params": {
                    "spawn_id": {"type": "string", "description": "The spawn_id to cancel", "required": True},
                },
            },
            {
                "name": "wait_agent",
                "description": "Wait for a spawned agent to complete (blocks until done or timeout)",
                "params": {
                    "spawn_id": {"type": "string", "description": "Agent to wait for", "required": True},
                    "timeout": {"type": "number", "description": "Max seconds to wait (default: 300)"},
                },
            },
            {
                "name": "send_message",
                "description": "Send a message to a running agent",
                "params": {
                    "spawn_id": {"type": "string", "description": "Target agent", "required": True},
                    "message": {"type": "string", "description": "Message content", "required": True},
                },
            },
        ],
    },
    "context_manager": {
        "actions": [
            {
                "name": "get_budget",
                "description": "Get the current context budget allocation showing token distribution across system prompt, cognitive state, memory, history, and tools",
                "params": {},
            },
            {
                "name": "compress_history",
                "description": "Compress conversation history by summarizing older messages. Use when context is getting large.",
                "params": {
                    "keep_last_n": {"type": "integer", "description": "Number of recent messages to keep uncompressed (default: 10)"},
                },
            },
            {
                "name": "fetch_context",
                "description": "Fetch detailed context from compressed conversation segments. Use to retrieve full details about a topic that was compressed.",
                "params": {
                    "query": {"type": "string", "description": "What to look for in compressed history", "required": True},
                    "segment_index": {"type": "integer", "description": "Specific compression segment to retrieve (0 = most recent)"},
                },
            },
            {
                "name": "get_tools_summary",
                "description": "Get a compact summary of available tools/actions, filtered by application permissions",
                "params": {
                    "module_filter": {"type": "string", "description": "Only show tools from this module"},
                },
            },
            {
                "name": "get_state",
                "description": "Get the current context window state: token usage, budget utilization, compression history",
                "params": {},
            },
        ],
    },
}
