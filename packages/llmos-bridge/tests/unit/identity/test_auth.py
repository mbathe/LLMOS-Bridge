"""Unit tests — IdentityResolver (API key auth and RBAC resolution)."""

from __future__ import annotations

from pathlib import Path

import pytest

from llmos_bridge.exceptions import AuthenticationError
from llmos_bridge.identity.auth import IdentityResolver
from llmos_bridge.identity.models import IdentityContext, Role
from llmos_bridge.identity.store import IdentityStore


@pytest.mark.unit
class TestIdentityResolverDisabled:
    """Tests when identity system is disabled (default mode)."""

    @pytest.fixture
    def resolver(self):
        return IdentityResolver(store=None, enabled=False)

    async def test_resolve_returns_default_context(self, resolver: IdentityResolver) -> None:
        ctx = await resolver.resolve()
        assert ctx.app_id == "default"
        assert ctx.role == Role.ADMIN
        assert ctx.agent_id is None

    async def test_resolve_ignores_headers(self, resolver: IdentityResolver) -> None:
        ctx = await resolver.resolve(
            authorization="Bearer llmos_fake",
            x_app="myapp",
            x_agent="myagent",
        )
        assert ctx.app_id == "default"
        assert ctx.role == Role.ADMIN

    def test_enabled_property(self, resolver: IdentityResolver) -> None:
        assert resolver.enabled is False

    def test_requires_api_keys_property(self, resolver: IdentityResolver) -> None:
        assert resolver.requires_api_keys is False


@pytest.mark.unit
class TestIdentityResolverHeadersOnly:
    """Tests with identity enabled but API keys not required."""

    @pytest.fixture
    def resolver(self):
        return IdentityResolver(store=None, enabled=True, require_api_keys=False)

    async def test_resolve_with_no_headers(self, resolver: IdentityResolver) -> None:
        ctx = await resolver.resolve()
        assert ctx.app_id == "default"
        assert ctx.role == Role.ADMIN

    async def test_resolve_with_app_header(self, resolver: IdentityResolver) -> None:
        ctx = await resolver.resolve(x_app="my-app")
        assert ctx.app_id == "my-app"

    async def test_resolve_with_agent_header(self, resolver: IdentityResolver) -> None:
        ctx = await resolver.resolve(x_agent="bot-1")
        assert ctx.agent_id == "bot-1"

    async def test_resolve_with_session_header(self, resolver: IdentityResolver) -> None:
        ctx = await resolver.resolve(x_session="sess-1")
        assert ctx.session_id == "sess-1"

    async def test_resolve_with_all_headers(self, resolver: IdentityResolver) -> None:
        ctx = await resolver.resolve(
            x_app="myapp", x_agent="myagent", x_session="mysess"
        )
        assert ctx.app_id == "myapp"
        assert ctx.agent_id == "myagent"
        assert ctx.session_id == "mysess"

    def test_enabled_property(self, resolver: IdentityResolver) -> None:
        assert resolver.enabled is True


@pytest.mark.unit
class TestIdentityResolverApiKeys:
    """Tests with API key validation enabled."""

    @pytest.fixture
    async def store(self, tmp_path: Path):
        s = IdentityStore(tmp_path / "identity.db")
        await s.init()
        await s.ensure_default_app()
        await s.create_agent(name="Bot", app_id="default", agent_id="ag1")
        yield s
        await s.close()

    @pytest.fixture
    def resolver(self, store: IdentityStore):
        return IdentityResolver(store=store, enabled=True, require_api_keys=True)

    async def test_resolve_with_valid_key(
        self, store: IdentityStore, resolver: IdentityResolver
    ) -> None:
        _, cleartext = await store.create_api_key("ag1", "default")
        ctx = await resolver.resolve(authorization=f"Bearer {cleartext}")
        assert ctx.app_id == "default"
        assert ctx.agent_id == "ag1"
        assert ctx.role == Role.AGENT

    async def test_resolve_missing_auth_raises(self, resolver: IdentityResolver) -> None:
        with pytest.raises(AuthenticationError):
            await resolver.resolve()

    async def test_resolve_empty_auth_raises(self, resolver: IdentityResolver) -> None:
        with pytest.raises(AuthenticationError):
            await resolver.resolve(authorization="")

    async def test_resolve_invalid_format_raises(self, resolver: IdentityResolver) -> None:
        with pytest.raises(AuthenticationError):
            await resolver.resolve(authorization="Token abc123")

    async def test_resolve_invalid_key_raises(self, resolver: IdentityResolver) -> None:
        with pytest.raises(AuthenticationError):
            await resolver.resolve(authorization="Bearer llmos_invalidkey")

    async def test_resolve_revoked_key_raises(
        self, store: IdentityStore, resolver: IdentityResolver
    ) -> None:
        key, cleartext = await store.create_api_key("ag1", "default")
        await store.revoke_api_key(key.key_id)
        with pytest.raises(AuthenticationError):
            await resolver.resolve(authorization=f"Bearer {cleartext}")

    async def test_resolve_passes_session_header(
        self, store: IdentityStore, resolver: IdentityResolver
    ) -> None:
        _, cleartext = await store.create_api_key("ag1", "default")
        ctx = await resolver.resolve(
            authorization=f"Bearer {cleartext}", x_session="s1"
        )
        assert ctx.session_id == "s1"


@pytest.mark.unit
class TestRoleCheck:
    """Tests for the check_role RBAC method."""

    @pytest.fixture
    def resolver(self):
        return IdentityResolver(store=None, enabled=False)

    def test_admin_has_all_roles(self, resolver: IdentityResolver) -> None:
        ctx = IdentityContext(role=Role.ADMIN)
        assert resolver.check_role(ctx, Role.ADMIN) is True
        assert resolver.check_role(ctx, Role.APP_ADMIN) is True
        assert resolver.check_role(ctx, Role.OPERATOR) is True
        assert resolver.check_role(ctx, Role.VIEWER) is True
        assert resolver.check_role(ctx, Role.AGENT) is True

    def test_viewer_cannot_operate(self, resolver: IdentityResolver) -> None:
        ctx = IdentityContext(role=Role.VIEWER)
        assert resolver.check_role(ctx, Role.VIEWER) is True
        assert resolver.check_role(ctx, Role.AGENT) is True
        assert resolver.check_role(ctx, Role.OPERATOR) is False
        assert resolver.check_role(ctx, Role.ADMIN) is False

    def test_agent_is_least_privileged(self, resolver: IdentityResolver) -> None:
        ctx = IdentityContext(role=Role.AGENT)
        assert resolver.check_role(ctx, Role.AGENT) is True
        assert resolver.check_role(ctx, Role.VIEWER) is False

    def test_operator_level(self, resolver: IdentityResolver) -> None:
        ctx = IdentityContext(role=Role.OPERATOR)
        assert resolver.check_role(ctx, Role.OPERATOR) is True
        assert resolver.check_role(ctx, Role.VIEWER) is True
        assert resolver.check_role(ctx, Role.APP_ADMIN) is False
