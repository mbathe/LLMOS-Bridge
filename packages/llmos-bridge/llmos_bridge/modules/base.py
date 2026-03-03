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

import functools
import platform
from abc import ABC, abstractmethod  # abstractmethod kept for get_manifest
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from typing import Callable

from llmos_bridge.exceptions import (
    ActionExecutionError,
    ActionNotFoundError,
    PermissionNotGrantedError,
    RateLimitExceededError,
)
from llmos_bridge.modules.manifest import ActionSpec, ModuleManifest


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


@dataclass
class ResourceEstimate:
    """Pre-execution cost estimation for an action.

    Used by the executor to schedule actions intelligently (e.g. avoid
    running multiple GPU-heavy vision parses simultaneously).
    """

    estimated_duration_seconds: float = 0.0
    estimated_memory_mb: float = 0.0
    estimated_cpu_percent: float = 0.0
    estimated_io_operations: int = 0
    confidence: float = 0.5  # 0.0 = wild guess, 1.0 = measured


@dataclass
class ModulePolicy:
    """Runtime policy constraints declared by a module.

    The PolicyEnforcer checks these constraints before dispatching
    actions to the module.
    """

    max_parallel_calls: int = 0  # 0 = unlimited
    cooldown_seconds: float = 0.0  # min seconds between calls
    allow_remote_invocation: bool = True
    execution_timeout: float = 0.0  # 0 = use system default
    max_memory_mb: int = 0  # 0 = unlimited
    retry_on_failure: bool = False


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
    MODULE_TYPE: str = "user"
    CONFIG_MODEL: type | None = None

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Auto-wrap ``get_manifest()`` to enrich with decorator metadata."""
        super().__init_subclass__(**kwargs)
        original = cls.__dict__.get("get_manifest")
        if original is not None and not getattr(original, "__isabstractmethod__", False):

            @functools.wraps(original)
            def _wrapped(self: "BaseModule", _orig: Any = original) -> "ModuleManifest":
                manifest = _orig(self)
                return self._enrich_manifest_metadata(manifest)

            cls.get_manifest = _wrapped  # type: ignore[assignment]

    def __init__(self) -> None:
        self._security: Any | None = None
        self._ctx: Any | None = None  # ModuleContext (set via set_context)
        self._dynamic_actions: dict[str, Callable[..., Any]] = {}
        self._dynamic_specs: dict[str, ActionSpec] = {}
        self._config: Any | None = None
        self._check_dependencies()

    def set_security(self, security: Any) -> None:
        """Inject the SecurityManager into this module.

        Called by the server startup after constructing the SecurityManager.
        Decorators on ``_action_*`` methods access it via ``self._security``.
        """
        self._security = security

    def _collect_security_metadata(self) -> dict[str, dict[str, Any]]:
        """Introspect decorated ``_action_*`` methods and return security metadata.

        Returns a dict keyed by action name (without the ``_action_`` prefix),
        with values from :func:`collect_security_metadata`.  Used to auto-enrich
        :class:`ActionSpec` entries in the manifest.
        """
        from llmos_bridge.security.decorators import collect_security_metadata

        result: dict[str, dict[str, Any]] = {}
        for attr_name in dir(self):
            if not attr_name.startswith("_action_"):
                continue
            handler = getattr(self, attr_name, None)
            if handler is None or not callable(handler):
                continue
            action_name = attr_name.removeprefix("_action_")
            meta = collect_security_metadata(handler)
            if meta:
                result[action_name] = meta
        return result

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

    def get_context_snippet(self) -> str | None:
        """Return dynamic context for the LLM system prompt, or ``None``.

        Modules that manage stateful resources (e.g. database connections)
        can override this to inject live context (schemas, session info)
        into the system prompt that guides the LLM.

        Default: ``None`` (no dynamic context).
        """
        return None

    # ------------------------------------------------------------------
    # Module Spec v2 — Context injection
    # ------------------------------------------------------------------

    def set_context(self, ctx: Any) -> None:
        """Inject the ModuleContext into this module.

        Called by the server startup after constructing the ServiceBus
        and LifecycleManager.  Provides structured access to inter-module
        communication, events, and system services.
        """
        self._ctx = ctx

    @property
    def ctx(self) -> Any | None:
        """Return the ModuleContext, or None if not yet injected."""
        return getattr(self, "_ctx", None)

    @property
    def config(self) -> Any | None:
        """Return the current validated config, or None if not configured."""
        return self._config

    def _collect_config_schema(self) -> dict[str, Any] | None:
        """Generate config_schema from CONFIG_MODEL if defined."""
        if self.CONFIG_MODEL is not None:
            return self.CONFIG_MODEL.to_config_schema()
        return None

    def _collect_streaming_metadata(self) -> dict[str, dict[str, Any]]:
        """Introspect decorated ``_action_*`` methods for streaming metadata.

        Returns a dict keyed by action name (without ``_action_`` prefix),
        with values from :func:`collect_streaming_metadata`.
        """
        from llmos_bridge.orchestration.streaming_decorators import collect_streaming_metadata

        result: dict[str, dict[str, Any]] = {}
        for attr_name in dir(self):
            if not attr_name.startswith("_action_"):
                continue
            handler = getattr(self, attr_name, None)
            if handler is None or not callable(handler):
                continue
            action_name = attr_name.removeprefix("_action_")
            meta = collect_streaming_metadata(handler)
            if meta:
                result[action_name] = meta
        return result

    # ------------------------------------------------------------------
    # Manifest auto-enrichment
    # ------------------------------------------------------------------

    _SECURITY_SPEC_KEYS = frozenset(
        {"permissions", "risk_level", "irreversible", "data_classification"}
    )

    # Keywords in permission strings that indicate higher risk levels.
    _HIGH_RISK_KEYWORDS = frozenset(
        {"delete", "kill", "admin", "credentials", "personal", "actuator"}
    )
    _MEDIUM_RISK_KEYWORDS = frozenset(
        {"write", "execute", "send", "external", "screen", "camera",
         "microphone", "keyboard", "browser", "gpio.write"}
    )

    def _enrich_manifest_metadata(self, manifest: ModuleManifest) -> ModuleManifest:
        """Auto-enrich manifest actions with security + streaming decorator metadata.

        Called automatically via ``__init_subclass__`` wrapping of ``get_manifest()``.
        Only fills fields that are still at their default (empty/falsy) values, so
        modules that already set them explicitly in ``get_manifest()`` are unaffected.
        """
        security_meta = self._collect_security_metadata()
        streaming_meta = self._collect_streaming_metadata()

        for action in manifest.actions:
            # Security decorator metadata → ActionSpec
            if action.name in security_meta:
                meta = security_meta[action.name]
                for key in self._SECURITY_SPEC_KEYS:
                    if key in meta and not getattr(action, key, None):
                        setattr(action, key, meta[key])

            # Infer risk_level from permissions when not explicitly set
            if not action.risk_level and action.permissions:
                action.risk_level = self._infer_risk_from_permissions(
                    action.permissions
                )

            # Fallback: still no risk_level → "low" (info / read-only)
            if not action.risk_level:
                action.risk_level = "low"

            # Streaming decorator metadata → ActionSpec
            if action.name in streaming_meta:
                meta = streaming_meta[action.name]
                if meta.get("streams_progress") and not action.streams_progress:
                    action.streams_progress = True

        return manifest

    @classmethod
    def _infer_risk_from_permissions(cls, permissions: list[str]) -> str:
        """Infer a risk level from permission strings when no explicit level is set."""
        joined = " ".join(permissions).lower()
        if any(kw in joined for kw in cls._HIGH_RISK_KEYWORDS):
            return "high"
        if any(kw in joined for kw in cls._MEDIUM_RISK_KEYWORDS):
            return "medium"
        return "low"

    # ------------------------------------------------------------------
    # Module Spec v2 — Lifecycle hooks (all default no-op)
    # ------------------------------------------------------------------

    async def on_start(self) -> None:
        """Called when the module transitions to ACTIVE state.

        Override to initialise connections, load models, etc.
        """

    async def on_stop(self) -> None:
        """Called when the module is being stopped/disabled.

        Override to close connections, save state, release resources.
        """

    async def on_pause(self) -> None:
        """Called when the module is paused (ACTIVE → PAUSED).

        Override to suspend background tasks or release non-critical resources.
        """

    async def on_resume(self) -> None:
        """Called when the module resumes (PAUSED → ACTIVE).

        Override to re-acquire resources suspended during pause.
        """

    async def on_config_update(self, config: dict[str, Any]) -> None:
        """Called when module configuration is updated at runtime.

        If CONFIG_MODEL is set, validates the incoming dict against the
        Pydantic model before storing. Subclasses can override to add
        custom logic after validation.
        """
        if self.CONFIG_MODEL is not None:
            self._config = self.CONFIG_MODEL.model_validate(config)

    # ------------------------------------------------------------------
    # Module Spec v2 — Introspection
    # ------------------------------------------------------------------

    async def health_check(self) -> dict[str, Any]:
        """Return health status of this module.

        Override to add connectivity checks, model load status, etc.
        """
        return {
            "status": "ok",
            "module_id": self.MODULE_ID,
            "version": self.VERSION,
        }

    def metrics(self) -> dict[str, Any]:
        """Return operational metrics for this module.

        Override to expose action counts, latencies, cache hit rates, etc.
        """
        return {}

    def state_snapshot(self) -> dict[str, Any]:
        """Return a snapshot of this module's internal state.

        Override to expose active sessions, loaded models, connections, etc.
        """
        return {}

    # ------------------------------------------------------------------
    # Module Spec v2 — Service registration
    # ------------------------------------------------------------------

    def register_services(self) -> list[Any]:
        """Return ServiceDescriptor instances for services this module provides.

        Override to declare services on the ServiceBus during startup.
        Default: no services provided.
        """
        return []

    # ------------------------------------------------------------------
    # Module Spec v3 — Event handling
    # ------------------------------------------------------------------

    async def on_event(self, topic: str, event: dict[str, Any]) -> None:
        """Called when an event is emitted on a topic this module subscribes to.

        Modules declare subscribed topics via ``subscribes_events`` in their
        manifest.  The lifecycle manager auto-subscribes modules on start and
        auto-unsubscribes on stop.

        Override to react to system events (e.g. react to security events,
        perception changes, other module state changes).
        """

    # ------------------------------------------------------------------
    # Module Spec v3 — State recovery
    # ------------------------------------------------------------------

    async def restore_state(self, state: dict[str, Any]) -> None:
        """Restore module state after crash/restart.

        Receives the dict previously returned by :meth:`state_snapshot`.
        Override to restore connections, sessions, loaded models, etc.
        """

    # ------------------------------------------------------------------
    # Module Spec v3 — Installation hooks
    # ------------------------------------------------------------------

    async def on_install(self) -> None:
        """Called when module is first installed from the hub.

        Override to perform one-time setup (download models, create DB tables).
        """

    async def on_update(self, old_version: str) -> None:
        """Called when module is upgraded to a new version.

        Override to perform migrations between versions.
        """

    # ------------------------------------------------------------------
    # Module Spec v3 — Resource awareness
    # ------------------------------------------------------------------

    async def on_resource_pressure(self, level: str) -> None:
        """Called when system detects memory/CPU pressure.

        Args:
            level: ``"warning"`` (>75% usage) or ``"critical"`` (>90%).

        Override to release caches, unload models, close idle connections.
        """

    async def estimate_cost(
        self, action: str, params: dict[str, Any]
    ) -> ResourceEstimate:
        """Pre-execution cost estimation for the given action.

        Override to provide module-specific estimates.
        Default: returns a generic low-confidence estimate.
        """
        return ResourceEstimate()

    # ------------------------------------------------------------------
    # Module Spec v3 — Policy and description
    # ------------------------------------------------------------------

    def policy_rules(self) -> ModulePolicy:
        """Declare runtime policy constraints for this module.

        Override to set max_parallel_calls, cooldowns, etc.
        Default: no constraints.
        """
        return ModulePolicy()

    def describe(self) -> dict[str, Any]:
        """Dynamic self-description for LLM introspection.

        Beyond the static manifest, this provides live context:
        loaded models, active connections, available capabilities
        based on current state.

        Default: returns the manifest as a dict.
        """
        return self.get_manifest().to_dict()

    # ------------------------------------------------------------------
    # Module Spec v3 — Dynamic action registration
    # ------------------------------------------------------------------

    def register_action(
        self,
        name: str,
        handler: Callable[..., Any],
        spec: ActionSpec | None = None,
    ) -> None:
        """Register a dynamic action at runtime.

        The handler must have signature: ``async (params: dict) -> Any``.
        Dynamic actions take precedence over ``_action_`` methods.
        """
        self._dynamic_actions[name] = handler
        if spec is not None:
            self._dynamic_specs[name] = spec

    def unregister_action(self, name: str) -> None:
        """Remove a dynamically registered action."""
        self._dynamic_actions.pop(name, None)
        self._dynamic_specs.pop(name, None)

    # ------------------------------------------------------------------
    # Action dispatch
    # ------------------------------------------------------------------

    def _get_handler(self, action: str) -> Any:
        """Look up an action handler method by name.

        Checks dynamic actions first, then falls back to the
        ``_action_<action>`` naming convention.
        """
        # Dynamic actions take precedence
        if action in self._dynamic_actions:
            return self._dynamic_actions[action]

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
        except (
            ActionNotFoundError,
            ActionExecutionError,
            PermissionNotGrantedError,
            RateLimitExceededError,
        ):
            raise
        except Exception as exc:
            raise ActionExecutionError(
                module_id=self.MODULE_ID, action=action, cause=exc
            ) from exc
