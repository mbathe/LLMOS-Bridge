"""Tool registry — Filters module actions for an app based on its tool definitions.

Takes ToolDefinition entries from the YAML and maps them to actual module
actions from the ModuleRegistry, applying constraints and building the
tool descriptions that get injected into the LLM system prompt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .models import ToolDefinition


@dataclass
class ResolvedTool:
    """A fully resolved tool ready for LLM consumption."""
    name: str                        # "module.action" or builtin name
    module: str                      # module ID (empty for builtins)
    action: str                      # action name (empty for builtins)
    description: str
    parameters: dict[str, Any]       # JSON Schema for params
    is_builtin: bool = False
    constraints: dict[str, Any] = field(default_factory=dict)


class AppToolRegistry:
    """Resolves tool definitions against the module registry."""

    def __init__(self, available_modules: dict[str, Any] | None = None):
        """Initialize with available modules.

        Args:
            available_modules: Dict mapping module_id -> module manifest/info.
                Each value should have 'actions' list of dicts with
                'name', 'description', 'params' keys.
        """
        self._modules = available_modules or {}

    @staticmethod
    def module_info_from_registry(registry: Any) -> dict[str, dict]:
        """Convert a ModuleRegistry's manifests to the dict format we expect.

        Args:
            registry: A ModuleRegistry instance (duck-typed to avoid circular imports).

        Returns:
            Dict suitable for passing to ``AppToolRegistry(available_modules=...)``.
        """
        from llmos_bridge.apps.daemon_executor import module_info_from_manifests
        return module_info_from_manifests(registry.all_manifests())

    def resolve_tools(self, tool_defs: list[ToolDefinition]) -> list[ResolvedTool]:
        """Resolve a list of ToolDefinition entries into ResolvedTools."""
        resolved: list[ResolvedTool] = []
        for td in tool_defs:
            resolved.extend(self._resolve_one(td))
        return resolved

    def _resolve_one(self, td: ToolDefinition) -> list[ResolvedTool]:
        """Resolve a single ToolDefinition."""
        # Built-in tool
        if td.builtin:
            return [self._resolve_builtin(td)]

        # Module-based tool
        if td.module:
            return self._resolve_module(td)

        # Custom tool (with id)
        if td.id:
            return [ResolvedTool(
                name=td.id,
                module="",
                action="",
                description=td.description,
                parameters=td.params,
                is_builtin=True,
            )]

        return []

    def _resolve_builtin(self, td: ToolDefinition) -> ResolvedTool:
        """Resolve a built-in tool."""
        builtin_info = _BUILTIN_TOOLS.get(td.builtin, {})
        return ResolvedTool(
            name=td.builtin,
            module="",
            action="",
            description=td.description or builtin_info.get("description", ""),
            parameters=builtin_info.get("parameters", {}),
            is_builtin=True,
        )

    def _resolve_module(self, td: ToolDefinition) -> list[ResolvedTool]:
        """Resolve a module-based tool definition into one or more ResolvedTools."""
        module_info = self._modules.get(td.module)
        if module_info is None:
            # Module not available — create a placeholder
            if td.action:
                return [ResolvedTool(
                    name=f"{td.module}.{td.action}",
                    module=td.module,
                    action=td.action,
                    description=td.description or f"{td.module}.{td.action}",
                    parameters={},
                )]
            return []

        # Get available actions from module
        actions = module_info.get("actions", [])
        if isinstance(actions, dict):
            actions = [{"name": k, **v} for k, v in actions.items()]

        resolved: list[ResolvedTool] = []
        for action_info in actions:
            action_name = action_info.get("name", "")

            # Filter: single action specified
            if td.action and action_name != td.action:
                continue

            # Filter: action subset
            if td.actions and action_name not in td.actions:
                continue

            # Filter: excluded actions
            if td.exclude and action_name in td.exclude:
                continue

            desc = td.description if (td.action and td.description) else action_info.get("description", "")
            params = action_info.get("params", action_info.get("parameters", {}))

            constraints = {}
            if td.constraints:
                constraints = td.constraints.model_dump(exclude_defaults=True)

            resolved.append(ResolvedTool(
                name=f"{td.module}.{action_name}",
                module=td.module,
                action=action_name,
                description=desc,
                parameters=params if isinstance(params, dict) else {},
                constraints=constraints,
            ))

        return resolved

    def format_for_llm(self, tools: list[ResolvedTool]) -> str:
        """Format resolved tools as a description block for the LLM system prompt."""
        lines = []
        for tool in tools:
            lines.append(f"- **{tool.name}**: {tool.description}")
            if tool.parameters:
                for pname, pinfo in tool.parameters.items():
                    if isinstance(pinfo, dict):
                        ptype = pinfo.get("type", "any")
                        pdesc = pinfo.get("description", "")
                        required = pinfo.get("required", False)
                        req_marker = " (required)" if required else ""
                        lines.append(f"    - {pname}: {ptype}{req_marker} — {pdesc}")
        return "\n".join(lines)

    def to_openai_tools(self, tools: list[ResolvedTool]) -> list[dict[str, Any]]:
        """Convert resolved tools to OpenAI-compatible function calling format."""
        result = []
        for tool in tools:
            properties = {}
            required_fields = []
            for pname, pinfo in tool.parameters.items():
                if isinstance(pinfo, dict):
                    prop = {"type": pinfo.get("type", "string")}
                    if "description" in pinfo:
                        prop["description"] = pinfo["description"]
                    if "enum" in pinfo:
                        prop["enum"] = pinfo["enum"]
                    properties[pname] = prop
                    if pinfo.get("required", False):
                        required_fields.append(pname)
                else:
                    properties[pname] = {"type": "string"}

            result.append({
                "type": "function",
                "function": {
                    "name": tool.name.replace(".", "__"),
                    "description": tool.description,
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required_fields,
                    },
                },
            })
        return result


# ─── Built-in tool definitions ─────────────────────────────────────────

_BUILTIN_TOOLS: dict[str, dict[str, Any]] = {
    "ask_user": {
        "description": "Ask the user a question and wait for their response",
        "parameters": {
            "question": {
                "type": "string",
                "description": "The question to ask",
                "required": True,
            },
        },
    },
    "todo": {
        "description": "Persistent task tracking: add, update, complete, remove, list, and clear tasks. Tasks survive across sessions.",
        "parameters": {
            "action": {
                "type": "string",
                "description": "Action to perform on the task list",
                "required": True,
                "enum": ["add", "update", "complete", "remove", "clear_completed", "list"],
            },
            "task": {
                "type": "string",
                "description": "Task description (for add/update)",
                "required": False,
            },
            "task_id": {
                "type": "string",
                "description": "Task ID (for update/complete/remove)",
                "required": False,
            },
            "status": {
                "type": "string",
                "description": "New status (for update)",
                "required": False,
                "enum": ["pending", "in_progress", "completed"],
            },
            "status_filter": {
                "type": "string",
                "description": "Filter tasks by status (for list). Default: all",
                "required": False,
                "enum": ["all", "pending", "in_progress", "completed"],
            },
        },
    },
    "memory": {
        "description": "Read/write to multi-level memory. Levels: working (fast, per-run), conversation (persisted KV), project (file), episodic (semantic search).",
        "parameters": {
            "action": {
                "type": "string",
                "description": "Action to perform",
                "required": True,
                "enum": ["store", "recall", "search", "list"],
            },
            "level": {
                "type": "string",
                "description": "Memory level to operate on",
                "required": False,
                "enum": ["working", "conversation", "project", "episodic"],
            },
            "key": {
                "type": "string",
                "description": "Key for store/recall operations",
                "required": False,
            },
            "value": {
                "type": "string",
                "description": "Value to store",
                "required": False,
            },
            "query": {
                "type": "string",
                "description": "Search query (for episodic search)",
                "required": False,
            },
            "top_k": {
                "type": "integer",
                "description": "Number of results for search (default: 5)",
                "required": False,
            },
        },
    },
    "delegate": {
        "description": "Delegate a subtask to another agent",
        "parameters": {
            "agent_id": {
                "type": "string",
                "description": "ID of the agent to delegate to",
                "required": True,
            },
            "task": {
                "type": "string",
                "description": "Task description for the agent",
                "required": True,
            },
        },
    },
    "emit": {
        "description": "Publish an event to the event bus",
        "parameters": {
            "topic": {
                "type": "string",
                "description": "Event topic",
                "required": True,
            },
            "data": {
                "type": "object",
                "description": "Event payload",
                "required": False,
            },
        },
    },
    "send_message": {
        "description": "Send a message to another agent (peer-to-peer communication)",
        "parameters": {
            "target": {
                "type": "string",
                "description": "ID of the target agent",
                "required": True,
            },
            "message": {
                "type": "string",
                "description": "Message content to send",
                "required": True,
            },
        },
    },
}
