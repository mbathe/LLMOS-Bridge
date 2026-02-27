"""Security layer — Declarative security decorators for module actions.

Six decorators that:
  (a) store metadata as attributes on the function (for introspection, manifest
      enrichment, system prompt generation)
  (b) wrap the function for runtime enforcement via ``self._security``
  (c) degrade gracefully when ``self._security`` is None (backward compatible)

Stacking order (outer → inner):
    @requires_permission → @sensitive_action → @rate_limited → @audit_trail

All decorators use ``functools.wraps`` to preserve ``__name__`` so that
``getattr(self, f"_action_{name}")`` dispatch works transparently in
:class:`BaseModule`.

Usage in a module::

    class FilesystemModule(BaseModule):

        @requires_permission(Permission.FILESYSTEM_WRITE, reason="Writes to disk")
        @audit_trail("standard")
        async def _action_write_file(self, params: dict) -> Any:
            ...

Community modules::

    class MyModule(BaseModule):

        @requires_permission("my_plugin.sensor", reason="Reads sensor data")
        @rate_limited(calls_per_minute=10)
        async def _action_read_sensor(self, params: dict) -> Any:
            ...
"""

from __future__ import annotations

import functools
import time
from typing import Any, Callable

from llmos_bridge.logging import get_logger
from llmos_bridge.security.models import DataClassification, RiskLevel

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Metadata copy helper
# ---------------------------------------------------------------------------

_SECURITY_ATTRS = (
    "_required_permissions",
    "_permission_reason",
    "_sensitive_action",
    "_risk_level",
    "_requires_confirmation",
    "_irreversible",
    "_rate_limit",
    "_audit_level",
    "_data_classification",
    "_intent_verified",
    "_intent_strict",
)


def _copy_metadata(source: Any, target: Any) -> None:
    """Copy all security decorator metadata from *source* to *target*.

    This ensures that when decorators are stacked, inner decorator
    attributes survive on the outermost wrapper.
    """
    for attr in _SECURITY_ATTRS:
        if hasattr(source, attr):
            setattr(target, attr, getattr(source, attr))


# ---------------------------------------------------------------------------
# @requires_permission
# ---------------------------------------------------------------------------


def requires_permission(
    *permissions: str, reason: str = ""
) -> Callable[..., Any]:
    """Declare OS-level permissions required by this action.

    At runtime (when ``self._security`` is set), calls
    ``permission_manager.check_or_raise()`` for each permission before
    executing the action.

    Parameters
    ----------
    *permissions:
        One or more permission strings (e.g. ``Permission.FILESYSTEM_WRITE``
        or ``"my_plugin.resource"``).
    reason:
        Human-readable reason shown in permission prompts and audit logs.
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        async def wrapper(self: Any, params: dict[str, Any]) -> Any:
            security = getattr(self, "_security", None)
            if security is not None:
                pm = security.permission_manager
                action_name = fn.__name__.removeprefix("_action_")
                for perm in permissions:
                    await pm.check_or_raise(
                        perm, self.MODULE_ID, action=action_name
                    )
            return await fn(self, params)

        # Store metadata
        wrapper._required_permissions = list(permissions)  # type: ignore[attr-defined]
        wrapper._permission_reason = reason  # type: ignore[attr-defined]
        _copy_metadata(fn, wrapper)
        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# @sensitive_action
# ---------------------------------------------------------------------------


def sensitive_action(
    risk_level: RiskLevel = RiskLevel.HIGH,
    *,
    requires_confirmation: bool = True,
    irreversible: bool = False,
) -> Callable[..., Any]:
    """Mark an action as sensitive with risk classification.

    At runtime, emits an audit event when the action is invoked.

    Parameters
    ----------
    risk_level:
        Risk classification for this action.
    requires_confirmation:
        Whether the action should require user confirmation (Phase 2).
    irreversible:
        Whether the action's effects cannot be undone.
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        async def wrapper(self: Any, params: dict[str, Any]) -> Any:
            security = getattr(self, "_security", None)
            if security is not None:
                action_name = fn.__name__.removeprefix("_action_")
                await security.audit.bus.emit(
                    "llmos.security",
                    {
                        "event": "sensitive_action_invoked",
                        "module_id": self.MODULE_ID,
                        "action": action_name,
                        "risk_level": risk_level.value,
                        "irreversible": irreversible,
                    },
                )
            return await fn(self, params)

        wrapper._sensitive_action = True  # type: ignore[attr-defined]
        wrapper._risk_level = risk_level  # type: ignore[attr-defined]
        wrapper._requires_confirmation = requires_confirmation  # type: ignore[attr-defined]
        wrapper._irreversible = irreversible  # type: ignore[attr-defined]
        _copy_metadata(fn, wrapper)
        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# @rate_limited
