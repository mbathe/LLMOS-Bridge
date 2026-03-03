"""Unit tests — AuthorizationGuard (identity-based authorization matrix)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from llmos_bridge.exceptions import ApplicationNotFoundError, AuthorizationError, QuotaExceededError
from llmos_bridge.identity.authorization import ROLE_HIERARCHY, AuthorizationGuard
from llmos_bridge.identity.models import Application, IdentityContext, Role
from llmos_bridge.identity.store import IdentityStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _app(
    app_id: str = "app1",
    enabled: bool = True,
    allowed_modules: list[str] | None = None,
    allowed_actions: dict[str, list[str]] | None = None,
    max_concurrent_plans: int = 10,
    max_actions_per_plan: int = 50,
) -> Application:
    return Application(
        app_id=app_id,
        name=app_id,
        enabled=enabled,
        allowed_modules=allowed_modules or [],
        allowed_actions=allowed_actions or {},
        max_concurrent_plans=max_concurrent_plans,
        max_actions_per_plan=max_actions_per_plan,
    )


def _identity(
    app_id: str = "app1",
    role: Role = Role.ADMIN,
    session_id: str | None = None,
) -> IdentityContext:
    return IdentityContext(app_id=app_id, role=role, session_id=session_id)


def _plan(n_actions: int = 3) -> MagicMock:
    plan = MagicMock()
    plan.plan_id = "plan-1"
    plan.actions = [MagicMock(module="filesystem", action=f"action_{i}") for i in range(n_actions)]
    return plan


# ---------------------------------------------------------------------------
# Disabled guard (standalone mode)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAuthorizationGuardDisabled:
    """When enabled=False, all methods must be no-ops."""

    def setup_method(self) -> None:
        self.guard = AuthorizationGuard(store=None, enabled=False)

    async def test_check_plan_submission_returns_none(self) -> None:
        result = await self.guard.check_plan_submission(_identity(), _plan())
        assert result is None

    def test_check_action_allowed_always_passes(self) -> None:
        app = _app(allowed_modules=["filesystem"], allowed_actions={"filesystem": ["read_file"]})
        # Even if app has strict allowlists, disabled guard never raises
        self.guard.check_action_allowed(app, "os_exec", "run_command")

    def test_require_role_always_passes(self) -> None:
        identity = _identity(role=Role.AGENT)
        self.guard.require_role(identity, Role.ADMIN, resource="test")

    def test_require_app_scope_always_passes(self) -> None:
        identity = _identity(app_id="app1")
        self.guard.require_app_scope(identity, "completely_different_app")

    async def test_check_session_binding_always_passes(self) -> None:
        identity = _identity(session_id="sess-1")
        await self.guard.check_session_binding(identity)


# ---------------------------------------------------------------------------
# Enabled guard — check_plan_submission
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCheckPlanSubmission:
    """Tests for check_plan_submission with a real SQLite store."""

    @pytest.fixture(autouse=True)
    async def _setup(self, tmp_path: Path) -> None:
        self.store = IdentityStore(tmp_path / "identity.db")
        await self.store.init()
        self.guard = AuthorizationGuard(store=self.store, enabled=True)
        yield
        await self.store.close()

    async def test_raises_app_not_found(self) -> None:
        identity = _identity(app_id="nonexistent")
        with pytest.raises(ApplicationNotFoundError):
            await self.guard.check_plan_submission(identity, _plan())

    async def test_raises_when_app_disabled(self) -> None:
        await self.store.create_application(name="disabled_app", app_id="disabled_app")
        await self.store.update_application("disabled_app", enabled=False)
        identity = _identity(app_id="disabled_app")
        with pytest.raises(AuthorizationError):
            await self.guard.check_plan_submission(identity, _plan())

    async def test_passes_with_valid_app_and_admin_role(self) -> None:
        await self.store.create_application(name="myapp", app_id="myapp")
        identity = _identity(app_id="myapp", role=Role.ADMIN)
        result = await self.guard.check_plan_submission(identity, _plan())
        assert result is not None
        assert result.app_id == "myapp"

    async def test_passes_with_agent_role(self) -> None:
        await self.store.create_application(name="myapp", app_id="myapp")
        identity = _identity(app_id="myapp", role=Role.AGENT)
        result = await self.guard.check_plan_submission(identity, _plan())
        assert result is not None

    async def test_raises_quota_actions_per_plan(self) -> None:
        await self.store.create_application(
            name="myapp", app_id="myapp", max_actions_per_plan=2
        )
        identity = _identity(app_id="myapp")
        with pytest.raises(QuotaExceededError) as exc_info:
            await self.guard.check_plan_submission(identity, _plan(n_actions=3))
        assert exc_info.value.resource == "max_actions_per_plan"

    async def test_raises_quota_concurrent_plans(self) -> None:
        await self.store.create_application(
            name="myapp", app_id="myapp", max_concurrent_plans=1
        )
        identity = _identity(app_id="myapp")
        self.guard.plan_started("myapp")
        with pytest.raises(QuotaExceededError) as exc_info:
            await self.guard.check_plan_submission(identity, _plan())
        assert exc_info.value.resource == "max_concurrent_plans"

    async def test_raises_when_module_not_in_allowlist(self) -> None:
        await self.store.create_application(
            name="myapp", app_id="myapp", allowed_modules=["filesystem"]
        )
        identity = _identity(app_id="myapp")
        plan = MagicMock()
        plan.plan_id = "plan-x"
        plan.actions = [MagicMock(module="os_exec", action="run_command")]
        with pytest.raises(AuthorizationError):
            await self.guard.check_plan_submission(identity, plan)

    async def test_passes_when_module_in_allowlist(self) -> None:
        await self.store.create_application(
            name="myapp", app_id="myapp", allowed_modules=["filesystem"]
        )
        identity = _identity(app_id="myapp")
        plan = MagicMock()
        plan.plan_id = "plan-x"
        plan.actions = [MagicMock(module="filesystem", action="read_file")]
        result = await self.guard.check_plan_submission(identity, plan)
        assert result is not None

    async def test_raises_when_action_not_in_action_allowlist(self) -> None:
        await self.store.create_application(
            name="myapp", app_id="myapp",
            allowed_actions={"filesystem": ["read_file"]},
        )
        identity = _identity(app_id="myapp")
        plan = MagicMock()
        plan.plan_id = "plan-x"
        plan.actions = [MagicMock(module="filesystem", action="delete_file")]
        with pytest.raises(AuthorizationError):
            await self.guard.check_plan_submission(identity, plan)

    async def test_passes_when_action_in_action_allowlist(self) -> None:
        await self.store.create_application(
            name="myapp", app_id="myapp",
            allowed_actions={"filesystem": ["read_file", "list_directory"]},
        )
        identity = _identity(app_id="myapp")
        plan = MagicMock()
        plan.plan_id = "plan-x"
        plan.actions = [MagicMock(module="filesystem", action="read_file")]
        result = await self.guard.check_plan_submission(identity, plan)
        assert result is not None


# ---------------------------------------------------------------------------
# check_action_allowed (sync, uses cached Application)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCheckActionAllowed:

    def setup_method(self) -> None:
        self.guard = AuthorizationGuard(store=None, enabled=True)

    def test_empty_allowlists_pass_everything(self) -> None:
        app = _app()
        self.guard.check_action_allowed(app, "os_exec", "run_command")

    def test_module_whitelist_blocks_unlisted_module(self) -> None:
        app = _app(allowed_modules=["filesystem"])
        with pytest.raises(AuthorizationError):
            self.guard.check_action_allowed(app, "os_exec", "run_command")

    def test_module_whitelist_allows_listed_module(self) -> None:
        app = _app(allowed_modules=["filesystem"])
        self.guard.check_action_allowed(app, "filesystem", "read_file")

    def test_action_whitelist_blocks_unlisted_action(self) -> None:
        app = _app(allowed_actions={"filesystem": ["read_file"]})
        with pytest.raises(AuthorizationError):
            self.guard.check_action_allowed(app, "filesystem", "delete_file")

    def test_action_whitelist_allows_listed_action(self) -> None:
        app = _app(allowed_actions={"filesystem": ["read_file", "write_file"]})
        self.guard.check_action_allowed(app, "filesystem", "read_file")

    def test_no_entry_for_module_allows_all_actions(self) -> None:
        # allowed_actions only restricts os_exec; filesystem is unrestricted
        app = _app(allowed_actions={"os_exec": ["run_command"]})
        self.guard.check_action_allowed(app, "filesystem", "delete_file")

    def test_empty_action_list_for_module_allows_all(self) -> None:
        # Explicit empty list = all actions allowed for that module
        app = _app(allowed_actions={"filesystem": []})
        self.guard.check_action_allowed(app, "filesystem", "delete_file")

    def test_combined_module_and_action_whitelist(self) -> None:
        app = _app(
            allowed_modules=["filesystem"],
            allowed_actions={"filesystem": ["read_file"]},
        )
        with pytest.raises(AuthorizationError):
            self.guard.check_action_allowed(app, "os_exec", "run_command")
        with pytest.raises(AuthorizationError):
            self.guard.check_action_allowed(app, "filesystem", "delete_file")
        self.guard.check_action_allowed(app, "filesystem", "read_file")

    def test_disabled_guard_always_passes(self) -> None:
        guard = AuthorizationGuard(store=None, enabled=False)
        app = _app(
            allowed_modules=["filesystem"],
            allowed_actions={"filesystem": ["read_file"]},
        )
        guard.check_action_allowed(app, "os_exec", "run_command")


# ---------------------------------------------------------------------------
# require_role
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRequireRole:

    def setup_method(self) -> None:
        self.guard = AuthorizationGuard(store=None, enabled=True)

    @pytest.mark.parametrize("role,minimum,should_pass", [
        (Role.ADMIN, Role.ADMIN, True),
        (Role.ADMIN, Role.APP_ADMIN, True),
        (Role.ADMIN, Role.OPERATOR, True),
        (Role.ADMIN, Role.VIEWER, True),
        (Role.ADMIN, Role.AGENT, True),
        (Role.APP_ADMIN, Role.ADMIN, False),
        (Role.APP_ADMIN, Role.APP_ADMIN, True),
        (Role.APP_ADMIN, Role.OPERATOR, True),
        (Role.OPERATOR, Role.ADMIN, False),
        (Role.OPERATOR, Role.APP_ADMIN, False),
        (Role.OPERATOR, Role.OPERATOR, True),
        (Role.OPERATOR, Role.VIEWER, True),
        (Role.OPERATOR, Role.AGENT, True),
        (Role.VIEWER, Role.OPERATOR, False),
        (Role.VIEWER, Role.VIEWER, True),
        (Role.VIEWER, Role.AGENT, True),
        (Role.AGENT, Role.VIEWER, False),
        (Role.AGENT, Role.AGENT, True),
    ])
    def test_role_hierarchy(self, role: Role, minimum: Role, should_pass: bool) -> None:
        identity = _identity(role=role)
        if should_pass:
            self.guard.require_role(identity, minimum)
        else:
            with pytest.raises(AuthorizationError) as exc_info:
                self.guard.require_role(identity, minimum)
            assert exc_info.value.role == role.value
            assert exc_info.value.required == minimum.value

    def test_role_hierarchy_constant_is_ordered(self) -> None:
        expected = [Role.ADMIN, Role.APP_ADMIN, Role.OPERATOR, Role.VIEWER, Role.AGENT]
        assert ROLE_HIERARCHY == expected


# ---------------------------------------------------------------------------
# require_app_scope
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRequireAppScope:

    def setup_method(self) -> None:
        self.guard = AuthorizationGuard(store=None, enabled=True)

    def test_admin_bypasses_app_scope(self) -> None:
        identity = _identity(app_id="app1", role=Role.ADMIN)
        self.guard.require_app_scope(identity, "app2")  # no error

    def test_app_admin_can_manage_own_app(self) -> None:
        identity = _identity(app_id="app1", role=Role.APP_ADMIN)
        self.guard.require_app_scope(identity, "app1")  # no error

    def test_app_admin_cannot_manage_other_app(self) -> None:
        identity = _identity(app_id="app1", role=Role.APP_ADMIN)
        with pytest.raises(AuthorizationError):
            self.guard.require_app_scope(identity, "app2")

    def test_operator_cannot_manage_other_app(self) -> None:
        identity = _identity(app_id="app1", role=Role.OPERATOR)
        with pytest.raises(AuthorizationError):
            self.guard.require_app_scope(identity, "app2")


# ---------------------------------------------------------------------------
# Session binding
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSessionBinding:

    @pytest.fixture(autouse=True)
    async def _setup(self, tmp_path: Path) -> None:
        self.store = IdentityStore(tmp_path / "identity.db")
        await self.store.init()
        self.guard = AuthorizationGuard(store=self.store, enabled=True)
        yield
        await self.store.close()

    async def test_no_session_id_passes(self) -> None:
        identity = _identity(session_id=None)
        await self.guard.check_session_binding(identity)

    async def test_unknown_session_id_passes(self) -> None:
        identity = _identity(session_id="nonexistent-session")
        await self.guard.check_session_binding(identity)

    async def test_session_in_correct_app_passes(self) -> None:
        await self.store.create_application(name="myapp", app_id="myapp")
        await self.store.create_session(app_id="myapp", session_id="sess-1")
        identity = _identity(app_id="myapp", session_id="sess-1")
        await self.guard.check_session_binding(identity)

    async def test_session_in_wrong_app_raises(self) -> None:
        await self.store.create_application(name="app1", app_id="app1")
        await self.store.create_application(name="app2", app_id="app2")
        await self.store.create_session(app_id="app1", session_id="sess-1")
        identity = _identity(app_id="app2", session_id="sess-1")
        with pytest.raises(AuthorizationError):
            await self.guard.check_session_binding(identity)


# ---------------------------------------------------------------------------
# Active plan counter
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestActivePlanCounter:

    def setup_method(self) -> None:
        self.guard = AuthorizationGuard(store=None, enabled=False)

    def test_initial_count_is_zero(self) -> None:
        assert self.guard.active_plan_count("app1") == 0

    def test_plan_started_increments(self) -> None:
        self.guard.plan_started("app1")
        self.guard.plan_started("app1")
        assert self.guard.active_plan_count("app1") == 2

    def test_plan_finished_decrements(self) -> None:
        self.guard.plan_started("app1")
        self.guard.plan_started("app1")
        self.guard.plan_finished("app1")
        assert self.guard.active_plan_count("app1") == 1

    def test_plan_finished_does_not_go_below_zero(self) -> None:
        self.guard.plan_finished("app1")
        assert self.guard.active_plan_count("app1") == 0

    def test_independent_counters_per_app(self) -> None:
        self.guard.plan_started("app1")
        self.guard.plan_started("app2")
        self.guard.plan_started("app2")
        assert self.guard.active_plan_count("app1") == 1
        assert self.guard.active_plan_count("app2") == 2
