"""Security layer — Permission guard.

The PermissionGuard is the single enforcement point for all security checks.
It runs before every action dispatch — no exception.

Checks performed (in order):
  1. Plan action count limit
  2. Action allowed by profile
  3. Explicit approval requirement (config + action flag)
  4. Sandbox path enforcement (filesystem actions only)
"""

from __future__ import annotations

from llmos_bridge.exceptions import ApprovalRequiredError, PermissionDeniedError
from llmos_bridge.protocol.models import IMLAction, IMLPlan
from llmos_bridge.security.profiles import PermissionProfileConfig


class PermissionGuard:
    """Enforces the active permission profile against plan and action requests.

    Usage::

        guard = PermissionGuard(profile_config, require_approval_for=["filesystem.delete_file"])
        guard.check_plan(plan)                           # plan-level checks
        guard.check_action(action, plan_id="p1")         # per-action checks
    """

    def __init__(
        self,
        profile: PermissionProfileConfig,
        require_approval_for: list[str] | None = None,
        sandbox_paths: list[str] | None = None,
    ) -> None:
        self._profile = profile
        self._require_approval_for: set[str] = set(require_approval_for or [])
        self._sandbox_paths: list[str] = sandbox_paths or []

    # ------------------------------------------------------------------
    # Plan-level checks
    # ------------------------------------------------------------------

    def check_plan(self, plan: IMLPlan) -> None:
        """Verify plan-level constraints before execution starts.

        Raises:
            PermissionDeniedError: The plan exceeds allowed action count.
        """
        if len(plan.actions) > self._profile.max_plan_actions:
            raise PermissionDeniedError(
                action="(plan)",
                module="(plan)",
                profile=self._profile.profile.value,
            )

        # Pre-flight check all actions to surface permission errors early.
        for action in plan.actions:
            if not self._profile.is_allowed(action.module, action.action):
                raise PermissionDeniedError(
                    action=action.action,
                    module=action.module,
                    profile=self._profile.profile.value,
                )

    # ------------------------------------------------------------------
    # Action-level checks
    # ------------------------------------------------------------------

    def check_action(self, action: IMLAction, plan_id: str) -> None:
        """Verify a single action is allowed before dispatch.

        This is called again at dispatch time (not just pre-flight)
        to guard against profile changes mid-plan.

        Raises:
            PermissionDeniedError: The action is not allowed.
            ApprovalRequiredError: The action requires user approval.
        """
        # Approval check runs FIRST: `require_approval_for` acts as a gated
        # permission that takes precedence over profile denials, allowing admins
        # to require human confirmation for otherwise-restricted actions.
        if self._requires_approval(action):
            if not self._profile.allow_approval_bypass:
                raise ApprovalRequiredError(action_id=action.id, plan_id=plan_id)
            # Unrestricted profile bypasses the approval gate; fall through to
            # the is_allowed check below.

        if not self._profile.is_allowed(action.module, action.action):
            raise PermissionDeniedError(
                action=action.action,
                module=action.module,
                profile=self._profile.profile.value,
            )

        if action.module == "filesystem":
            self._check_sandbox(action)

    def is_allowed(self, module_id: str, action_name: str) -> bool:
        """Check without raising — useful for UI feature flags."""
        return self._profile.is_allowed(module_id, action_name)

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _requires_approval(self, action: IMLAction) -> bool:
        if action.requires_approval:
            return True
        key = f"{action.module}.{action.action}"
        return key in self._require_approval_for

    def _check_sandbox(self, action: IMLAction) -> None:
        """Reject filesystem actions that target paths outside the sandbox."""
        if not self._sandbox_paths:
            return  # No sandbox configured.

        path: str | None = action.params.get("path") or action.params.get("source")
        if path is None:
            return

        # Resolve template expressions conservatively — if the path contains
        # a template, skip sandbox check (it will be re-checked at runtime
        # after resolution with the actual resolved value).
        if "{{" in path:
            return

        import os.path

        abs_path = os.path.abspath(path)
        for sandbox in self._sandbox_paths:
            abs_sandbox = os.path.abspath(sandbox)
            if abs_path.startswith(abs_sandbox + os.sep) or abs_path == abs_sandbox:
                return

        raise PermissionDeniedError(
            action=action.action,
            module=action.module,
            profile=self._profile.profile.value,
        )
