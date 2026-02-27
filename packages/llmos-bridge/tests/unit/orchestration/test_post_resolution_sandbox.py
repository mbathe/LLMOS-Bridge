"""Unit tests for post-resolution sandbox enforcement.

After template resolution the executor calls ``guard.check_sandbox_params()``
to re-validate resolved paths.  These tests verify that method directly
without spinning up the full executor, keeping them fast and focused.
"""

from __future__ import annotations

import pytest

from llmos_bridge.exceptions import PermissionDeniedError
from llmos_bridge.security.guard import PermissionGuard, _PATH_PARAM_KEYS
from llmos_bridge.security.profiles import PermissionProfileConfig, PermissionProfile


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def sandboxed_guard() -> PermissionGuard:
    """Guard configured with a single sandbox directory."""
    profile = PermissionProfileConfig(
        profile=PermissionProfile.POWER_USER,
        allowed_patterns=frozenset(["*.*"]),
        max_plan_actions=200,
    )
    return PermissionGuard(
        profile=profile,
        sandbox_paths=["/home/user/safe"],
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPostResolutionSandbox:
    def test_check_sandbox_params_called_with_resolved_params(
        self, sandboxed_guard: PermissionGuard
    ) -> None:
        """Simulate the executor flow: after template resolution,
        check_sandbox_params is called with concrete values.
        Verify it catches an unsafe resolved path."""
        # Before resolution the param was "{{result.a1.path}}"
        # After resolution it became "/etc/passwd"
        resolved_params = {"path": "/etc/passwd"}
        with pytest.raises(PermissionDeniedError) as exc_info:
            sandboxed_guard.check_sandbox_params("filesystem", "read_file", resolved_params)
        assert exc_info.value.module == "filesystem"
        assert exc_info.value.action == "read_file"

    def test_resolved_path_outside_sandbox_raises(
        self, sandboxed_guard: PermissionGuard
    ) -> None:
        """A resolved path outside the sandbox must be rejected."""
        with pytest.raises(PermissionDeniedError) as exc_info:
            sandboxed_guard.check_sandbox_params(
                "excel", "open_workbook", {"path": "/var/log/secret.xlsx"}
            )
        assert exc_info.value.module == "excel"
        assert exc_info.value.action == "open_workbook"
        assert exc_info.value.profile == "power_user"

    def test_resolved_path_inside_sandbox_passes(
        self, sandboxed_guard: PermissionGuard
    ) -> None:
        """A resolved path inside the sandbox is allowed."""
        # Should not raise
        sandboxed_guard.check_sandbox_params(
            "excel", "open_workbook", {"path": "/home/user/safe/report.xlsx"}
        )

    def test_multiple_path_params_all_checked(
        self, sandboxed_guard: PermissionGuard
    ) -> None:
        """When params contain multiple path keys, ALL are checked.
        If any one is outside the sandbox, the call must fail."""
        params = {
            "path": "/home/user/safe/input.xlsx",       # safe
            "output_path": "/tmp/exfiltrated.xlsx",      # outside sandbox
        }
        with pytest.raises(PermissionDeniedError) as exc_info:
            sandboxed_guard.check_sandbox_params("excel", "save_workbook", params)
        # The error should reference the module and action
        assert exc_info.value.module == "excel"
        assert exc_info.value.action == "save_workbook"

    def test_database_param_checked(
        self, sandboxed_guard: PermissionGuard
    ) -> None:
        """The 'database' param key should also be sandbox-checked."""
        assert "database" in _PATH_PARAM_KEYS
        with pytest.raises(PermissionDeniedError) as exc_info:
            sandboxed_guard.check_sandbox_params(
                "database", "connect", {"database": "/etc/secret.db"}
            )
        assert exc_info.value.module == "database"
        assert exc_info.value.action == "connect"

    def test_image_path_checked(
        self, sandboxed_guard: PermissionGuard
    ) -> None:
        """The 'image_path' param key should also be sandbox-checked."""
        assert "image_path" in _PATH_PARAM_KEYS
        with pytest.raises(PermissionDeniedError) as exc_info:
            sandboxed_guard.check_sandbox_params(
                "powerpoint", "add_image", {"image_path": "/etc/shadow"}
            )
        assert exc_info.value.module == "powerpoint"
        assert exc_info.value.action == "add_image"
