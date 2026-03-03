"""Unit tests — IdentityStore (async SQLite persistence for identity entities)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from llmos_bridge.identity.models import Agent, ApiKey, Application, Role, Session
from llmos_bridge.identity.store import IdentityStore


@pytest.mark.unit
class TestIdentityStoreInit:
    """Tests for store initialization and lifecycle."""

    async def test_init_creates_database_file(self, tmp_path: Path) -> None:
        db_path = tmp_path / "subdir" / "identity.db"
        store = IdentityStore(db_path)
        await store.init()
        try:
            assert db_path.exists()
        finally:
            await store.close()

    async def test_close_is_idempotent(self, tmp_path: Path) -> None:
        store = IdentityStore(tmp_path / "identity.db")
        await store.init()
        await store.close()
        await store.close()  # Should not raise.


@pytest.mark.unit
class TestIdentityStoreApplications:
    """Tests for Application CRUD."""

    @pytest.fixture
    async def store(self, tmp_path: Path):
        s = IdentityStore(tmp_path / "identity.db")
        await s.init()
        yield s
        await s.close()

    async def test_create_and_get_application(self, store: IdentityStore) -> None:
        app = await store.create_application(name="TestApp", app_id="test-1")
        assert app.name == "TestApp"
        assert app.app_id == "test-1"
        assert app.enabled is True

        fetched = await store.get_application("test-1")
        assert fetched is not None
        assert fetched.app_id == "test-1"
        assert fetched.name == "TestApp"

    async def test_get_application_not_found(self, store: IdentityStore) -> None:
        assert await store.get_application("nonexistent") is None

    async def test_get_application_by_name(self, store: IdentityStore) -> None:
        await store.create_application(name="MyApp", app_id="app-1")
        result = await store.get_application_by_name("MyApp")
        assert result is not None
        assert result.app_id == "app-1"

    async def test_get_application_by_name_not_found(self, store: IdentityStore) -> None:
        assert await store.get_application_by_name("nope") is None

    async def test_create_duplicate_name_raises(self, store: IdentityStore) -> None:
        await store.create_application(name="Unique", app_id="a1")
        with pytest.raises(Exception):  # SQLite UNIQUE constraint
            await store.create_application(name="Unique", app_id="a2")

    async def test_list_applications_excludes_disabled(self, store: IdentityStore) -> None:
        await store.create_application(name="Enabled", app_id="e1")
        await store.create_application(name="Disabled", app_id="d1")
        await store.update_application("d1", enabled=False)

        active = await store.list_applications(include_disabled=False)
        assert len(active) == 1
        assert active[0].app_id == "e1"

        all_apps = await store.list_applications(include_disabled=True)
        assert len(all_apps) == 2

    async def test_update_application(self, store: IdentityStore) -> None:
        await store.create_application(name="Original", app_id="u1")
        updated = await store.update_application(
            "u1", description="Updated desc", max_concurrent_plans=20
        )
        assert updated is not None
        assert updated.description == "Updated desc"
        assert updated.max_concurrent_plans == 20

    async def test_update_application_not_found(self, store: IdentityStore) -> None:
        result = await store.update_application("nonexistent", description="x")
        assert result is None

    async def test_update_application_no_changes(self, store: IdentityStore) -> None:
        await store.create_application(name="NoChange", app_id="nc1")
        result = await store.update_application("nc1")  # no kwargs
        assert result is not None
        assert result.name == "NoChange"

    async def test_soft_delete_application(self, store: IdentityStore) -> None:
        await store.create_application(name="ToDelete", app_id="del1")
        deleted = await store.delete_application("del1", hard=False)
        assert deleted is True

        app = await store.get_application("del1")
        assert app is not None
        assert app.enabled is False

    async def test_hard_delete_application(self, store: IdentityStore) -> None:
        await store.create_application(name="HardDel", app_id="hd1")
        await store.create_agent(name="Agent", app_id="hd1")

        deleted = await store.delete_application("hd1", hard=True)
        assert deleted is True
        assert await store.get_application("hd1") is None

    async def test_delete_nonexistent_returns_false(self, store: IdentityStore) -> None:
        assert await store.delete_application("nope") is False

    async def test_ensure_default_app(self, store: IdentityStore) -> None:
        app = await store.ensure_default_app()
        assert app.app_id == "default"
        assert app.name == "default"

        # Calling again should return the same app.
        app2 = await store.ensure_default_app()
        assert app2.app_id == "default"

    async def test_create_application_with_all_fields(self, store: IdentityStore) -> None:
        app = await store.create_application(
            name="Full",
            app_id="full-1",
            description="Full app",
            max_concurrent_plans=5,
            max_actions_per_plan=20,
            allowed_modules=["filesystem"],
            tags={"env": "test"},
        )
        assert app.allowed_modules == ["filesystem"]
        assert app.tags == {"env": "test"}
        assert app.max_concurrent_plans == 5

        # Verify persistence.
        fetched = await store.get_application("full-1")
        assert fetched is not None
        assert fetched.allowed_modules == ["filesystem"]
        assert fetched.tags == {"env": "test"}


@pytest.mark.unit
class TestIdentityStoreAgents:
    """Tests for Agent CRUD."""

    @pytest.fixture
    async def store(self, tmp_path: Path):
        s = IdentityStore(tmp_path / "identity.db")
        await s.init()
        await s.ensure_default_app()
        yield s
        await s.close()

    async def test_create_and_get_agent(self, store: IdentityStore) -> None:
        agent = await store.create_agent(name="Bot", app_id="default", agent_id="ag1")
        assert agent.name == "Bot"
        assert agent.role == Role.AGENT

        fetched = await store.get_agent("ag1")
        assert fetched is not None
        assert fetched.name == "Bot"
        assert fetched.app_id == "default"

    async def test_get_agent_not_found(self, store: IdentityStore) -> None:
        assert await store.get_agent("nonexistent") is None

    async def test_create_agent_custom_role(self, store: IdentityStore) -> None:
        agent = await store.create_agent(
            name="Admin", app_id="default", role=Role.OPERATOR
        )
        assert agent.role == Role.OPERATOR

    async def test_list_agents(self, store: IdentityStore) -> None:
        await store.create_agent(name="Bot1", app_id="default", agent_id="ag1")
        await store.create_agent(name="Bot2", app_id="default", agent_id="ag2")

        agents = await store.list_agents("default")
        assert len(agents) == 2

    async def test_list_agents_excludes_disabled(self, store: IdentityStore) -> None:
        await store.create_agent(name="Active", app_id="default", agent_id="ag1")
        await store.create_agent(name="Disabled", app_id="default", agent_id="ag2")
        await store.delete_agent("ag2")

        active = await store.list_agents("default", include_disabled=False)
        assert len(active) == 1
        assert active[0].agent_id == "ag1"

    async def test_delete_agent(self, store: IdentityStore) -> None:
        await store.create_agent(name="ToDelete", app_id="default", agent_id="ag1")
        deleted = await store.delete_agent("ag1")
        assert deleted is True
        assert await store.get_agent("ag1") is None

    async def test_delete_agent_revokes_keys(self, store: IdentityStore) -> None:
        agent = await store.create_agent(name="Bot", app_id="default", agent_id="ag1")
        key, cleartext = await store.create_api_key("ag1", "default")

        await store.delete_agent("ag1")
        # Key should be revoked.
        result = await store.resolve_api_key(cleartext)
        assert result is None

    async def test_delete_nonexistent_agent(self, store: IdentityStore) -> None:
        assert await store.delete_agent("nonexistent") is False

    async def test_create_agent_with_metadata(self, store: IdentityStore) -> None:
        agent = await store.create_agent(
            name="Rich", app_id="default", metadata={"version": "1.0"}
        )
        fetched = await store.get_agent(agent.agent_id)
        assert fetched is not None
        assert fetched.metadata == {"version": "1.0"}


@pytest.mark.unit
class TestIdentityStoreApiKeys:
    """Tests for API key lifecycle."""

    @pytest.fixture
    async def store(self, tmp_path: Path):
        s = IdentityStore(tmp_path / "identity.db")
        await s.init()
        await s.ensure_default_app()
        await s.create_agent(name="Bot", app_id="default", agent_id="ag1")
        yield s
        await s.close()

    async def test_create_and_resolve_api_key(self, store: IdentityStore) -> None:
        key, cleartext = await store.create_api_key("ag1", "default")
        assert cleartext.startswith("llmos_")
        assert key.prefix == cleartext[:12]

        result = await store.resolve_api_key(cleartext)
        assert result is not None
        app_id, agent_id, role = result
        assert app_id == "default"
        assert agent_id == "ag1"
        assert role == Role.AGENT

    async def test_resolve_invalid_key_returns_none(self, store: IdentityStore) -> None:
        assert await store.resolve_api_key("llmos_bogus") is None

    async def test_revoke_api_key(self, store: IdentityStore) -> None:
        key, cleartext = await store.create_api_key("ag1", "default")
        revoked = await store.revoke_api_key(key.key_id)
        assert revoked is True

        # Revoked key should not resolve.
        assert await store.resolve_api_key(cleartext) is None

    async def test_revoke_nonexistent_key(self, store: IdentityStore) -> None:
        assert await store.revoke_api_key("nonexistent") is False

    async def test_list_api_keys(self, store: IdentityStore) -> None:
        await store.create_api_key("ag1", "default")
        await store.create_api_key("ag1", "default")

        keys = await store.list_api_keys("ag1")
        assert len(keys) == 2

    async def test_list_api_keys_excludes_revoked(self, store: IdentityStore) -> None:
        key1, _ = await store.create_api_key("ag1", "default")
        await store.create_api_key("ag1", "default")
        await store.revoke_api_key(key1.key_id)

        keys = await store.list_api_keys("ag1")
        assert len(keys) == 1

    async def test_expired_key_returns_none(self, store: IdentityStore) -> None:
        _, cleartext = await store.create_api_key(
            "ag1", "default", expires_at=time.time() - 100
        )
        assert await store.resolve_api_key(cleartext) is None

    async def test_key_for_disabled_agent_returns_none(self, store: IdentityStore) -> None:
        _, cleartext = await store.create_api_key("ag1", "default")
        await store.delete_agent("ag1")
        assert await store.resolve_api_key(cleartext) is None

    async def test_key_for_disabled_app_returns_none(self, store: IdentityStore) -> None:
        _, cleartext = await store.create_api_key("ag1", "default")
        await store.update_application("default", enabled=False)
        assert await store.resolve_api_key(cleartext) is None


@pytest.mark.unit
class TestIdentityStoreSessions:
    """Tests for Session CRUD."""

    @pytest.fixture
    async def store(self, tmp_path: Path):
        s = IdentityStore(tmp_path / "identity.db")
        await s.init()
        await s.ensure_default_app()
        yield s
        await s.close()

    async def test_create_and_get_session(self, store: IdentityStore) -> None:
        session = await store.create_session(app_id="default", session_id="s1")
        assert session.session_id == "s1"
        assert session.app_id == "default"

        fetched = await store.get_session("s1")
        assert fetched is not None
        assert fetched.session_id == "s1"

    async def test_get_session_not_found(self, store: IdentityStore) -> None:
        assert await store.get_session("nonexistent") is None

    async def test_list_sessions(self, store: IdentityStore) -> None:
        await store.create_session(app_id="default", session_id="s1")
        await store.create_session(app_id="default", session_id="s2")

        sessions = await store.list_sessions("default")
        assert len(sessions) == 2

    async def test_touch_session(self, store: IdentityStore) -> None:
        session = await store.create_session(app_id="default", session_id="s1")
        original_active = session.last_active

        import asyncio
        await asyncio.sleep(0.05)
        await store.touch_session("s1")

        updated = await store.get_session("s1")
        assert updated is not None
        assert updated.last_active > original_active

    async def test_cleanup_expired_sessions(self, store: IdentityStore) -> None:
        # Create a session with old last_active.
        await store.create_session(app_id="default", session_id="old")
        assert store._db is not None
        await store._db.execute(
            "UPDATE sessions SET last_active = ? WHERE session_id = ?",
            (time.time() - 10000, "old"),
        )
        await store._db.commit()

        await store.create_session(app_id="default", session_id="fresh")

        deleted = await store.cleanup_expired_sessions(max_age_seconds=5000)
        assert deleted == 1

        # Fresh session should still exist.
        assert await store.get_session("fresh") is not None
        assert await store.get_session("old") is None

    async def test_create_session_with_metadata(self, store: IdentityStore) -> None:
        session = await store.create_session(
            app_id="default", metadata={"purpose": "testing"}
        )
        fetched = await store.get_session(session.session_id)
        assert fetched is not None
        assert fetched.metadata == {"purpose": "testing"}


@pytest.mark.unit
class TestIdentityStoreStats:
    """Tests for app_stats()."""

    @pytest.fixture
    async def store(self, tmp_path: Path):
        s = IdentityStore(tmp_path / "identity.db")
        await s.init()
        await s.ensure_default_app()
        yield s
        await s.close()

    async def test_empty_stats(self, store: IdentityStore) -> None:
        stats = await store.app_stats("default")
        assert stats == {"agent_count": 0, "key_count": 0, "session_count": 0}

    async def test_populated_stats(self, store: IdentityStore) -> None:
        await store.create_agent(name="Bot1", app_id="default", agent_id="ag1")
        await store.create_agent(name="Bot2", app_id="default", agent_id="ag2")
        await store.create_api_key("ag1", "default")
        await store.create_session(app_id="default")
        await store.create_session(app_id="default")

        stats = await store.app_stats("default")
        assert stats["agent_count"] == 2
        assert stats["key_count"] == 1
        assert stats["session_count"] == 2
