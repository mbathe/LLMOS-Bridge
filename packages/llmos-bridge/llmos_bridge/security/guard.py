"""Security layer — Permission guard.

The PermissionGuard is the single enforcement point for all security checks.
It runs before every action dispatch — no exception.

Checks performed (in order):
  1. Plan action count limit
  2. Action allowed by profile
  3. Explicit approval requirement (config + action flag)
  4. Sandbox path enforcement (all modules with path-like params)
"""

from __future__ import annotations

import os.path
from typing import Any

from llmos_bridge.exceptions import ApprovalRequiredError, PermissionDeniedError
from llmos_bridge.protocol.models import IMLAction, IMLPlan
from llmos_bridge.security.profiles import PermissionProfileConfig

# All known parameter keys that carry file paths across all modules.
_PATH_PARAM_KEYS = (
    "path",
    "source",
    "destination",
    "output_path",
    "image_path",
    "file_path",
    "theme_path",
    "screenshot_path",
    "database",
)


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

        # Sandbox check applies to ALL modules — any param containing a file
        # path is validated against the configured sandbox directories.
        self._check_sandbox(action)

    def check_sandbox_params(
        self, module: str, action: str, params: dict[str, Any]
    ) -> None:
        """Re-check sandbox with resolved params after template resolution.

        The pre-flight ``_check_sandbox`` skips paths containing ``{{``
        template expressions.  This method is called by the executor *after*
        template resolution so that resolved paths are validated too.
        """
        if not self._sandbox_paths:
            return
        for key in _PATH_PARAM_KEYS:
            value = params.get(key)
            if value and isinstance(value, str) and "{{" not in value:
                self._validate_sandbox_path(value, module, action)

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
        """Reject actions that target paths outside the sandbox."""
        if not self._sandbox_paths:
            return

        for key in _PATH_PARAM_KEYS:
            path = action.params.get(key)
            if not path or not isinstance(path, str):
                continue

            # Template expressions are skipped here — they will be
            # re-checked via check_sandbox_params() after resolution.
            if "{{" in path:
                continue

            self._validate_sandbox_path(path, action.module, action.action)

    def _validate_sandbox_path(self, path: str, module: str, action: str) -> None:
        """Raise PermissionDeniedError if *path* is outside all sandbox dirs."""
        abs_path = os.path.abspath(path)
        for sandbox in self._sandbox_paths:
            abs_sandbox = os.path.abspath(sandbox)
            if abs_path.startswith(abs_sandbox + os.sep) or abs_path == abs_sandbox:
                return

        raise PermissionDeniedError(
            action=action,
            module=module,
            profile=self._profile.profile.value,
        )
