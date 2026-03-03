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
    # Security decorator metadata (auto-populated by _collect_security_metadata)
    permissions: list[str] = field(default_factory=list)
    risk_level: str = ""
    irreversible: bool = False
    data_classification: str = ""
    # Streaming metadata (auto-populated by _collect_streaming_metadata)
    streams_progress: bool = False
    # -- Module Spec v3 fields (all default → backwards compatible) --
    output_schema: dict[str, Any] | None = field(
        default=None,
        metadata={"description": "Full JSONSchema for the return value. None means untyped."},
    )
    side_effects: list[str] = field(
        default_factory=list,
        metadata={
            "description": (
                "Declared side effects: 'filesystem_write', 'filesystem_delete', "
                "'network_request', 'state_mutation', 'process_spawn', "
                "'screen_interaction', 'notification', 'external_api'."
            )
        },
    )
    execution_mode: str = field(
        default="async",
        metadata={"description": "Execution mode: 'sync', 'async', 'background', 'scheduled'."},
    )
    capabilities: list["Capability"] = field(
        default_factory=list,
        metadata={"description": "Structured capabilities with scope and constraints."},
    )

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
class Capability:
    """Structured permission/capability with scope and constraints.

    Extends plain string permissions with rich metadata for fine-grained
    access control.  Plain strings are still supported — ``Capability``
    adds scope and constraints for modules that need them.

    Examples::

        Capability("filesystem.write", scope="sandbox_only")
        Capability("network.send", constraints={"allowed_hosts": ["api.example.com"]})
        Capability("database.write", scope="schema_only", constraints={"tables": ["users"]})
    """

    permission: str  # e.g. "filesystem.write", "network.send"
    scope: str = ""  # e.g. "sandbox_only", "user_home", "schema_only"
    constraints: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"permission": self.permission}
        if self.scope:
            d["scope"] = self.scope
        if self.constraints:
            d["constraints"] = self.constraints
        return d

    @classmethod
    def from_string(cls, permission: str) -> "Capability":
        """Create a Capability from a plain permission string."""
        return cls(permission=permission)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Capability":
        """Create a Capability from a dict."""
        return cls(
            permission=data["permission"],
            scope=data.get("scope", ""),
            constraints=data.get("constraints", {}),
        )


@dataclass
class ServiceDescriptor:
    """Description of a service provided by a module.

    Used in the manifest to declare what services a module exposes
    on the ServiceBus for inter-module communication.
    """

    name: str
    methods: list[str] = field(default_factory=list)
    description: str = ""


@dataclass
class ResourceLimits:
    """Resource budget for a module."""

    max_cpu_percent: float = 100.0  # 0-100
    max_memory_mb: int = 0  # 0 = unlimited
    max_execution_seconds: float = 0.0  # 0 = unlimited
    max_concurrent_actions: int = 0  # 0 = unlimited


@dataclass
class ModuleSignature:
    """Ed25519 cryptographic signature of a module package."""

    public_key_fingerprint: str  # SHA-256 of the Ed25519 public key
    signature_hex: str  # hex-encoded Ed25519 signature
    signed_hash: str  # SHA-256 of manifest + code content
    signed_at: str = ""  # ISO 8601 timestamp


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
    # -- Module Spec v2 fields (all default → backwards compatible) --
    module_type: str = "user"
    provides_services: list[ServiceDescriptor] = field(default_factory=list)
    consumes_services: list[str] = field(default_factory=list)
    emits_events: list[str] = field(default_factory=list)
    subscribes_events: list[str] = field(default_factory=list)
    config_schema: dict[str, Any] | None = None
    # -- Module Spec v3 fields (all default → backwards compatible) --
    resource_limits: ResourceLimits | None = None
    sandbox_level: str = "none"  # "none" | "basic" | "strict" | "isolated"
    license: str = ""  # SPDX identifier, e.g. "MIT", "Apache-2.0"
    optional_dependencies: list[str] = field(default_factory=list)
    module_dependencies: dict[str, str] = field(
        default_factory=dict,
        metadata={"description": "Module-to-module deps with PEP-440 version specifiers."},
    )
    signing: ModuleSignature | None = None
    declared_capabilities: list[Capability] = field(
        default_factory=list,
        metadata={
            "description": (
                "Structured capabilities with scope and constraints.  "
                "Extends declared_permissions with fine-grained control."
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
        result: dict[str, Any] = {
            "module_id": self.module_id,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "homepage": self.homepage,
            "platforms": self.platforms,
            "dependencies": self.dependencies,
            "tags": self.tags,
            "declared_permissions": self.declared_permissions,
            "actions": [self._action_to_dict(a) for a in self.actions],
        }
        # Include v2 fields only when non-default (compact output).
        if self.module_type != "user":
            result["module_type"] = self.module_type
        if self.provides_services:
            result["provides_services"] = [
                {"name": s.name, "methods": s.methods, "description": s.description}
                for s in self.provides_services
            ]
        if self.consumes_services:
            result["consumes_services"] = self.consumes_services
        if self.emits_events:
            result["emits_events"] = self.emits_events
        if self.subscribes_events:
            result["subscribes_events"] = self.subscribes_events
        if self.config_schema is not None:
            result["config_schema"] = self.config_schema
        # Include v3 fields only when non-default.
        if self.resource_limits is not None:
            result["resource_limits"] = {
                "max_cpu_percent": self.resource_limits.max_cpu_percent,
                "max_memory_mb": self.resource_limits.max_memory_mb,
                "max_execution_seconds": self.resource_limits.max_execution_seconds,
                "max_concurrent_actions": self.resource_limits.max_concurrent_actions,
            }
        if self.sandbox_level != "none":
            result["sandbox_level"] = self.sandbox_level
        if self.license:
            result["license"] = self.license
        if self.optional_dependencies:
            result["optional_dependencies"] = self.optional_dependencies
        if self.module_dependencies:
            result["module_dependencies"] = self.module_dependencies
        if self.signing is not None:
            result["signing"] = {
                "public_key_fingerprint": self.signing.public_key_fingerprint,
                "signature_hex": self.signing.signature_hex,
                "signed_hash": self.signing.signed_hash,
                "signed_at": self.signing.signed_at,
            }
        if self.declared_capabilities:
            result["declared_capabilities"] = [
                c.to_dict() for c in self.declared_capabilities
            ]
        return result

    @staticmethod
    def _action_to_dict(a: ActionSpec) -> dict[str, Any]:
        """Serialise an ActionSpec to a dict, including v3 fields when non-default."""
        d: dict[str, Any] = {
            "name": a.name,
            "description": a.description,
            "params_schema": a.to_json_schema(),
            "returns": a.returns,
            "returns_description": a.returns_description,
            "permission_required": a.permission_required,
            "platforms": a.platforms,
            "examples": a.examples,
            "tags": a.tags,
            "permissions": a.permissions,
            "risk_level": a.risk_level,
            "irreversible": a.irreversible,
            "data_classification": a.data_classification,
        }
        # v3 fields — only when non-default.
        if a.output_schema is not None:
            d["output_schema"] = a.output_schema
        if a.side_effects:
            d["side_effects"] = a.side_effects
        if a.execution_mode != "async":
            d["execution_mode"] = a.execution_mode
        if a.capabilities:
            d["capabilities"] = [c.to_dict() for c in a.capabilities]
        if a.streams_progress:
            d["streams_progress"] = True
        return d
