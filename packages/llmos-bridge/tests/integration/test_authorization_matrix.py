"""Integration tests — Authorization matrix (end-to-end enforcement).

Tests verify that the authorization guard correctly gates plan execution
when integrated with the executor and identity store.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llmos_bridge.exceptions import (
    ApplicationNotFoundError,
    AuthorizationError,
    QuotaExceededError,
)
from llmos_bridge.identity.authorization import AuthorizationGuard
from llmos_bridge.identity.models import Application, IdentityContext, Role
from llmos_bridge.identity.store import IdentityStore


@pytest.fixture
async def identity_store(tmp_path: Path):
    """Create and initialise a temporary identity store."""
    store = IdentityStore(tmp_path / "identity.db")
    await store.init()
    yield store
    await store.close()


# ---------------------------------------------------------------------------
# Authorization with real IdentityStore
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestAuthorizationWithStore:
    """Full integration: AuthorizationGuard + IdentityStore + mock plan."""

    async def test_zero_config_default_app(self, identity_store: IdentityStore) -> None:
        """Default app with no restrictions should allow everything."""
        await identity_store.ensure_default_app()
        guard = AuthorizationGuard(store=identity_store, enabled=True)

        identity = IdentityContext(app_id="default", role=Role.ADMIN)
        plan = _mock_plan([("filesystem", "read_file"), ("os_exec", "run_command")])

        app = await guard.check_plan_submission(identity, plan)
        assert app is not None
        assert app.app_id == "default"
        # All actions pass
        guard.check_action_allowed(app, "filesystem", "read_file")
        guard.check_action_allowed(app, "os_exec", "run_command")

    async def test_module_whitelist_enforcement(self, identity_store: IdentityStore) -> None:
        """Only whitelisted modules should be allowed."""
        await identity_store.create_application(
            name="fs-only",
            app_id="fs-only",
            allowed_modules=["filesystem"],
        )
        guard = AuthorizationGuard(store=identity_store, enabled=True)
        identity = IdentityContext(app_id="fs-only", role=Role.OPERATOR)

        # Plan with allowed module → pass
        plan_ok = _mock_plan([("filesystem", "read_file")])
        app = await guard.check_plan_submission(identity, plan_ok)
        assert app is not None

        # Plan with disallowed module → fail
        plan_bad = _mock_plan([("os_exec", "run_command")])
        with pytest.raises(AuthorizationError):
            await guard.check_plan_submission(identity, plan_bad)

    async def test_action_whitelist_enforcement(self, identity_store: IdentityStore) -> None:
        """Only whitelisted actions for a module should be allowed."""
        await identity_store.create_application(
            name="read-only",
            app_id="read-only",
            allowed_actions={"filesystem": ["read_file", "list_directory"]},
        )
        guard = AuthorizationGuard(store=identity_store, enabled=True)
        identity = IdentityContext(app_id="read-only", role=Role.OPERATOR)

        # Allowed action → pass
        plan_ok = _mock_plan([("filesystem", "read_file")])
        app = await guard.check_plan_submission(identity, plan_ok)
        assert app is not None

        # Disallowed action → fail
        plan_bad = _mock_plan([("filesystem", "delete_file")])
        with pytest.raises(AuthorizationError):
            await guard.check_plan_submission(identity, plan_bad)

    async def test_combined_module_and_action_whitelist(self, identity_store: IdentityStore) -> None:
        """Both module and action whitelists work together."""
        await identity_store.create_application(
            name="strict",
            app_id="strict",
            allowed_modules=["filesystem", "database"],
            allowed_actions={
                "filesystem": ["read_file"],
                "database": ["execute_query"],
            },
        )
        guard = AuthorizationGuard(store=identity_store, enabled=True)
        identity = IdentityContext(app_id="strict", role=Role.OPERATOR)

        # Both allowed → pass
        plan_ok = _mock_plan([("filesystem", "read_file"), ("database", "execute_query")])
        app = await guard.check_plan_submission(identity, plan_ok)
        assert app is not None

        # Module allowed but action denied
        plan_bad1 = _mock_plan([("filesystem", "write_file")])
        with pytest.raises(AuthorizationError):
            await guard.check_plan_submission(identity, plan_bad1)

        # Module denied entirely
        plan_bad2 = _mock_plan([("os_exec", "run_command")])
        with pytest.raises(AuthorizationError):
            await guard.check_plan_submission(identity, plan_bad2)

    async def test_concurrent_plan_quota(self, identity_store: IdentityStore) -> None:
        """Exceeding concurrent plan limit should raise QuotaExceededError."""
        await identity_store.create_application(
            name="limited",
            app_id="limited",
            max_concurrent_plans=2,
        )
        guard = AuthorizationGuard(store=identity_store, enabled=True)
        identity = IdentityContext(app_id="limited", role=Role.OPERATOR)
        plan = _mock_plan([("filesystem", "read_file")])

        # First two plans succeed
        app = await guard.check_plan_submission(identity, plan)
        guard.plan_started("limited")
        app = await guard.check_plan_submission(identity, plan)
        guard.plan_started("limited")

        # Third plan exceeds quota
        with pytest.raises(QuotaExceededError, match="max_concurrent_plans"):
            await guard.check_plan_submission(identity, plan)

        # Finish one plan — should allow again
        guard.plan_finished("limited")
        app = await guard.check_plan_submission(identity, plan)
        assert app is not None

    async def test_action_count_quota(self, identity_store: IdentityStore) -> None:
        """Plans with too many actions should be rejected."""
        await identity_store.create_application(
            name="small",
            app_id="small",
            max_actions_per_plan=2,
        )
        guard = AuthorizationGuard(store=identity_store, enabled=True)
        identity = IdentityContext(app_id="small", role=Role.OPERATOR)

        # 2 actions → OK
        plan_ok = _mock_plan([("filesystem", "read_file"), ("filesystem", "list_directory")])
        app = await guard.check_plan_submission(identity, plan_ok)
        assert app is not None

        # 3 actions → exceeds
        plan_big = _mock_plan([
            ("filesystem", "read_file"),
            ("filesystem", "list_directory"),
            ("filesystem", "write_file"),
        ])
        with pytest.raises(QuotaExceededError, match="max_actions_per_plan"):
            await guard.check_plan_submission(identity, plan_big)

    async def test_disabled_app_rejected(self, identity_store: IdentityStore) -> None:
        """Disabled applications should reject plan submissions."""
        await identity_store.create_application(name="off", app_id="off")
        await identity_store.update_application("off", enabled=False)
        guard = AuthorizationGuard(store=identity_store, enabled=True)
        identity = IdentityContext(app_id="off", role=Role.ADMIN)
        plan = _mock_plan([("filesystem", "read_file")])
        with pytest.raises(AuthorizationError):
            await guard.check_plan_submission(identity, plan)

    async def test_session_binding_enforcement(self, identity_store: IdentityStore) -> None:
        """Sessions must belong to the correct application."""
        await identity_store.create_application(name="App1", app_id="app-1")
        await identity_store.create_application(name="App2", app_id="app-2")
        await identity_store.create_session(app_id="app-1", session_id="sess-1")

        guard = AuthorizationGuard(store=identity_store, enabled=True)

        # Correct binding → pass
        identity_ok = IdentityContext(
            app_id="app-1", role=Role.OPERATOR, session_id="sess-1",
        )
        plan = _mock_plan([("filesystem", "read_file")])
        app = await guard.check_plan_submission(identity_ok, plan)
        assert app is not None

        # Wrong binding → fail
        identity_bad = IdentityContext(
            app_id="app-2", role=Role.OPERATOR, session_id="sess-1",
        )
        with pytest.raises(AuthorizationError):
            await guard.check_plan_submission(identity_bad, plan)


# ---------------------------------------------------------------------------
# RBAC on API routes (simulated)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestRBACEnforcement:
    """RBAC role checks as used by API routes."""

    async def test_role_hierarchy_complete(self, identity_store: IdentityStore) -> None:
        guard = AuthorizationGuard(store=identity_store, enabled=True)

        # ADMIN can do everything
        admin = IdentityContext(app_id="default", role=Role.ADMIN)
        guard.require_role(admin, Role.ADMIN)
        guard.require_role(admin, Role.VIEWER)
        guard.require_role(admin, Role.AGENT)

        # VIEWER cannot create applications
        viewer = IdentityContext(app_id="default", role=Role.VIEWER)
        with pytest.raises(AuthorizationError):
            guard.require_role(viewer, Role.ADMIN, resource="create_application")

        # OPERATOR can submit plans but not manage apps
        operator = IdentityContext(app_id="default", role=Role.OPERATOR)
        guard.require_role(operator, Role.OPERATOR)
        with pytest.raises(AuthorizationError):
            guard.require_role(operator, Role.APP_ADMIN, resource="update_application")

    async def test_app_scope_isolation(self, identity_store: IdentityStore) -> None:
        guard = AuthorizationGuard(store=identity_store, enabled=True)

        # APP_ADMIN scoped to app-A
        app_admin = IdentityContext(app_id="app-A", role=Role.APP_ADMIN)
        guard.require_app_scope(app_admin, "app-A")  # Same app → OK

        with pytest.raises(AuthorizationError):
            guard.require_app_scope(app_admin, "app-B")  # Different app → fail

        # ADMIN bypasses scope
        admin = IdentityContext(app_id="app-A", role=Role.ADMIN)
        guard.require_app_scope(admin, "app-B")  # ADMIN → always OK


# ---------------------------------------------------------------------------
# Guard disabled (zero-config mode)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestZeroConfig:
    """With identity.enabled=False, everything should pass."""

    async def test_disabled_guard_allows_all(self) -> None:
        guard = AuthorizationGuard(store=None, enabled=False)
        identity = IdentityContext()  # default: app_id="default", role=ADMIN

        plan = _mock_plan([
            ("os_exec", "run_command"),
            ("filesystem", "delete_file"),
        ])
        result = await guard.check_plan_submission(identity, plan)
        assert result is None  # Disabled → returns None

        # Role checks are no-op
        guard.require_role(
            IdentityContext(role=Role.AGENT),
            Role.ADMIN,
        )

        # Scope checks are no-op
        guard.require_app_scope(
            IdentityContext(app_id="A"),
            "B",
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_plan(
    actions: list[tuple[str, str]],
    plan_id: str = "test-plan",
) -> MagicMock:
    plan = MagicMock()
    plan.plan_id = plan_id
    mock_actions = []
    for module, action in actions:
        a = MagicMock()
        a.module = module
        a.action = action
        a.id = f"{module}.{action}"
        mock_actions.append(a)
    plan.actions = mock_actions
    return plan