# ---------------------------------------------------------------------------


def rate_limited(
    calls_per_minute: int | None = None,
    calls_per_hour: int | None = None,
) -> Callable[..., Any]:
    """Enforce per-action rate limits.

    At runtime, calls ``rate_limiter.check_or_raise()`` with the
    configured limits.

    Parameters
    ----------
    calls_per_minute:
        Maximum invocations per rolling 60-second window.
    calls_per_hour:
        Maximum invocations per rolling 3600-second window.
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        async def wrapper(self: Any, params: dict[str, Any]) -> Any:
            security = getattr(self, "_security", None)
            if security is not None:
                action_key = f"{self.MODULE_ID}.{fn.__name__.removeprefix('_action_')}"
                security.rate_limiter.check_or_raise(
                    action_key,
                    calls_per_minute=calls_per_minute,
                    calls_per_hour=calls_per_hour,
                )
            return await fn(self, params)

        wrapper._rate_limit = {  # type: ignore[attr-defined]
            "calls_per_minute": calls_per_minute,
            "calls_per_hour": calls_per_hour,
        }
        _copy_metadata(fn, wrapper)
        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# @audit_trail
# ---------------------------------------------------------------------------


def audit_trail(level: str = "standard") -> Callable[..., Any]:
    """Add before/after audit logging to an action.

    Parameters
    ----------
    level:
        One of ``"minimal"``, ``"standard"``, or ``"detailed"``.
        - minimal: log invocation only
        - standard: log invocation + success/failure
        - detailed: log invocation + params + result summary
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        async def wrapper(self: Any, params: dict[str, Any]) -> Any:
            security = getattr(self, "_security", None)
            if security is None:
                return await fn(self, params)

            action_name = fn.__name__.removeprefix("_action_")
            action_key = f"{self.MODULE_ID}.{action_name}"

            # Before
            before_event: dict[str, Any] = {
                "event": "audit_action_before",
                "module_id": self.MODULE_ID,
                "action": action_name,
                "audit_level": level,
            }
            if level == "detailed":
                before_event["params"] = _safe_summary(params)
            await security.audit.bus.emit("llmos.actions", before_event)

            start = time.time()
            try:
                result = await fn(self, params)
            except Exception as exc:
                # After — failure
                after_event: dict[str, Any] = {
                    "event": "audit_action_after",
                    "module_id": self.MODULE_ID,
                    "action": action_name,
                    "audit_level": level,
                    "success": False,
                    "duration_ms": round((time.time() - start) * 1000, 1),
                    "error": str(exc),
                }
                await security.audit.bus.emit("llmos.actions", after_event)
                raise

            # After — success
            after_event = {
                "event": "audit_action_after",
                "module_id": self.MODULE_ID,
                "action": action_name,
                "audit_level": level,
                "success": True,
                "duration_ms": round((time.time() - start) * 1000, 1),
            }
            if level == "detailed":
                after_event["result_summary"] = _safe_summary(result)
            await security.audit.bus.emit("llmos.actions", after_event)

            return result

        wrapper._audit_level = level  # type: ignore[attr-defined]
        _copy_metadata(fn, wrapper)
        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# @data_classification
# ---------------------------------------------------------------------------


