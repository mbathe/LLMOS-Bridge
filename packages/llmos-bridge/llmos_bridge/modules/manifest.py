"""Module layer — Capability Manifest.

A ModuleManifest is the machine-readable contract between a module and the
rest of the system (LangChain SDK generator, /modules API, IML validator).

It describes:
  - Module identity and version
  - Supported platforms
  - Available actions with their param schemas
  - Required permission level
  - Usage examples for LLM few-shot prompting
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ParamSpec:
    """Description of a single action parameter."""

    name: str
    type: str  # JSON Schema type: string, integer, number, boolean, object, array
    description: str
    required: bool = True
    default: Any = None
    enum: list[Any] | None = None
    example: Any = None


@dataclass
class ActionSpec:
    """Description of a single action exposed by a module."""

    name: str
    description: str
    params: list[ParamSpec] = field(default_factory=list)
    returns: str = "object"
    returns_description: str = ""
    examples: list[dict[str, Any]] = field(default_factory=list)
    permission_required: str = "local_worker"
    platforms: list[str] = field(default_factory=lambda: ["all"])
    tags: list[str] = field(default_factory=list)

    def to_json_schema(self) -> dict[str, Any]:
        """Generate a JSONSchema dict for the params of this action."""
        properties: dict[str, Any] = {}
        required: list[str] = []

        for param in self.params:
            prop: dict[str, Any] = {
                "type": param.type,
                "description": param.description,
            }
            if param.enum is not None:
                prop["enum"] = param.enum
            if param.example is not None:
                prop["examples"] = [param.example]
            if param.default is not None:
                prop["default"] = param.default
            properties[param.name] = prop
            if param.required:
                required.append(param.name)

        schema: dict[str, Any] = {
            "type": "object",
            "properties": properties,
        }
        if required:
            schema["required"] = required
        return schema

    def to_langchain_tool_schema(self) -> dict[str, Any]:
        """Generate a LangChain-compatible tool schema."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.to_json_schema(),
        }


@dataclass
class ModuleManifest:
    """Complete capability manifest for a module.

    The ``declared_permissions`` field is part of the plugin security model.
    Modules declare the OS-level or system capabilities they require, so the
    PermissionGuard can enforce them at the platform level before loading.

    Standard permission strings:
      - ``"filesystem_read"``   — read access to the filesystem
      - ``"filesystem_write"``  — write/delete access to the filesystem
      - ``"process_execute"``   — ability to spawn subprocesses
      - ``"process_kill"``      — ability to terminate processes
      - ``"network_access"``    — outbound HTTP/HTTPS connections
      - ``"screen_capture"``    — screenshot / display access
      - ``"gpio_access"``       — GPIO hardware access (IoT/Raspberry Pi)
      - ``"database_access"``   — database read/write
      - ``"display_automation"``— GUI automation (keyboard/mouse injection)
      - ``"browser_control"``   — headless browser automation

    Community modules may declare custom strings prefixed with their module_id,
    e.g. ``"mymodule:cloud_sync"``.
    """

    module_id: str
    version: str
    description: str
    author: str = ""
    homepage: str = ""
    platforms: list[str] = field(default_factory=lambda: ["all"])
    actions: list[ActionSpec] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    declared_permissions: list[str] = field(
        default_factory=list,
        metadata={
            "description": (
                "OS-level capabilities required by this module.  "
                "Declaring permissions enables the PermissionGuard to enforce "
                "them before loading and gives users visibility into what a "
                "module can do before installing it."
            )
        },
    )

    def get_action(self, action_name: str) -> ActionSpec | None:
        for action in self.actions:
            if action.name == action_name:
                return action
        return None

    def action_names(self) -> list[str]:
        return [a.name for a in self.actions]

    def to_dict(self) -> dict[str, Any]:
        return {
            "module_id": self.module_id,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "homepage": self.homepage,
            "platforms": self.platforms,
            "dependencies": self.dependencies,
            "tags": self.tags,
            "declared_permissions": self.declared_permissions,
            "actions": [
                {
                    "name": a.name,
                    "description": a.description,
                    "params_schema": a.to_json_schema(),
                    "returns": a.returns,
                    "returns_description": a.returns_description,
                    "permission_required": a.permission_required,
                    "platforms": a.platforms,
                    "examples": a.examples,
                    "tags": a.tags,
                }
                for a in self.actions
            ],
        }
