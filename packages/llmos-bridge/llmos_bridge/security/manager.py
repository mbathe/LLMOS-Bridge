"""Security layer — SecurityManager aggregate.

Single object injected into all modules via ``BaseModule.set_security()``,
grouping all security subsystems:

    - ``permission_manager`` — check/grant/revoke OS-level permissions
    - ``rate_limiter``       — per-action sliding-window rate limiting
    - ``audit``              — AuditLogger for event trail
    - ``intent_verifier``    — LLM-based intent verification (optional)

Usage::

    sm = SecurityManager(
        permission_manager=pm,
        rate_limiter=limiter,
        audit=audit_logger,
    )
    module.set_security(sm)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from llmos_bridge.security.audit import AuditLogger
from llmos_bridge.security.permissions import PermissionManager
from llmos_bridge.security.rate_limiter import ActionRateLimiter

if TYPE_CHECKING:
    from llmos_bridge.security.intent_verifier import IntentVerifier


@dataclass
class SecurityManager:
    """Aggregate of all security subsystems.

    Injected into every module instance so that decorators can access
    permission checks, rate limiting, and audit logging through a single
    ``self._security`` reference.
    """

    permission_manager: PermissionManager
    rate_limiter: ActionRateLimiter
    audit: AuditLogger
    intent_verifier: IntentVerifier | None = field(default=None)
