"""DaemonToolExecutor — Routes app tool calls through the daemon infrastructure.

This is the single integration point between the App Language (apps/) and the
daemon's module registry, security pipeline, and event bus.

Full pipeline:
    1. Rate limiting (ActionRateLimiter — per-action sliding window)
    2. Intent verification (IntentVerifier — LLM-based per-action security)
    3. Authorization guard (identity-based RBAC — app/session allowlists)
    4. Tool constraints check (YAML-level paths, forbidden commands, etc.)
    5. Capabilities check (app-level grant/deny with when: expressions)
    6. Approval rules (with when: expressions + count-based triggers)
    7. Scanner pipeline (HeuristicScanner, LLMGuard, PromptGuard)
    8. Permission guard (profile-based allowlist + sandbox)
    9. Module execution with ExecutionContext, per-tool timeout + retry
    10. Perception capture (screenshot/OCR before/after tool calls)
    11. Output sanitization (prompt injection defense)
    12. Audit enforcement (level, redaction, notifications)
    13. EventBus audit trail
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from llmos_bridge.events.bus import EventBus
    from llmos_bridge.identity.authorization import AuthorizationGuard
    from llmos_bridge.identity.models import IdentityContext
    from llmos_bridge.identity.store import IdentityStore
    from llmos_bridge.modules.registry import ModuleRegistry
    from llmos_bridge.orchestration.approval import ApprovalGate
    from llmos_bridge.security.guard import PermissionGuard
    from llmos_bridge.security.intent_verifier import IntentVerifier
    from llmos_bridge.security.rate_limiter import ActionRateLimiter
    from llmos_bridge.security.sanitizer import OutputSanitizer
    from llmos_bridge.security.scanners.pipeline import SecurityPipeline
    from llmos_bridge.apps.models import (
        AuditConfig,
        CapabilitiesConfig,
        PerceptionAppConfig,
    )

# ── Per-request execution context (concurrency-safe) ────────────────
# All per-app mutable state lives here, NOT on DaemonToolExecutor instance.
# This prevents concurrent app runs from overwriting each other's security
# settings — the #1 critical scalability bug.

from dataclasses import dataclass, field as dc_field


@dataclass
class _ExecutionScope:
    """Per-request mutable state for a single app execution.

    Stored in a ContextVar so concurrent async tasks each get their own copy.
    """
    identity: IdentityContext | None = None
    capabilities: CapabilitiesConfig | None = None
    perception: PerceptionAppConfig | None = None
    audit: AuditConfig | None = None
    tool_constraints: dict[str, dict[str, Any]] = dc_field(default_factory=dict)
    action_counts: dict[str, int] = dc_field(default_factory=dict)
    security_profile: str | None = None
    sandbox_paths: list[str] = dc_field(default_factory=list)
    sandbox_commands: list[str] = dc_field(default_factory=list)
    run_id: str = ""  # Unique ID for this app run (for approval tracking)
    # Cached identity objects for AuthorizationGuard (set by API routes)
    _cached_app: Any = None
    _cached_session: Any = None


_current_scope: contextvars.ContextVar[_ExecutionScope] = contextvars.ContextVar(
    "_current_scope", default=None,  # type: ignore[arg-type]
)

logger = logging.getLogger(__name__)


def set_identity_context(identity: IdentityContext | None) -> None:
    """Set the identity context for the current async call chain."""
    scope = _current_scope.get()
    if scope is None:
        scope = _ExecutionScope(identity=identity)
        _current_scope.set(scope)
    else:
        scope.identity = identity


def get_identity_context() -> IdentityContext | None:
    """Get the identity context for the current async call chain."""
    scope = _current_scope.get()
    return scope.identity if scope else None


def _get_scope() -> _ExecutionScope:
    """Get or create the execution scope for the current async context."""
    scope = _current_scope.get()
    if scope is None:
        scope = _ExecutionScope()
        _current_scope.set(scope)
    return scope


class DaemonToolExecutor:
    """Routes app tool calls through the daemon's full security + module pipeline.

    Integrates:
    - ModuleRegistry (all 18+ modules + community)
    - PermissionGuard (security profiles)
    - SecurityPipeline (HeuristicScanner, LLMGuard, PromptGuard)
    - OutputSanitizer (prompt injection defense on output)
    - EventBus (audit trail)
    - App-level capabilities (grant/deny/approval from YAML with when: expressions)
    - ToolConstraints enforcement (paths, forbidden commands, etc.)
    - Perception capture (screenshot/OCR before/after tool calls)
    - Audit config enforcement (level, redaction, notifications)
    - Multi-node routing (preferred_node)
    """

    def __init__(
        self,
        module_registry: ModuleRegistry,
        permission_guard: PermissionGuard | None = None,
        sanitizer: OutputSanitizer | None = None,
        event_bus: EventBus | None = None,
        scanner_pipeline: SecurityPipeline | None = None,
        capabilities: CapabilitiesConfig | None = None,
        node_registry: Any | None = None,
        routing_config: Any | None = None,
        expression_engine: Any | None = None,
        perception_config: PerceptionAppConfig | None = None,
        param_models: dict[str, dict[str, type]] | None = None,
        # IML security integrations
        rate_limiter: ActionRateLimiter | None = None,
        intent_verifier: IntentVerifier | None = None,
        authorization_guard: AuthorizationGuard | None = None,
        identity_store: IdentityStore | None = None,
    ):
        self._registry = module_registry
        self._guard = permission_guard
        self._sanitizer = sanitizer
        self._event_bus = event_bus
        self._scanner = scanner_pipeline
        self._node_registry = node_registry
        self._routing_config = routing_config
        self._expr = expression_engine
        self._param_models = param_models  # ALL_PARAMS: module_id -> {action -> PydanticModel}
        # IML security subsystems
        self._rate_limiter = rate_limiter
        self._intent_verifier = intent_verifier
        self._authorization_guard = authorization_guard
        self._identity_store = identity_store
        # Approval gate — if provided, approval_required rules BLOCK and wait
        # for a human decision instead of returning an immediate error.
        self._approval_gate: ApprovalGate | None = None
        # Per-request state lives in _ExecutionScope (ContextVar) to prevent
        # concurrent app runs from overwriting each other's settings.
        # If capabilities/perception are passed to __init__, seed a default scope
        # for single-app or test usage (will be overridden per-request).
        if capabilities or perception_config:
            scope = _get_scope()
            if capabilities:
                scope.capabilities = capabilities
                scope.audit = capabilities.audit if capabilities else None
            if perception_config:
                scope.perception = perception_config

    # ── Per-request state (concurrency-safe via ContextVar) ──────

    def set_capabilities(self, capabilities: CapabilitiesConfig) -> None:
        """Update the capabilities config (called per-app run). Concurrency-safe."""
        scope = _get_scope()
        scope.capabilities = capabilities
        scope.action_counts.clear()
        scope.audit = capabilities.audit if capabilities else None

    def set_approval_gate(self, gate: ApprovalGate) -> None:
        """Wire an ApprovalGate for blocking approval_required rules."""
        self._approval_gate = gate

    def set_metrics_collector(self, collector: Any) -> None:
        """Wire a MetricsCollector for per-action metric tracking."""
        self._metrics_collector = collector

    def set_perception(self, config: PerceptionAppConfig) -> None:
        """Update the perception config (called per-app run). Concurrency-safe."""
        _get_scope().perception = config

    def set_tool_constraints(self, constraints: dict[str, dict[str, Any]]) -> None:
        """Set tool constraints keyed by 'module.action'. Concurrency-safe."""
        _get_scope().tool_constraints = constraints

    def set_security_profile(self, profile: str) -> None:
        """Set the permission profile for this app run. Concurrency-safe."""
        _get_scope().security_profile = profile

    def set_sandbox(
        self, allowed_paths: list[str] | None = None, blocked_commands: list[str] | None = None
    ) -> None:
        """Apply sandbox constraints from the security: block. Concurrency-safe."""
        scope = _get_scope()
        scope.sandbox_paths = allowed_paths or []
        scope.sandbox_commands = blocked_commands or []

    def _validate_params(
        self, module_id: str, action: str, params: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Pre-validate params against Pydantic models if available.

        Returns validated (coerced) params dict, or None if no model available.
        Raises ValueError with a clear message on validation failure.
        """
        if self._param_models is None:
            return None
        module_params = self._param_models.get(module_id)
        if module_params is None:
            return None
        model_cls = module_params.get(action)
        if model_cls is None:
            return None
        try:
            validated = model_cls.model_validate(params)
            return validated.model_dump()
        except Exception as e:
            raise ValueError(
                f"Invalid parameters for {module_id}.{action}: {e}"
            ) from e

    async def execute(
        self, module_id: str, action: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Execute a module action through the full daemon pipeline.

        This callback has the same signature as StandaloneToolExecutor.execute()
        so it can be passed directly to AppRuntime(execute_tool=...).
        """
        start = time.monotonic()
        success = False
        error_msg = ""
        action_key = f"{module_id}.{action}"

        try:
            # 1. Check module exists
            try:
                module = self._registry.get(module_id)
            except Exception:
                return {"error": f"Module '{module_id}' not available"}

            # 1b. Pre-validate params against Pydantic models (early error feedback)
            validated_params = self._validate_params(module_id, action, params)
            if validated_params is not None:
                params = validated_params

            # 2. Rate limiting (IML ActionRateLimiter — per-action sliding window)
            rate_error = self._check_rate_limit(module_id, action)
            if rate_error:
                return {"error": rate_error}

            # 3. Intent verification (IML IntentVerifier — LLM-based per-action)
            intent_error = await self._check_intent(module_id, action, params)
            if intent_error:
                return {"error": intent_error}

            # 4. Authorization guard (IML identity-based RBAC)
            auth_error = self._check_authorization(module_id, action)
            if auth_error:
                return {"error": auth_error}

            # 5. Tool constraints check (YAML-level paths, forbidden commands, etc.)
            constraint_error = self._check_tool_constraints(module_id, action, params)
            if constraint_error:
                return {"error": constraint_error}

            # 5b. Sandbox constraints from security: block
            sandbox_error = self._check_sandbox(module_id, action, params)
            if sandbox_error:
                return {"error": sandbox_error}

            # 6. App-level capabilities check (YAML grant/deny with when: expressions)
            cap_error = self._check_capabilities(module_id, action, params)
            if cap_error:
                return {"error": cap_error}

            # 7. App-level approval rules check (with when: expressions)
            approval_msg = self._check_approval_required(module_id, action, params)
            if approval_msg:
                approval_result = await self._handle_approval(
                    module_id, action, params, approval_msg
                )
                if approval_result is not None:
                    return approval_result

            # 8. Scanner pipeline (HeuristicScanner, PromptGuard, etc.)
            scan_error = await self._scan_params(module_id, action, params)
            if scan_error:
                return {"error": scan_error}

            # 9. Permission guard (daemon-level security profile)
            if self._guard is not None:
                if not self._guard.is_allowed(module_id, action):
                    from llmos_bridge.security.guard import PermissionDeniedError
                    raise PermissionDeniedError(
                        action=action,
                        module=module_id,
                        profile=self._guard._profile.profile.value,
                    )
                self._guard.check_sandbox_params(module_id, action, params)

            # 10. Perception capture: before
            await self._capture_perception(module_id, action, "before")

            # 11. Build ExecutionContext for module (IML tracing)
            exec_ctx = self._build_execution_context(module_id, action)

            # 12. Execute with per-tool timeout + retry
            result = await self._execute_with_timeout_retry(
                module, module_id, action, params, exec_ctx,
            )

            # 13. Perception capture: after
            await self._capture_perception(module_id, action, "after")

            # 14. Sanitize output
            if self._sanitizer is not None:
                result = self._sanitizer.sanitize(result, module=module_id, action=action)

            # 15. Enforce max_response_size constraint
            scope = _current_scope.get()
            tool_constraints = scope.tool_constraints if scope else {}
            constraints = tool_constraints.get(action_key, {})
            max_response_size = constraints.get("max_response_size", "")
            if max_response_size:
                result_str = json.dumps(result, default=str)
                result_size = len(result_str.encode("utf-8", errors="replace"))
                limit_bytes = _parse_size(max_response_size)
                if limit_bytes > 0 and result_size > limit_bytes:
                    logger.warning(
                        "Response size %d exceeds max_response_size %s for %s",
                        result_size, max_response_size, action_key,
                    )
                    result = {
                        "error": f"Response size ({result_size} bytes) exceeds "
                        f"max_response_size ({max_response_size})",
                        "truncated": True,
                    }

            success = True

            # Track action count for trigger-based approval rules (per-request scope)
            if scope is not None:
                scope.action_counts[action_key] = scope.action_counts.get(action_key, 0) + 1

            # Normalize to dict
            if not isinstance(result, dict):
                result = {"result": result}

            return result

        except Exception as e:
            error_msg = str(e)
            logger.exception(
                "daemon_tool_execute_failed: %s.%s", module_id, action,
            )
            return {"error": f"{type(e).__name__}: {e}"}

        finally:
            # 15. Audit enforcement + EventBus
            await self._emit_audit(module_id, action, params, success, error_msg, start)

    # ── IML Security: Rate Limiting ──────────────────────────────

    def _check_rate_limit(
        self, module_id: str, action: str
    ) -> str | None:
        """Check per-action rate limits via IML ActionRateLimiter."""
        if self._rate_limiter is None:
            return None

        action_key = f"{module_id}.{action}"

        # Get rate limit config from YAML tool constraints if available
        scope = _current_scope.get()
        tool_constraints = scope.tool_constraints if scope else {}
        constraints = tool_constraints.get(action_key, {})
        calls_per_minute = constraints.get("rate_limit_per_minute")
        calls_per_hour = constraints.get("rate_limit_per_hour")

        # If no per-tool rate limit configured, skip (don't block by default)
        if calls_per_minute is None and calls_per_hour is None:
            return None

        try:
            self._rate_limiter.check_or_raise(
                action_key,
                calls_per_minute=calls_per_minute,
                calls_per_hour=calls_per_hour,
            )
        except Exception as e:
            return f"RateLimitExceeded: {e}"

        return None

    # ── IML Security: Intent Verification ────────────────────────

    async def _check_intent(
        self, module_id: str, action: str, params: dict[str, Any]
    ) -> str | None:
        """Run LLM-based intent verification on this action (IML IntentVerifier)."""
        if self._intent_verifier is None or not self._intent_verifier.enabled:
            return None

        # Build a lightweight IMLAction-like object for verify_action
        try:
            from llmos_bridge.protocol.models import IMLAction

            iml_action = IMLAction(
                id=f"app-{module_id}-{action}-{int(time.time())}",
                action=action,
                module=module_id,
                params=params,
            )

            scope = _current_scope.get()
            identity = scope.identity if scope else None
            plan_desc = f"App tool call: {module_id}.{action}"
            if identity and identity.app_id:
                plan_desc = f"App({identity.app_id}): {module_id}.{action}"

            result = await self._intent_verifier.verify_action(
                iml_action,
                plan_id=f"app-{int(time.time())}",
                plan_description=plan_desc,
            )

            if not result.is_safe():
                logger.warning(
                    "app_intent_verification_rejected: %s.%s verdict=%s",
                    module_id, action, result.verdict.value,
                )
                return (
                    f"IntentVerificationFailed: {result.reasoning} "
                    f"(verdict={result.verdict.value}, risk={result.risk_level})"
                )
        except Exception as e:
            logger.warning("app_intent_verification_error: %s.%s — %s", module_id, action, e)
            # In non-strict mode, let it pass on error
            if self._intent_verifier.strict:
                return f"IntentVerificationError: {e}"

        return None

    # ── IML Security: Authorization Guard ────────────────────────

    def _check_authorization(
        self, module_id: str, action: str
    ) -> str | None:
        """Check identity-based RBAC via IML AuthorizationGuard."""
        if self._authorization_guard is None:
            return None

        scope = _current_scope.get()
        identity = scope.identity if scope else None
        if identity is None or not identity.app_id:
            return None  # No identity context — skip (anonymous access)

        try:
            # AuthorizationGuard.check_action_allowed needs an Application object
            # We cache it on the scope to avoid repeated DB lookups
            app_obj = getattr(scope, '_cached_app', None)
            if app_obj is None and self._identity_store is not None:
                # Synchronous lookup not possible here — use cached value only
                # The API route should pre-cache this on the scope
                pass

            if app_obj is not None:
                session_obj = getattr(scope, '_cached_session', None)
                self._authorization_guard.check_action_allowed(
                    app_obj, module_id, action, session=session_obj,
                )
        except Exception as e:
            return f"AuthorizationDenied: {e}"

        return None

    # ── ExecutionContext builder ──────────────────────────────────

    def _build_execution_context(
        self, module_id: str, action: str
    ) -> Any:
        """Build an ExecutionContext for module.execute() (IML tracing)."""
        from llmos_bridge.modules.base import ExecutionContext

        scope = _current_scope.get()
        identity = scope.identity if scope else None

        return ExecutionContext(
            plan_id=f"app-{int(time.time() * 1000)}",
            action_id=f"{module_id}.{action}",
            session_id=identity.session_id if identity else None,
            extra={
                "source": "yaml_app",
                "app_id": identity.app_id if identity else None,
                "agent_id": identity.agent_id if identity else None,
            },
        )

    # ── Per-tool timeout + retry ─────────────────────────────────

    async def _execute_with_timeout_retry(
        self,
        module: Any,
        module_id: str,
        action: str,
        params: dict[str, Any],
        exec_ctx: Any,
    ) -> Any:
        """Execute module action with per-tool timeout and retry from YAML constraints."""
        scope = _current_scope.get()
        tool_constraints = scope.tool_constraints if scope else {}
        constraints = tool_constraints.get(f"{module_id}.{action}", {})

        # Parse per-tool timeout (from YAML ToolConstraints.timeout)
        timeout_str = constraints.get("timeout", "")
        timeout = _parse_duration(timeout_str) if timeout_str else 0

        # Parse per-tool retry config
        max_retries = constraints.get("max_retries", 0)
        retry_backoff = constraints.get("retry_backoff", "exponential")

        last_error: Exception | None = None
        for attempt in range(max(1, max_retries + 1)):
            if attempt > 0:
                # Backoff between retries
                if retry_backoff == "exponential":
                    delay = min(2 ** attempt, 30)
                elif retry_backoff == "linear":
                    delay = attempt * 2
                else:  # fixed
                    delay = 2
                await asyncio.sleep(delay)
                logger.info(
                    "app_tool_retry: %s.%s attempt=%d/%d",
                    module_id, action, attempt + 1, max_retries + 1,
                )

            try:
                coro = self._dispatch(module, module_id, action, params, exec_ctx)
                if timeout > 0:
                    return await asyncio.wait_for(coro, timeout=timeout)
                return await coro
            except asyncio.TimeoutError:
                last_error = asyncio.TimeoutError(
                    f"{module_id}.{action} timed out after {timeout}s"
                )
                if attempt >= max_retries:
                    raise last_error
            except Exception as e:
                last_error = e
                if attempt >= max_retries:
                    raise

        raise last_error  # Should never reach here

    # ── Tool constraints enforcement ─────────────────────────────

    def _check_sandbox(
        self, module_id: str, action: str, params: dict[str, Any]
    ) -> str | None:
        """Enforce sandbox constraints from the security: YAML block."""
        scope = _current_scope.get()
        sandbox_paths = scope.sandbox_paths if scope else []
        sandbox_commands = scope.sandbox_commands if scope else []
        # Check allowed paths (applies to filesystem and os_exec working_directory)
        if sandbox_paths:
            param_path = params.get("path", params.get("directory", params.get("working_directory", "")))
            if param_path:
                resolved = str(Path(param_path).resolve())
                in_allowed = any(
                    resolved.startswith(str(Path(p).resolve()))
                    for p in sandbox_paths
                )
                if not in_allowed:
                    return f"Path '{param_path}' outside sandbox (allowed: {sandbox_paths})"

        # Check blocked commands
        if sandbox_commands:
            cmd = params.get("command", "")
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
            for blocked in sandbox_commands:
                if blocked in cmd_str:
                    return f"Command blocked by sandbox: '{blocked}'"

        return None

    def _check_tool_constraints(
        self, module_id: str, action: str, params: dict[str, Any]
    ) -> str | None:
        """Enforce ToolConstraints defined in YAML tools block."""
        scope = _current_scope.get()
        tool_constraints = scope.tool_constraints if scope else {}
        key = f"{module_id}.{action}"
        constraints = tool_constraints.get(key)
        if not constraints:
            return None

        # Path restrictions
        allowed_paths = constraints.get("paths", [])
        if allowed_paths:
            param_path = params.get("path", params.get("directory", params.get("working_directory", "")))
            if param_path:
                resolved = str(Path(param_path).resolve())
                in_allowed = any(
                    resolved.startswith(str(Path(p).resolve()))
                    for p in allowed_paths
                )
                if not in_allowed:
                    return f"Path '{param_path}' not in allowed paths for {key}"

        # Forbidden commands
        forbidden_cmds = constraints.get("forbidden_commands", [])
        if forbidden_cmds:
            cmd = params.get("command", "")
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
            for forbidden in forbidden_cmds:
                if forbidden in cmd_str:
                    return f"Command contains forbidden pattern '{forbidden}' for {key}"

        # Forbidden patterns (regex) — only serializes params when patterns exist
        forbidden_patterns = constraints.get("forbidden_patterns", [])
        if forbidden_patterns:
            # Use pre-compiled patterns if available (cached on first use)
            compiled_key = f"_compiled_{key}"
            compiled = constraints.get(compiled_key)
            if compiled is None:
                compiled = [re.compile(p) for p in forbidden_patterns]
                constraints[compiled_key] = compiled
            params_str = json.dumps(params, default=str)
            for i, compiled_re in enumerate(compiled):
                if compiled_re.search(params_str):
                    return f"Params match forbidden pattern '{forbidden_patterns[i]}' for {key}"

        # Read-only mode
        if constraints.get("read_only"):
            write_actions = {"write_file", "create_file", "delete_file", "move_file",
                             "run_command", "write_config", "create_directory"}
            if action in write_actions:
                return f"Action {key} blocked: tool is read-only"

        # Network restriction
        if constraints.get("network") is False:
            network_modules = {"web_search", "browser", "http_client"}
            if module_id in network_modules:
                return f"Network access denied for {key}"

        # Allowed domains
        allowed_domains = constraints.get("allowed_domains", [])
        if allowed_domains:
            url = params.get("url", params.get("query", ""))
            if url and isinstance(url, str):
                from urllib.parse import urlparse
                try:
                    host = urlparse(url).hostname or ""
                    if host and not any(host.endswith(d) for d in allowed_domains):
                        return f"Domain '{host}' not in allowed domains for {key}"
                except Exception:
                    pass

        # Max file size (pre-check on content param)
        max_file_size = constraints.get("max_file_size", "")
        if max_file_size:
            content = params.get("content", "")
            if content and isinstance(content, str):
                content_size = len(content.encode("utf-8", errors="replace"))
                limit_bytes = _parse_size(max_file_size)
                if limit_bytes > 0 and content_size > limit_bytes:
                    return f"Content size ({content_size} bytes) exceeds max_file_size ({max_file_size}) for {key}"

        # Working directory enforcement
        working_dir = constraints.get("working_directory", "")
        if working_dir:
            param_cwd = params.get("working_directory", params.get("cwd", ""))
            if param_cwd:
                resolved_cwd = str(Path(param_cwd).resolve())
                allowed_cwd = str(Path(working_dir).resolve())
                if not resolved_cwd.startswith(allowed_cwd):
                    return f"Working directory '{param_cwd}' outside allowed directory '{working_dir}' for {key}"

        # Forbidden tables (database module)
        forbidden_tables = constraints.get("forbidden_tables", [])
        if forbidden_tables:
            query = params.get("query", params.get("sql", ""))
            if query and isinstance(query, str):
                query_lower = query.lower()
                for table in forbidden_tables:
                    if table.lower() in query_lower:
                        return f"Query references forbidden table '{table}' for {key}"

        return None

    # ── Capabilities enforcement ──────────────────────────────────

    def _check_capabilities(
        self, module_id: str, action: str, params: dict[str, Any]
    ) -> str | None:
        """Check app-level grant/deny rules with when: expression support."""
        scope = _current_scope.get()
        capabilities = scope.capabilities if scope else None
        if capabilities is None:
            return None

        # Check denials first (deny takes precedence)
        for denial in capabilities.deny:
            if denial.module == module_id:
                if not denial.action or denial.action == action:
                    # Evaluate when: condition if present
                    if denial.when and not self._eval_condition(denial.when, module_id, action, params):
                        continue  # Condition not met, skip this denial
                    reason = denial.reason or f"Action {module_id}.{action} denied by app capabilities"
                    return reason

        # Check grants (if grants are specified, only granted actions are allowed)
        if capabilities.grant:
            allowed = False
            matched_grant = None
            for grant in capabilities.grant:
                if grant.module == module_id:
                    if not grant.actions or action in grant.actions:
                        allowed = True
                        matched_grant = grant
                        break
            if not allowed:
                return f"Action {module_id}.{action} not in app capability grants"

            # Enforce grant-level constraints (paths, forbidden_commands, etc.)
            if matched_grant and matched_grant.constraints:
                grant_constraints = matched_grant.constraints.model_dump(exclude_defaults=True)
                if grant_constraints:
                    # Merge grant constraints into tool constraints for this action
                    # (grant constraints act as additional restrictions)
                    grant_error = self._check_grant_constraints(
                        module_id, action, params, grant_constraints
                    )
                    if grant_error:
                        return grant_error

        return None

    def _check_grant_constraints(
        self, module_id: str, action: str, params: dict[str, Any],
        constraints: dict[str, Any],
    ) -> str | None:
        """Enforce constraints attached to a capability grant."""
        key = f"{module_id}.{action}"

        # Reuse the same logic as _check_tool_constraints
        # by building a temporary tool_constraints dict
        scope = _current_scope.get()
        saved = (scope.tool_constraints if scope else {}).get(key) if scope else None
        try:
            if scope:
                scope.tool_constraints[key] = constraints
            return self._check_tool_constraints(module_id, action, params)
        finally:
            if scope:
                if saved is not None:
                    scope.tool_constraints[key] = saved
                else:
                    scope.tool_constraints.pop(key, None)

    def _check_approval_required(
        self, module_id: str, action: str, params: dict[str, Any]
    ) -> str | None:
        """Check if this action requires approval with when: expression support."""
        scope = _current_scope.get()
        capabilities = scope.capabilities if scope else None
        if capabilities is None:
            return None

        for rule in capabilities.approval_required:
            if rule.module and rule.module != module_id:
                continue
            if rule.action and rule.action != action:
                continue

            # Evaluate when: condition if present
            # Use default_on_error=False: broken conditions should NOT trigger approval
            if rule.when and not self._eval_condition(
                rule.when, module_id, action, params, default_on_error=False
            ):
                continue

            # Check count-based triggers
            if rule.trigger == "action_count" and rule.threshold > 0:
                key = f"{module_id}.{action}"
                count = scope.action_counts.get(key, 0)
                if count < rule.threshold:
                    continue  # Below threshold, no approval needed yet

            return rule.message or f"Approval required for {module_id}.{action}"

        return None

    async def _handle_approval(
        self,
        module_id: str,
        action: str,
        params: dict[str, Any],
        message: str,
    ) -> dict[str, Any] | None:
        """Handle an approval-required action.

        If an ApprovalGate is wired, block and wait for human decision.
        Otherwise, return an immediate error (standalone/CLI fallback).

        Returns:
            None if approved (proceed with execution).
            dict with "error" key if rejected/skipped.
            dict with modified params if MODIFY decision.
        """
        if self._approval_gate is None:
            # No gate — immediate error (standalone mode)
            return {"error": f"Approval required: {message}"}

        # Check auto-approve first
        if self._approval_gate.is_auto_approved(module_id, action):
            logger.info("Auto-approved %s.%s (APPROVE_ALWAYS)", module_id, action)
            return None  # proceed

        import uuid
        from llmos_bridge.orchestration.approval import (
            ApprovalDecision,
            ApprovalRequest,
        )

        scope = _current_scope.get()
        run_id = scope.run_id if scope else "unknown"

        request = ApprovalRequest(
            plan_id=run_id,
            action_id=f"{module_id}.{action}.{uuid.uuid4().hex[:8]}",
            module=module_id,
            action_name=action,
            params=params,
            risk_level="medium",
            description=message,
            requires_approval_reason="yaml_approval_rule",
        )

        # Emit SSE event so dashboard/CLI can show the approval prompt
        if self._event_bus:
            await self._event_bus.emit("llmos.approvals", {
                "type": "approval_requested",
                "request": request.to_dict(),
            })

        # Find the matching rule to get timeout/on_timeout config
        timeout = 300.0  # default 5 min
        on_timeout = "reject"
        if scope and scope.capabilities:
            for rule in scope.capabilities.approval_required:
                if rule.module and rule.module != module_id:
                    continue
                if rule.action and rule.action != action:
                    continue
                if rule.timeout:
                    from llmos_bridge.apps.flow_executor import _parse_duration
                    timeout = _parse_duration(rule.timeout) or 300.0
                on_timeout = rule.on_timeout or "reject"
                break

        logger.info(
            "Waiting for approval: %s.%s (timeout=%ss, on_timeout=%s)",
            module_id, action, timeout, on_timeout,
        )

        response = await self._approval_gate.request_approval(
            request, timeout=timeout, timeout_behavior=on_timeout,
        )

        # Process the decision
        if response.decision == ApprovalDecision.APPROVE:
            logger.info("Approved: %s.%s by %s", module_id, action, response.approved_by)
            return None  # proceed with execution

        if response.decision == ApprovalDecision.APPROVE_ALWAYS:
            logger.info("Approve-always: %s.%s by %s", module_id, action, response.approved_by)
            return None  # proceed (auto-approve handled by gate)

        if response.decision == ApprovalDecision.MODIFY:
            # Replace params and proceed — caller will use modified params
            if response.modified_params:
                params.clear()
                params.update(response.modified_params)
            logger.info("Modified and approved: %s.%s", module_id, action)
            return None  # proceed with modified params

        if response.decision == ApprovalDecision.SKIP:
            return {"skipped": True, "reason": response.reason or "Skipped by user"}

        # REJECT
        return {"error": f"Rejected: {response.reason or 'Action rejected by user'}"}

    def _eval_condition(
        self, expr: str, module_id: str, action: str, params: dict[str, Any],
        *, default_on_error: bool = True,
    ) -> bool:
        """Evaluate a when: condition expression.

        Args:
            default_on_error: What to return if evaluation fails.
                - True for deny rules (fail-closed: broken condition → deny = safe)
                - False for approval rules (fail-open: broken condition → no approval needed)
        """
        if self._expr is None:
            # No expression engine — treat as always true
            return True
        try:
            from llmos_bridge.apps.expression import ExpressionContext
            ctx = ExpressionContext(
                variables={
                    "module": module_id,
                    "action": action,
                    "params": params,
                    "action_counts": dict((_current_scope.get() or _ExecutionScope()).action_counts),
                },
            )
            return self._expr.evaluate_condition(expr, ctx)
        except Exception as e:
            logger.warning(
                "condition_eval_failed",
                expr=expr,
                module=module_id,
                action=action,
                error=str(e),
                default=default_on_error,
            )
            return default_on_error

    # ── Scanner pipeline ──────────────────────────────────────────

    async def _scan_params(
        self, module_id: str, action: str, params: dict[str, Any]
    ) -> str | None:
        """Run security scanners on action params. Returns error or None."""
        if self._scanner is None or not self._scanner.enabled:
            return None

        # Serialize params as text for scanning
        text = f"Module: {module_id}\nAction: {action}\n"
        text += json.dumps(params, default=str)

        try:
            from llmos_bridge.security.scanners.base import ScanContext
            context = ScanContext(
                plan_id=f"app-scan-{int(time.time())}",
                plan_description=f"App tool call: {module_id}.{action}",
                action_count=1,
                module_ids=[module_id],
            )

            # Run individual scanners (not full pipeline which needs IMLPlan)
            for scanner in self._scanner.registry.list_enabled():
                result = await scanner.scan(text, context)
                if result.verdict.value == "reject":
                    logger.warning(
                        "app_tool_blocked_by_scanner: %s.%s scanner=%s",
                        module_id, action, result.scanner_id,
                    )
                    return (
                        f"Blocked by security scanner ({result.scanner_id}): "
                        f"{result.details or 'suspicious content detected'}"
                    )
        except Exception as e:
            logger.warning("app_scanner_error: %s", e)

        return None

    # ── Perception capture ───────────────────────────────────────

    async def _capture_perception(
        self, module_id: str, action: str, phase: str
    ) -> None:
        """Capture screenshot/OCR before or after tool execution."""
        scope = _current_scope.get()
        perception = scope.perception if scope else None
        if perception is None or not perception.enabled:
            return

        # Check per-action override
        key = f"{module_id}.{action}"
        action_config = perception.actions.get(key)

        if phase == "before":
            should_capture = (
                (action_config and action_config.capture_before)
                or (not action_config and perception.capture_before)
            )
        else:
            should_capture = (
                (action_config and action_config.capture_after)
                or (not action_config and perception.capture_after)
            )

        if not should_capture:
            return

        try:
            perception_module = self._registry.get("perception")
            timeout = (
                (action_config.timeout_seconds if action_config else None)
                or perception.timeout_seconds
            )
            ocr = (
                (action_config.ocr_enabled if action_config else None)
                or perception.ocr_enabled
            )

            import asyncio
            result = await asyncio.wait_for(
                perception_module.execute("capture_screen", {
                    "ocr": ocr,
                    "metadata": {"module": module_id, "action": action, "phase": phase},
                }),
                timeout=timeout,
            )
            logger.debug("perception_%s: %s.%s captured", phase, module_id, action)
        except Exception as e:
            logger.debug("perception_%s_skipped: %s.%s — %s", phase, module_id, action, e)

    # ── Audit enforcement ────────────────────────────────────────

    async def _emit_audit(
        self,
        module_id: str,
        action: str,
        params: dict[str, Any],
        success: bool,
        error_msg: str,
        start: float,
    ) -> None:
        """Emit audit event with audit config enforcement (level, redaction, notifications)."""
        if self._event_bus is None:
            return

        # Determine audit level (from per-request scope)
        scope = _current_scope.get()
        audit = scope.audit if scope else None
        if audit:
            level = audit.level.value if audit.level else "full"
            if level == "none":
                return
            if level == "errors" and success:
                return
            if level == "mutations" and success:
                read_actions = {
                    "read_file", "list_directory", "search_files", "get_info",
                    "health_check", "get_metrics", "query", "search",
                }
                if action in read_actions:
                    return

        elapsed_ms = (time.monotonic() - start) * 1000

        event: dict[str, Any] = {
            "event": "app_tool_execution",
            "module": module_id,
            "action": action,
            "success": success,
            "error": error_msg,
            "duration_ms": round(elapsed_ms, 1),
        }

        # Include identity context if available (set by API routes / AppRuntime)
        identity = scope.identity if scope else None
        if identity is not None:
            event["app_id"] = identity.app_id
            if identity.agent_id:
                event["agent_id"] = identity.agent_id
            if identity.session_id:
                event["session_id"] = identity.session_id
            event["role"] = identity.role.value if hasattr(identity.role, "value") else str(identity.role)

        # Include params if audit says so (with redaction)
        if audit and audit.log_params:
            if audit.redact_secrets:
                event["params"] = _redact_secrets(params)
            else:
                event["params"] = params

        try:
            await self._event_bus.emit("llmos.actions.results", event)
        except Exception:
            pass

    # ── Multi-node dispatch ───────────────────────────────────────

    async def _dispatch(
        self, module: Any, module_id: str, action: str, params: dict[str, Any],
        exec_ctx: Any = None,
    ) -> Any:
        """Dispatch to module, with optional multi-node routing and ExecutionContext."""
        # If node_registry is available and module supports remote invocation,
        # try to route to the best node
        if self._node_registry is not None and self._routing_config is not None:
            try:
                from llmos_bridge.orchestration.routing import CapabilityRouter
                router = CapabilityRouter(
                    node_registry=self._node_registry,
                    config=self._routing_config,
                )
                node = router.select_node(module_id, action)
                if node is not None and not node.is_local:
                    return await node.execute_action(module_id, action, params)
            except Exception:
                pass  # Fall through to local execution

        # Pass ExecutionContext to module.execute() (IML tracing)
        return await module.execute(action, params, context=exec_ctx)

    # ── Batch execution ────────────────────────────────────────────

    async def execute_batch(
        self, calls: list[tuple[str, str, dict[str, Any]]]
    ) -> list[dict[str, Any]]:
        """Execute multiple tool calls concurrently through the full pipeline.

        Each call is a (module_id, action, params) tuple.  All calls run in
        parallel via ``asyncio.gather``, sharing the same security pipeline
        but executing independently.  This is used by AgentRuntime when the
        LLM returns multiple tool_use blocks in one response.

        Returns results in the same order as the input calls.
        """
        if not calls:
            return []
        if len(calls) == 1:
            return [await self.execute(*calls[0])]

        results = await asyncio.gather(
            *(self.execute(m, a, p) for m, a, p in calls),
            return_exceptions=True,
        )
        final: list[dict[str, Any]] = []
        for r in results:
            if isinstance(r, Exception):
                final.append({"error": f"{type(r).__name__}: {r}"})
            else:
                final.append(r)
        return final

    # ── Module info ───────────────────────────────────────────────

    def get_module_info(self) -> dict[str, dict]:
        """Build module_info dict from real ModuleManifest objects."""
        return module_info_from_manifests(self._registry.all_manifests())


def module_info_from_manifests(manifests: list) -> dict[str, dict]:
    """Convert a list of ModuleManifest objects to the dict format AppToolRegistry expects.

    Args:
        manifests: List of ModuleManifest dataclass instances.

    Returns:
        Dict mapping module_id -> {"actions": [{"name", "description", "params"}]}
    """
    info: dict[str, dict] = {}
    for manifest in manifests:
        actions = []
        for action_spec in manifest.actions:
            params: dict[str, Any] = {}
            for p in action_spec.params:
                param_def: dict[str, Any] = {
                    "type": p.type,
                    "description": p.description,
                    "required": p.required,
                }
                if p.enum is not None:
                    param_def["enum"] = p.enum
                if p.default is not None:
                    param_def["default"] = p.default
                params[p.name] = param_def
            actions.append({
                "name": action_spec.name,
                "description": action_spec.description,
                "params": params,
            })
        info[manifest.module_id] = {"actions": actions}
    return info


# ── Helpers ───────────────────────────────────────────────────────

_SECRET_PATTERNS = re.compile(
    r"(api[_-]?key|token|secret|password|credential|auth)",
    re.IGNORECASE,
)


def _redact_secrets(params: dict[str, Any]) -> dict[str, Any]:
    """Redact values of params whose keys look like secrets."""
    redacted: dict[str, Any] = {}
    for k, v in params.items():
        if _SECRET_PATTERNS.search(k):
            redacted[k] = "***REDACTED***"
        elif isinstance(v, dict):
            redacted[k] = _redact_secrets(v)
        else:
            redacted[k] = v
    return redacted


def _parse_duration(s: str) -> float:
    """Parse a duration string like '30s', '5m', '1h' to seconds."""
    if not s:
        return 0.0
    s = s.strip().lower()
    try:
        if s.endswith("ms"):
            return float(s[:-2]) / 1000
        if s.endswith("s"):
            return float(s[:-1])
        if s.endswith("m"):
            return float(s[:-1]) * 60
        if s.endswith("h"):
            return float(s[:-1]) * 3600
        return float(s)
    except ValueError:
        return 0.0


_SIZE_UNITS = {"b": 1, "kb": 1024, "mb": 1024**2, "gb": 1024**3, "tb": 1024**4}


def _parse_size(s: str) -> int:
    """Parse a size string like '50MB', '1GB' to bytes."""
    if not s:
        return 0
    s = s.strip()
    match = re.match(r"^(\d+(?:\.\d+)?)\s*(B|KB|MB|GB|TB)$", s, re.IGNORECASE)
    if not match:
        return 0
    value = float(match.group(1))
    unit = match.group(2).lower()
    return int(value * _SIZE_UNITS.get(unit, 1))