def data_classification(
    classification: DataClassification,
) -> Callable[..., Any]:
    """Declare the data sensitivity level for an action.

    This is primarily metadata used by the system prompt, the security
    dashboard, and future intent verification.  No runtime enforcement
    in Phase 1.

    Parameters
    ----------
    classification:
        The data sensitivity level.
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        async def wrapper(self: Any, params: dict[str, Any]) -> Any:
            return await fn(self, params)

        wrapper._data_classification = classification  # type: ignore[attr-defined]
        _copy_metadata(fn, wrapper)
        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# @intent_verified
# ---------------------------------------------------------------------------


def intent_verified(strict: bool = False) -> Callable[..., Any]:
    """Mark action for intent verification (Phase 2).

    In Phase 1 this is metadata-only.  In Phase 2 the runtime wrapper
    will call ``IntentVerifier.verify_action()`` before execution.

    Parameters
    ----------
    strict:
        When ``True`` (Phase 2), verification failure blocks execution.
        When ``False``, verification failure is logged but execution continues.
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        async def wrapper(self: Any, params: dict[str, Any]) -> Any:
            security = getattr(self, "_security", None)
            if security is not None:
                verifier = getattr(security, "intent_verifier", None)
                if verifier is not None and verifier.enabled:
                    from llmos_bridge.protocol.models import IMLAction

                    action_name = fn.__name__.removeprefix("_action_")
                    mock_action = IMLAction(
                        id=f"_runtime_{action_name}",
                        action=action_name,
                        module=getattr(self, "MODULE_ID", "unknown"),
                        params=params,
                    )
                    result = await verifier.verify_action(mock_action)
                    if not result.is_safe():
                        if strict:
                            from llmos_bridge.exceptions import SuspiciousIntentError

                            raise SuspiciousIntentError(
                                plan_id="(decorator)",
                                reasoning=result.reasoning,
                                threats=[t.threat_type.value for t in result.threats],
                            )
                        else:
                            log.warning(
                                "intent_verification_action_warn",
                                module=getattr(self, "MODULE_ID", "unknown"),
                                action=action_name,
                                reasoning=result.reasoning,
                            )
            return await fn(self, params)

        wrapper._intent_verified = True  # type: ignore[attr-defined]
        wrapper._intent_strict = strict  # type: ignore[attr-defined]
        _copy_metadata(fn, wrapper)
        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# Introspection helpers
# ---------------------------------------------------------------------------


def collect_security_metadata(fn: Any) -> dict[str, Any]:
    """Extract all security decorator metadata from a (possibly wrapped) function.

    Returns a dict with keys present only if the corresponding decorator
    was applied::

        {
            "permissions": ["filesystem.write"],
            "permission_reason": "Writes to disk",
            "risk_level": "high",
            "irreversible": True,
            "requires_confirmation": True,
            "rate_limit": {"calls_per_minute": 60, "calls_per_hour": None},
            "audit_level": "standard",
            "data_classification": "confidential",
            "intent_verified": True,
            "intent_strict": False,
        }
    """
    meta: dict[str, Any] = {}

    if hasattr(fn, "_required_permissions"):
        meta["permissions"] = fn._required_permissions
    if hasattr(fn, "_permission_reason"):
        meta["permission_reason"] = fn._permission_reason
    if hasattr(fn, "_risk_level"):
        meta["risk_level"] = fn._risk_level.value if hasattr(fn._risk_level, "value") else fn._risk_level
    if hasattr(fn, "_irreversible"):
        meta["irreversible"] = fn._irreversible
    if hasattr(fn, "_requires_confirmation"):
        meta["requires_confirmation"] = fn._requires_confirmation
    if hasattr(fn, "_rate_limit"):
        meta["rate_limit"] = fn._rate_limit
    if hasattr(fn, "_audit_level"):
        meta["audit_level"] = fn._audit_level
    if hasattr(fn, "_data_classification"):
        classification = fn._data_classification
        meta["data_classification"] = classification.value if hasattr(classification, "value") else classification
    if hasattr(fn, "_intent_verified"):
        meta["intent_verified"] = fn._intent_verified
    if hasattr(fn, "_intent_strict"):
        meta["intent_strict"] = fn._intent_strict

    return meta


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _safe_summary(obj: Any, max_len: int = 200) -> Any:
    """Create a safe, truncated summary of a value for audit logs."""
    if obj is None:
        return None
    if isinstance(obj, (bool, int, float)):
        return obj
    if isinstance(obj, str):
        return obj[:max_len] + ("..." if len(obj) > max_len else "")
    if isinstance(obj, dict):
        return {k: _safe_summary(v, max_len) for k, v in list(obj.items())[:10]}
    if isinstance(obj, (list, tuple)):
        return [_safe_summary(v, max_len) for v in obj[:5]]
    return str(obj)[:max_len]
