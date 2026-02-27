"""Unit tests for PermissionGuard sandbox path enforcement.

Tests cover:
- Sandbox checking across ALL modules (not just filesystem)
- All path param keys (_PATH_PARAM_KEYS)
- Template expression skipping in pre-flight check
- Post-resolution check via check_sandbox_params()
- No-sandbox-configured permissive behavior
"""

from __future__ import annotations

import pytest

from llmos_bridge.exceptions import PermissionDeniedError
from llmos_bridge.protocol.models import IMLAction
from llmos_bridge.security.guard import PermissionGuard, _PATH_PARAM_KEYS
from llmos_bridge.security.profiles import PermissionProfileConfig, PermissionProfile


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def sandboxed_guard() -> PermissionGuard:
    """Guard with power_user profile and a single sandbox path."""
    profile = PermissionProfileConfig(
        profile=PermissionProfile.POWER_USER,
        allowed_patterns=frozenset(["*.*"]),
        max_plan_actions=200,
    )
    return PermissionGuard(
        profile=profile,
        sandbox_paths=["/home/user/safe"],
    )


@pytest.fixture()
def open_guard() -> PermissionGuard:
    """Guard with no sandbox configured (everything allowed)."""
    profile = PermissionProfileConfig(
        profile=PermissionProfile.POWER_USER,
        allowed_patterns=frozenset(["*.*"]),
        max_plan_actions=200,
    )
    return PermissionGuard(
        profile=profile,
        sandbox_paths=[],
    )


# ---------------------------------------------------------------------------
# Filesystem module — classic sandbox checks
# ---------------------------------------------------------------------------


class TestFilesystemSandbox:
    def test_filesystem_path_inside_sandbox_allowed(self, sandboxed_guard: PermissionGuard) -> None:
        action = IMLAction(
            id="a1",
            action="read_file",
            module="filesystem",
            params={"path": "/home/user/safe/file.txt"},
        )
        # Should not raise
        sandboxed_guard.check_action(action, plan_id="p1")

    def test_filesystem_path_outside_sandbox_blocked(self, sandboxed_guard: PermissionGuard) -> None:
        action = IMLAction(
            id="a1",
            action="read_file",
            module="filesystem",
            params={"path": "/etc/passwd"},
        )
        with pytest.raises(PermissionDeniedError) as exc_info:
            sandboxed_guard.check_action(action, plan_id="p1")
        assert exc_info.value.module == "filesystem"
        assert exc_info.value.action == "read_file"


# ---------------------------------------------------------------------------
# Non-filesystem modules — sandbox applies to ALL modules
# ---------------------------------------------------------------------------


class TestCrossModuleSandbox:
    def test_excel_path_inside_sandbox_allowed(self, sandboxed_guard: PermissionGuard) -> None:
        action = IMLAction(
            id="a1",
            action="open_workbook",
            module="excel",
            params={"path": "/home/user/safe/book.xlsx"},
        )
        # Should not raise
        sandboxed_guard.check_action(action, plan_id="p1")

    def test_excel_path_outside_sandbox_blocked(self, sandboxed_guard: PermissionGuard) -> None:
        action = IMLAction(
            id="a1",
            action="open_workbook",
            module="excel",
            params={"path": "/etc/shadow"},
        )
        with pytest.raises(PermissionDeniedError) as exc_info:
            sandboxed_guard.check_action(action, plan_id="p1")
        assert exc_info.value.module == "excel"
        assert exc_info.value.action == "open_workbook"

    def test_word_output_path_outside_sandbox_blocked(self, sandboxed_guard: PermissionGuard) -> None:
        action = IMLAction(
            id="a1",
            action="save_document",
            module="word",
            params={"output_path": "/tmp/evil.docx"},
        )
        with pytest.raises(PermissionDeniedError) as exc_info:
            sandboxed_guard.check_action(action, plan_id="p1")
        assert exc_info.value.module == "word"
        assert exc_info.value.action == "save_document"


# ---------------------------------------------------------------------------
# Template expression handling
# ---------------------------------------------------------------------------


class TestTemplateSkipping:
    def test_template_path_skipped_in_check_action(self, sandboxed_guard: PermissionGuard) -> None:
        """Paths containing {{...}} template expressions are skipped during
        pre-flight sandbox check — they will be validated after resolution."""
        action = IMLAction(
            id="a2",
            action="open_workbook",
            module="excel",
            params={"path": "{{result.a1.path}}"},
        )
        # Should NOT raise even though the path doesn't match any sandbox
        sandboxed_guard.check_action(action, plan_id="p1")


# ---------------------------------------------------------------------------
# check_sandbox_params — post-resolution validation
# ---------------------------------------------------------------------------


class TestCheckSandboxParams:
    def test_check_sandbox_params_blocks_resolved_template(
        self, sandboxed_guard: PermissionGuard
    ) -> None:
        """After template resolution, paths outside sandbox are rejected."""
        with pytest.raises(PermissionDeniedError) as exc_info:
            sandboxed_guard.check_sandbox_params(
                "excel", "open_workbook", {"path": "/etc/passwd"}
            )
        assert exc_info.value.module == "excel"
        assert exc_info.value.action == "open_workbook"

    def test_check_sandbox_params_allows_safe_path(
        self, sandboxed_guard: PermissionGuard
    ) -> None:
        # Should not raise
        sandboxed_guard.check_sandbox_params(
            "excel", "open_workbook", {"path": "/home/user/safe/x.xlsx"}
        )

    def test_check_sandbox_params_skips_still_unresolved(
        self, sandboxed_guard: PermissionGuard
    ) -> None:
        """If a template expression is still present (partial resolution),
        it should be skipped — not treated as a literal path."""
        # Should not raise
        sandboxed_guard.check_sandbox_params(
            "excel", "open_workbook", {"path": "{{result.a1.output_path}}"}
        )


# ---------------------------------------------------------------------------
# No sandbox configured — permissive mode
# ---------------------------------------------------------------------------


class TestNoSandbox:
    def test_no_sandbox_configured_allows_all(self, open_guard: PermissionGuard) -> None:
        """When sandbox_paths is empty, all paths are allowed."""
        action = IMLAction(
            id="a1",
            action="read_file",
            module="filesystem",
            params={"path": "/etc/passwd"},
        )
        # Should not raise
        open_guard.check_action(action, plan_id="p1")

    def test_no_sandbox_check_sandbox_params_allows_all(
        self, open_guard: PermissionGuard
    ) -> None:
        """check_sandbox_params also allows everything when no sandbox is set."""
        # Should not raise
        open_guard.check_sandbox_params(
            "excel", "open_workbook", {"path": "/etc/shadow"}
        )
