"""Unit tests — PermissionGuard."""

import pytest

from llmos_bridge.exceptions import ApprovalRequiredError, PermissionDeniedError
from llmos_bridge.protocol.models import IMLAction, IMLPlan
from llmos_bridge.security.guard import PermissionGuard
from llmos_bridge.security.profiles import PermissionProfile, get_profile_config


def _action(module: str, action: str, action_id: str = "a1", requires_approval: bool = False) -> IMLAction:
    return IMLAction(
        id=action_id,
        action=action,
        module=module,
        params={},
        requires_approval=requires_approval,
    )


def _plan(*actions: IMLAction) -> IMLPlan:
    return IMLPlan(plan_id="g-test", description="Guard test", actions=list(actions))


class TestReadonlyProfile:
    @pytest.fixture
    def guard(self) -> PermissionGuard:
        return PermissionGuard(profile=get_profile_config(PermissionProfile.READONLY))

    def test_read_file_allowed(self, guard: PermissionGuard) -> None:
        action = _action("filesystem", "read_file")
        guard.check_action(action, plan_id="p1")  # Should not raise

    def test_write_file_denied(self, guard: PermissionGuard) -> None:
        action = _action("filesystem", "write_file")
        with pytest.raises(PermissionDeniedError) as exc:
            guard.check_action(action, plan_id="p1")
        assert exc.value.profile == "readonly"

    def test_run_command_denied(self, guard: PermissionGuard) -> None:
        action = _action("os_exec", "run_command")
        with pytest.raises(PermissionDeniedError):
            guard.check_action(action, plan_id="p1")

    def test_plan_preflight_fails_on_disallowed(self, guard: PermissionGuard) -> None:
        plan = _plan(_action("filesystem", "delete_file"))
        with pytest.raises(PermissionDeniedError):
            guard.check_plan(plan)


class TestLocalWorkerProfile:
    @pytest.fixture
    def guard(self) -> PermissionGuard:
        return PermissionGuard(
            profile=get_profile_config(PermissionProfile.LOCAL_WORKER),
            require_approval_for=["filesystem.delete_file"],
        )

    def test_read_file_allowed(self, guard: PermissionGuard) -> None:
        guard.check_action(_action("filesystem", "read_file"), plan_id="p1")

    def test_write_file_allowed(self, guard: PermissionGuard) -> None:
        guard.check_action(_action("filesystem", "write_file"), plan_id="p1")

    def test_delete_file_requires_approval(self, guard: PermissionGuard) -> None:
        action = _action("filesystem", "delete_file")
        with pytest.raises(ApprovalRequiredError) as exc:
            guard.check_action(action, plan_id="p1")
        assert exc.value.action_id == "a1"

    def test_action_flag_requires_approval(self, guard: PermissionGuard) -> None:
        action = _action("filesystem", "write_file", requires_approval=True)
        with pytest.raises(ApprovalRequiredError):
            guard.check_action(action, plan_id="p1")


class TestUnrestrictedProfile:
    @pytest.fixture
    def guard(self) -> PermissionGuard:
        return PermissionGuard(profile=get_profile_config(PermissionProfile.UNRESTRICTED))

    def test_everything_allowed(self, guard: PermissionGuard) -> None:
        for module, action in [
            ("filesystem", "delete_file"),
            ("os_exec", "kill_process"),
            ("database", "delete_record"),
            ("browser", "navigate_to"),
            ("gui", "click_position"),
        ]:
            guard.check_action(_action(module, action), plan_id="p1")

    def test_approval_bypass(self, guard: PermissionGuard) -> None:
        action = _action("filesystem", "delete_file", requires_approval=True)
        guard.check_action(action, plan_id="p1")  # Should not raise


class TestSandboxPaths:
    @pytest.fixture
    def guard(self, tmp_path: "Path") -> PermissionGuard:
        return PermissionGuard(
            profile=get_profile_config(PermissionProfile.LOCAL_WORKER),
            sandbox_paths=[str(tmp_path)],
        )

    def test_path_inside_sandbox_allowed(self, guard: PermissionGuard, tmp_path: "Path") -> None:
        action = IMLAction(
            id="a1",
            action="write_file",
            module="filesystem",
            params={"path": str(tmp_path / "output.txt"), "content": "x"},
        )
        guard.check_action(action, plan_id="p1")

    def test_path_outside_sandbox_denied(self, guard: PermissionGuard) -> None:
        action = IMLAction(
            id="a1",
            action="write_file",
            module="filesystem",
            params={"path": "/etc/passwd", "content": "x"},
        )
        with pytest.raises(PermissionDeniedError):
            guard.check_action(action, plan_id="p1")

    def test_template_path_skips_sandbox_check(self, guard: PermissionGuard) -> None:
        action = IMLAction(
            id="a1",
            action="write_file",
            module="filesystem",
            params={"path": "{{result.a0.path}}", "content": "x"},
        )
        guard.check_action(action, plan_id="p1")  # Template paths are deferred


class TestPlanActionLimit:
    def test_plan_exceeds_limit_denied(self) -> None:
        guard = PermissionGuard(profile=get_profile_config(PermissionProfile.READONLY))
        actions = [
            IMLAction(
                id=f"a{i}",
                action="read_file",
                module="filesystem",
                params={"path": f"/tmp/{i}"},
            )
            for i in range(25)  # ReadOnly max is 20
        ]
        plan = IMLPlan(plan_id="big-plan", description="Too big", actions=actions)
        with pytest.raises(PermissionDeniedError):
            guard.check_plan(plan)
