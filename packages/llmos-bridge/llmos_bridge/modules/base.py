"""Module layer — BaseModule interface.

Every module — built-in or community — must subclass ``BaseModule`` and
implement ``execute()`` and ``get_manifest()``.

Design principles:
  - Modules are stateless between action calls where possible.
  - Modules that manage connections (database, browser) track sessions
    internally by session_id string key.
  - All errors are raised as ``ActionExecutionError``.
  - Modules declare their platform support upfront so the registry can
    gracefully degrade on unsupported platforms.
"""

from __future__ import annotations

import platform
from abc import ABC, abstractmethod  # abstractmethod kept for get_manifest
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from llmos_bridge.exceptions import ActionExecutionError, ActionNotFoundError
from llmos_bridge.modules.manifest import ModuleManifest


class Platform(str, Enum):
    LINUX = "linux"
    WINDOWS = "windows"
    MACOS = "macos"
    RASPBERRY_PI = "raspberry_pi"
    ALL = "all"


@dataclass
class ExecutionContext:
    """Contextual information passed to every module action call."""

    plan_id: str
    action_id: str
    session_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ActionResult:
    """Structured result returned by a module action.

    The ``output`` field is what gets stored in ``execution_results`` and
    returned to the LLM.  ``metadata`` is for internal bookkeeping.
    """

    success: bool
    output: Any = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "output": self.output,
            "error": self.error,
            "metadata": self.metadata,
        }


class BaseModule(ABC):
    """Abstract base class for all LLMOS Bridge modules.

    Subclasses must:
      1. Set ``MODULE_ID`` class attribute (snake_case, e.g. ``"filesystem"``)
      2. Set ``VERSION`` class attribute (semver string)
      3. Set ``SUPPORTED_PLATFORMS`` class attribute
      4. Implement :meth:`get_manifest`
      5. Implement ``_action_<name>`` methods for each declared action
      6. Optionally implement :meth:`_check_dependencies` (raise ``ModuleLoadError`` if not met)

    The :meth:`execute` method is **not** abstract — it provides a default dispatch
    implementation that routes ``action`` to the corresponding ``_action_<action>``
    method via naming convention.  Subclasses only need to override ``execute``
    when they require non-standard dispatch logic.
    """

    MODULE_ID: str = ""
    VERSION: str = "0.0.0"
    SUPPORTED_PLATFORMS: list[Platform] = [Platform.ALL]

    def __init__(self) -> None:
        self._check_dependencies()

    def is_supported_on_current_platform(self) -> bool:
        if Platform.ALL in self.SUPPORTED_PLATFORMS:
            return True
        current = platform.system().lower()
        mapping = {
            "linux": Platform.LINUX,
            "windows": Platform.WINDOWS,
            "darwin": Platform.MACOS,
        }
        current_platform = mapping.get(current)
        if current_platform is None:
            return False
        return current_platform in self.SUPPORTED_PLATFORMS

    @abstractmethod
    def get_manifest(self) -> ModuleManifest:
        """Return the Capability Manifest for this module.

        The manifest is used to:
          - Generate LangChain tools
          - Populate the /modules API endpoint
          - Validate params schemas
        """
        ...

    def _check_dependencies(self) -> None:
        """Raise ``ModuleLoadError`` if a required dependency is missing.

        Called in ``__init__``.  Default implementation does nothing.
        """

    def _get_handler(self, action: str) -> Any:
        """Look up an action handler method by name.

        Convention: action ``"read_file"`` maps to method ``_action_read_file``.
        """
        method_name = f"_action_{action}"
        handler = getattr(self, method_name, None)
        if handler is None:
            raise ActionNotFoundError(module_id=self.MODULE_ID, action=action)
        return handler

    async def execute(
        self, action: str, params: dict[str, Any], context: ExecutionContext | None = None
    ) -> Any:
        """Dispatch *action* to the corresponding ``_action_<action>`` method.

        Subclasses that need custom dispatch logic (e.g. stateful session
        management) may override this method.

        Args:
            action:  Action name (e.g. ``"read_file"``).
            params:  Already-resolved and schema-validated parameters.
            context: Optional execution context for tracing.

        Returns:
            Any JSON-serialisable value.  Will be sanitised by OutputSanitizer.

        Raises:
            ActionNotFoundError: If no ``_action_<action>`` method exists.
            ActionExecutionError: If the handler raises any unexpected exception.
        """
        handler = self._get_handler(action)
        try:
            return await handler(params)
        except (ActionNotFoundError, ActionExecutionError):
            raise
        except Exception as exc:
            raise ActionExecutionError(
                module_id=self.MODULE_ID, action=action, cause=exc
            ) from exc
