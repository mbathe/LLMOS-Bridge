"""Unit tests — Identity models (Role, Application, Agent, etc.)."""

from __future__ import annotations

import pytest

from llmos_bridge.identity.models import (
    Agent,
    ApiKey,
    Application,
    ClusterInfo,
    IdentityContext,
    Role,
    Session,
)


@pytest.mark.unit
class TestRole:
    """Tests for the Role enum."""

    def test_role_values(self) -> None:
        assert Role.ADMIN == "admin"
        assert Role.APP_ADMIN == "app_admin"
        assert Role.OPERATOR == "operator"
        assert Role.VIEWER == "viewer"
        assert Role.AGENT == "agent"

    def test_role_from_string(self) -> None:
        assert Role("admin") is Role.ADMIN
        assert Role("agent") is Role.AGENT

    def test_role_invalid_raises(self) -> None:
        with pytest.raises(ValueError):
            Role("superuser")


@pytest.mark.unit
class TestApplication:
    """Tests for the Application model."""

    def test_minimal_creation(self) -> None:
        app = Application(app_id="app1", name="Test App")
        assert app.app_id == "app1"
        assert app.name == "Test App"
        assert app.enabled is True
        assert app.max_concurrent_plans == 10
        assert app.max_actions_per_plan == 50
        assert app.allowed_modules == []
        assert app.tags == {}

    def test_full_creation(self) -> None:
        app = Application(
            app_id="app2",
            name="Production",
            description="Production application",
            enabled=False,
            max_concurrent_plans=5,
            max_actions_per_plan=20,
            allowed_modules=["filesystem", "os_exec"],
            tags={"env": "prod"},
        )
        assert app.description == "Production application"
        assert app.enabled is False
        assert app.allowed_modules == ["filesystem", "os_exec"]
        assert app.tags == {"env": "prod"}

    def test_created_at_auto_set(self) -> None:
        app = Application(app_id="a", name="A")
        assert app.created_at > 0
        assert app.updated_at > 0

    def test_serialization_roundtrip(self) -> None:
        app = Application(app_id="x", name="X", tags={"k": "v"})
        data = app.model_dump()
        restored = Application(**data)
        assert restored == app


@pytest.mark.unit
class TestAgent:
    """Tests for the Agent model."""

    def test_defaults(self) -> None:
        agent = Agent(agent_id="ag1", name="Bot", app_id="app1")
        assert agent.role == Role.AGENT
        assert agent.enabled is True
        assert agent.metadata == {}

    def test_custom_role(self) -> None:
        agent = Agent(agent_id="ag2", name="Admin Bot", app_id="app1", role=Role.OPERATOR)
        assert agent.role == Role.OPERATOR


@pytest.mark.unit
class TestApiKey:
    """Tests for the ApiKey model."""

    def test_defaults(self) -> None:
        key = ApiKey(key_id="k1", agent_id="ag1", app_id="app1", key_hash="abc123")
        assert key.prefix == ""
        assert key.revoked is False
        assert key.expires_at is None

    def test_revoked_key(self) -> None:
        key = ApiKey(key_id="k2", agent_id="ag1", app_id="app1", key_hash="xyz", revoked=True)
        assert key.revoked is True


@pytest.mark.unit
class TestSession:
    """Tests for the Session model."""

    def test_defaults(self) -> None:
        s = Session(session_id="s1", app_id="app1")
        assert s.agent_id is None
        assert s.metadata == {}
        assert s.created_at > 0

    def test_with_agent(self) -> None:
        s = Session(session_id="s2", app_id="app1", agent_id="ag1")
        assert s.agent_id == "ag1"


@pytest.mark.unit
class TestClusterInfo:
    """Tests for the ClusterInfo model."""

    def test_auto_id(self) -> None:
        c = ClusterInfo(name="test-cluster")
        assert c.cluster_id  # auto-generated UUID
        assert c.name == "test-cluster"

    def test_explicit_id(self) -> None:
        c = ClusterInfo(cluster_id="my-id", name="cluster")
        assert c.cluster_id == "my-id"


@pytest.mark.unit
class TestIdentityContext:
    """Tests for the IdentityContext model."""

    def test_defaults(self) -> None:
        ctx = IdentityContext()
        assert ctx.app_id == "default"
        assert ctx.agent_id is None
        assert ctx.session_id is None
        assert ctx.role == Role.ADMIN

    def test_custom_values(self) -> None:
        ctx = IdentityContext(
            app_id="myapp", agent_id="bot1", session_id="s1", role=Role.VIEWER
        )
        assert ctx.app_id == "myapp"
        assert ctx.role == Role.VIEWER
