"""LLMOS Bridge — Exception hierarchy.

All exceptions raised by the daemon inherit from LLMOSError so that callers
can catch the full family with a single except clause when needed.

Hierarchy:
    LLMOSError
    ├── ProtocolError
    │   ├── IMLParseError
    │   ├── IMLValidationError
    │   └── TemplateResolutionError
    ├── SecurityError
    │   ├── PermissionDeniedError
    │   ├── ApprovalRequiredError
    │   └── SanitizationError
    ├── OrchestrationError
    │   ├── DAGCycleError
    │   ├── DependencyError
    │   └── ExecutionTimeoutError
    ├── ModuleError
    │   ├── ModuleNotFoundError
    │   ├── ActionNotFoundError
    │   ├── ModuleLoadError
    │   └── ActionExecutionError
    ├── PerceptionError
    │   ├── ScreenCaptureError
    │   └── OCRError
    └── MemoryError
        ├── StateStoreError
        └── VectorStoreError
"""

from __future__ import annotations

from typing import Any


class LLMOSError(Exception):
    """Base exception for all LLMOS Bridge errors."""

    def __init__(self, message: str, context: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.context: dict[str, Any] = context or {}

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.message!r}, context={self.context})"


# ---------------------------------------------------------------------------
# Protocol layer
# ---------------------------------------------------------------------------


class ProtocolError(LLMOSError):
    """Base for all IML protocol errors."""


class IMLParseError(ProtocolError):
    """The incoming payload could not be parsed as valid JSON or IML structure."""

    def __init__(self, message: str, raw_payload: str | None = None) -> None:
        super().__init__(message, context={"raw_payload": raw_payload})
        self.raw_payload = raw_payload


class IMLValidationError(ProtocolError):
    """The IML plan failed Pydantic validation."""

    def __init__(self, message: str, errors: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message, context={"validation_errors": errors or []})
        self.errors = errors or []


class TemplateResolutionError(ProtocolError):
    """A ``{{result.X.Y}}`` template reference could not be resolved."""

    def __init__(self, template: str, reason: str) -> None:
        super().__init__(
            f"Cannot resolve template '{template}': {reason}",
            context={"template": template, "reason": reason},
        )
        self.template = template


# ---------------------------------------------------------------------------
# Security layer
# ---------------------------------------------------------------------------


class SecurityError(LLMOSError):
    """Base for all security-related errors."""


class PermissionDeniedError(SecurityError):
    """The active permission profile does not allow this action."""

    def __init__(self, action: str, module: str, profile: str) -> None:
        super().__init__(
            f"Permission denied: action '{module}.{action}' is not allowed "
            f"under profile '{profile}'",
            context={"action": action, "module": module, "profile": profile},
        )
        self.action = action
        self.module = module
        self.profile = profile


class ApprovalRequiredError(SecurityError):
    """The action requires explicit user approval before execution."""

    def __init__(self, action_id: str, plan_id: str) -> None:
        super().__init__(
            f"Action '{action_id}' in plan '{plan_id}' requires user approval",
            context={"action_id": action_id, "plan_id": plan_id},
        )
        self.action_id = action_id
        self.plan_id = plan_id


class SanitizationError(SecurityError):
    """An output sanitisation rule was violated."""


# ---------------------------------------------------------------------------
# Orchestration layer
# ---------------------------------------------------------------------------


class OrchestrationError(LLMOSError):
    """Base for all orchestration errors."""


class DAGCycleError(OrchestrationError):
    """The dependency graph contains a cycle."""

    def __init__(self, cycle: list[str]) -> None:
        super().__init__(
            f"Dependency cycle detected: {' -> '.join(cycle)}",
            context={"cycle": cycle},
        )
        self.cycle = cycle


class DependencyError(OrchestrationError):
    """A required dependency failed or was not found."""

    def __init__(self, action_id: str, dep_id: str, reason: str) -> None:
        super().__init__(
            f"Action '{action_id}' dependency '{dep_id}' not satisfied: {reason}",
            context={"action_id": action_id, "dep_id": dep_id, "reason": reason},
        )


class ExecutionTimeoutError(OrchestrationError):
    """An action exceeded its configured timeout."""

    def __init__(self, action_id: str, timeout_seconds: int) -> None:
        super().__init__(
            f"Action '{action_id}' timed out after {timeout_seconds}s",
            context={"action_id": action_id, "timeout_seconds": timeout_seconds},
        )
        self.action_id = action_id
        self.timeout_seconds = timeout_seconds


# ---------------------------------------------------------------------------
# Module layer
# ---------------------------------------------------------------------------


class ModuleError(LLMOSError):
    """Base for all module errors."""


class ModuleNotFoundError(ModuleError):
    """No module with the given ID is registered."""

    def __init__(self, module_id: str) -> None:
        super().__init__(
            f"Module '{module_id}' is not registered",
            context={"module_id": module_id},
        )
        self.module_id = module_id


class ActionNotFoundError(ModuleError):
    """The module does not expose the requested action."""

    def __init__(self, module_id: str, action: str) -> None:
        super().__init__(
            f"Module '{module_id}' does not expose action '{action}'",
            context={"module_id": module_id, "action": action},
        )
        self.module_id = module_id
        self.action = action


class ModuleLoadError(ModuleError):
    """A module failed to initialise (missing dependency, wrong platform, etc.)."""

    def __init__(self, module_id: str, reason: str) -> None:
        super().__init__(
            f"Module '{module_id}' failed to load: {reason}",
            context={"module_id": module_id, "reason": reason},
        )


class ActionExecutionError(ModuleError):
    """An action raised an unexpected error during execution."""

    def __init__(self, module_id: str, action: str, cause: Exception) -> None:
        super().__init__(
            f"Action '{module_id}.{action}' failed: {cause}",
            context={"module_id": module_id, "action": action, "cause": str(cause)},
        )
        self.cause = cause


# ---------------------------------------------------------------------------
# Perception layer
# ---------------------------------------------------------------------------


class PerceptionError(LLMOSError):
    """Base for all perception errors."""


class ScreenCaptureError(PerceptionError):
    """Failed to capture a screenshot."""


class OCRError(PerceptionError):
    """Failed to perform OCR on a captured image."""


# ---------------------------------------------------------------------------
# Memory layer
# ---------------------------------------------------------------------------


class MemoryError(LLMOSError):
    """Base for all memory errors."""


class StateStoreError(MemoryError):
    """SQLite state store operation failed."""


class VectorStoreError(MemoryError):
    """ChromaDB vector store operation failed."""
