"""Identity layer — Authorization matrix enforcement.

The ``AuthorizationGuard`` is the central enforcement point for
identity-based authorization.  It sits between the API layer and the
executor, checking:

  1. Application exists and is enabled
  2. RBAC role is sufficient for the operation
  3. Module is in the application's ``allowed_modules`` whitelist
  4. Action is in the application's ``allowed_actions[module]`` whitelist
  5. Application quotas (``max_concurrent_plans``, ``max_actions_per_plan``)
  6. Session belongs to the correct application

When ``enabled=False`` (standalone mode), all methods are no-ops —
preserving zero-config behaviour.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from llmos_bridge.exceptions import (
    ApplicationNotFoundError,
    AuthorizationError,
    QuotaExceededError,
)
from llmos_bridge.identity.models import Application, IdentityContext, Role, Session
from llmos_bridge.logging import get_logger

if TYPE_CHECKING:
    from llmos_bridge.identity.store import IdentityStore
    from llmos_bridge.protocol.models import IMLPlan

log = get_logger(__name__)

# Ordered from most privileged (index 0) to least privileged.
ROLE_HIERARCHY: list[Role] = [
    Role.ADMIN,
    Role.APP_ADMIN,
    Role.OPERATOR,
    Role.VIEWER,
    Role.AGENT,
]


class AuthorizationGuard:
    """Enforces the identity-based authorization matrix.

    Designed as a **stateless** (aside from active-plan counter)
    enforcement layer that can be injected into both the API routes
    and the PlanExecutor.

    Usage::

        guard = AuthorizationGuard(store=identity_store, enabled=True)

        # Before plan execution:
        app = await guard.check_plan_submission(identity, plan)
        guard.plan_started(identity.app_id)
        try:
            for action in plan.actions:
                guard.check_action_allowed(app, action.module, action.action)
                ...
        finally:
            guard.plan_finished(identity.app_id)

        # In API routes:
        guard.require_role(identity, Role.APP_ADMIN, resource="applications")
        guard.require_app_scope(identity, target_app_id)
    """

    def __init__(
        self,
        store: IdentityStore | None = None,
        enabled: bool = False,
    ) -> None:
        self._store = store
        self._enabled = enabled
        # In-memory counter: app_id → number of currently running plans.
        # Reset on daemon restart (acceptable — quotas are soft limits).
        self._active_plans: dict[str, int] = {}

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ------------------------------------------------------------------
    # Plan-level authorization (async — reads from store)
    # ------------------------------------------------------------------

    async def check_plan_submission(
        self,
        identity: IdentityContext,
        plan: IMLPlan,
    ) -> Application | None:
        """Pre-execution authorization check.

        Returns the resolved ``Application`` for downstream per-action
        checks, or ``None`` when the guard is disabled.

        Raises:
            ApplicationNotFoundError: Application doesn't exist.
            AuthorizationError: Application is disabled, session expired, or role too low.
            QuotaExceededError: Concurrent plan limit or action count exceeded.
        """
        if not self._enabled or self._store is None:
            return None

        # 1. Resolve application
        app = await self._store.get_application(identity.app_id)
        if app is None:
            raise ApplicationNotFoundError(identity.app_id)

        # 2. Application must be enabled
        if not app.enabled:
            raise AuthorizationError(
                role=identity.role.value,
                required="enabled_app",
                resource=f"application:{app.app_id}",
            )

        # 3. Role check: submitting a plan requires at least AGENT level
        self.require_role(identity, Role.AGENT, resource="plan_submission")

        # 4. Check action count quota
        if len(plan.actions) > app.max_actions_per_plan:
            raise QuotaExceededError(
                app_id=app.app_id,
                resource="max_actions_per_plan",
                limit=app.max_actions_per_plan,
            )

        # 5. Check concurrent plan quota
        active = self._active_plans.get(app.app_id, 0)
        if active >= app.max_concurrent_plans:
            raise QuotaExceededError(
                app_id=app.app_id,
                resource="max_concurrent_plans",
                limit=app.max_concurrent_plans,
            )

        # 6. Session validation (binding + expiry + module restrictions)
        session = await self.validate_session(identity)

        # 7. Pre-flight: all actions must be allowed by app + session allowlists
        for action in plan.actions:
            self.check_action_allowed(app, action.module, action.action, session=session)

        return app

    # ------------------------------------------------------------------
    # Per-action authorization (sync — uses cached Application)
    # ------------------------------------------------------------------

    def check_action_allowed(
        self,
        app: Application,
        module_id: str,
        action_name: str,
        *,
        session: Session | None = None,
    ) -> None:
        """Verify a single action against the application's (and session's) allowlists.

        Enforces (in order):
          1. App-level module whitelist
          2. App-level per-module action whitelist
          3. Session-level module whitelist (if session provided, further restricts app's list)

        Raises:
            AuthorizationError: Module or action is not allowed.
        """
        if not self._enabled:
            return

        # 1. App-level module whitelist (empty = all modules allowed)
        if app.allowed_modules and module_id not in app.allowed_modules:
            raise AuthorizationError(
                role="app_policy",
                required=f"module:{module_id}",
                resource=f"application:{app.app_id}",
            )

        # 2. App-level per-module action whitelist (no entry = all actions allowed)
        if app.allowed_actions and module_id in app.allowed_actions:
            allowed = app.allowed_actions[module_id]
            if allowed and action_name not in allowed:
                raise AuthorizationError(
                    role="app_policy",
                    required=f"action:{module_id}.{action_name}",
                    resource=f"application:{app.app_id}",
                )

        # 3. Session-level module whitelist (non-empty = further restriction on app's list)
        if session is not None and session.allowed_modules:
            if module_id not in session.allowed_modules:
                raise AuthorizationError(
                    role="session_policy",
                    required=f"module:{module_id}",
                    resource=f"session:{session.session_id}",
                )

    # ------------------------------------------------------------------
    # Session validation (binding + expiry)
    # ------------------------------------------------------------------

    async def validate_session(
        self,
        identity: IdentityContext,
    ) -> Session | None:
        """Validate the session and return it for downstream checks.

        Checks (in order):
          1. Session belongs to the caller's application
          2. Session has not expired (absolute or idle timeout)

        Returns the ``Session`` if present and valid, or ``None`` if
        no session_id in the identity.

        Raises:
            AuthorizationError: Session belongs to a different app or has expired.
        """
        if not self._enabled or self._store is None:
            return None
        if identity.session_id is None:
            return None  # No session — nothing to check

        session = await self._store.get_session(identity.session_id)
        if session is None:
            return None  # Session doesn't exist — allow (may be auto-created)

        # 1. Binding: session must belong to the caller's application
        if session.app_id != identity.app_id:
            raise AuthorizationError(
                role=identity.role.value,
                required=f"session_app:{session.app_id}",
                resource=f"session:{identity.session_id}",
            )

        # 2. Expiry: reject if session has expired
        if session.is_expired():
            raise AuthorizationError(
                role=identity.role.value,
                required="active_session",
                resource=f"session:{identity.session_id}",
            )

        return session

    # Backward-compat alias (used by older tests)
    async def check_session_binding(self, identity: IdentityContext) -> None:
        """Deprecated — use validate_session() instead."""
        await self.validate_session(identity)

    # ------------------------------------------------------------------
    # RBAC role enforcement
    # ------------------------------------------------------------------

    def require_role(
        self,
        identity: IdentityContext,
        minimum: Role,
        resource: str = "",
    ) -> None:
        """Raise ``AuthorizationError`` if the caller's role is below *minimum*.

        Role hierarchy (most → least privileged):
          ADMIN > APP_ADMIN > OPERATOR > VIEWER > AGENT
        """
        if not self._enabled:
            return
        caller_idx = (
            ROLE_HIERARCHY.index(identity.role)
            if identity.role in ROLE_HIERARCHY
            else len(ROLE_HIERARCHY)
        )
        required_idx = (
            ROLE_HIERARCHY.index(minimum)
            if minimum in ROLE_HIERARCHY
            else 0
        )
        if caller_idx > required_idx:
            raise AuthorizationError(
                role=identity.role.value,
                required=minimum.value,
                resource=resource,
            )

    def require_app_scope(
        self,
        identity: IdentityContext,
        target_app_id: str,
    ) -> None:
        """Verify APP_ADMIN can only manage their own application.

        ADMIN bypasses this check.  All other roles below APP_ADMIN
        should have been caught by ``require_role`` first.
        """
        if not self._enabled:
            return
        if identity.role == Role.ADMIN:
            return  # ADMIN can manage any app
        if identity.app_id != target_app_id:
            raise AuthorizationError(
                role=identity.role.value,
                required="app_scope",
                resource=f"application:{target_app_id}",
            )

    # ------------------------------------------------------------------
    # Active plan tracking
    # ------------------------------------------------------------------

    def plan_started(self, app_id: str) -> None:
        """Increment the active plan counter for an application."""
        self._active_plans[app_id] = self._active_plans.get(app_id, 0) + 1

    def plan_finished(self, app_id: str) -> None:
        """Decrement the active plan counter for an application."""
        current = self._active_plans.get(app_id, 0)
        if current > 0:
            self._active_plans[app_id] = current - 1
        else:
            self._active_plans.pop(app_id, None)

    def active_plan_count(self, app_id: str) -> int:
        """Return the number of currently running plans for an application."""
        return self._active_plans.get(app_id, 0)
