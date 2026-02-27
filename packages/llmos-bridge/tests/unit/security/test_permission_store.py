"""Unit tests -- PermissionStore (async SQLite grant persistence)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from llmos_bridge.security.models import PermissionGrant, PermissionScope
from llmos_bridge.security.permission_store import PermissionStore


def _grant(
    permission: str = "filesystem.write",
    module_id: str = "filesystem",
    scope: PermissionScope = PermissionScope.SESSION,
    granted_by: str = "user",
    reason: str = "",
    expires_at: float | None = None,
) -> PermissionGrant:
    return PermissionGrant(
        permission=permission,
        module_id=module_id,
        scope=scope,
        granted_at=time.time(),
        granted_by=granted_by,
        reason=reason,
        expires_at=expires_at,
    )


@pytest.mark.unit
class TestPermissionStore:
    """Tests for PermissionStore async SQLite store."""

    @pytest.fixture
    async def store(self, tmp_path: Path):
        db_path = tmp_path / "permissions.db"
        s = PermissionStore(db_path)
        await s.init()
        yield s
        await s.close()

    # 1. init creates the database file
    async def test_init_creates_database_file(self, tmp_path: Path) -> None:
        db_path = tmp_path / "subdir" / "permissions.db"
        store = PermissionStore(db_path)
        await store.init()
        try:
            assert db_path.exists()
        finally:
            await store.close()

    # 2. grant + is_granted returns True
    async def test_grant_then_is_granted(self, store: PermissionStore) -> None:
        await store.grant(_grant("filesystem.write", "filesystem"))
        assert await store.is_granted("filesystem.write", "filesystem") is True

    # 3. is_granted returns False for missing grant
    async def test_is_granted_returns_false_for_missing(self, store: PermissionStore) -> None:
        assert await store.is_granted("no.such.perm", "no_module") is False

    # 4. grant overwrites existing grant (same PK)
    async def test_grant_overwrites_existing(self, store: PermissionStore) -> None:
        g1 = _grant("filesystem.write", "filesystem", reason="first")
        g2 = _grant("filesystem.write", "filesystem", reason="second")
        await store.grant(g1)
        await store.grant(g2)
        result = await store.get_grant("filesystem.write", "filesystem")
        assert result is not None
        assert result.reason == "second"

    # 5. revoke removes grant and returns True
    async def test_revoke_removes_grant(self, store: PermissionStore) -> None:
        await store.grant(_grant("filesystem.write", "filesystem"))
        removed = await store.revoke("filesystem.write", "filesystem")
        assert removed is True
        assert await store.is_granted("filesystem.write", "filesystem") is False

    # 6. revoke returns False for missing grant
    async def test_revoke_returns_false_for_missing(self, store: PermissionStore) -> None:
        removed = await store.revoke("no.perm", "no_module")
        assert removed is False

    # 7. revoke_all_for_module removes all grants for that module
    async def test_revoke_all_for_module(self, store: PermissionStore) -> None:
        await store.grant(_grant("filesystem.read", "filesystem"))
        await store.grant(_grant("filesystem.write", "filesystem"))
        await store.grant(_grant("os.process.execute", "os_exec"))

        count = await store.revoke_all_for_module("filesystem")
        assert count == 2
        assert await store.is_granted("filesystem.read", "filesystem") is False
        assert await store.is_granted("filesystem.write", "filesystem") is False
        # Other module untouched
        assert await store.is_granted("os.process.execute", "os_exec") is True

    # 8. get_grant returns the grant object
    async def test_get_grant_returns_object(self, store: PermissionStore) -> None:
        g = _grant("filesystem.write", "filesystem", reason="test reason", granted_by="admin")
        await store.grant(g)
        result = await store.get_grant("filesystem.write", "filesystem")
        assert result is not None
        assert result.permission == "filesystem.write"
        assert result.module_id == "filesystem"
        assert result.reason == "test reason"
        assert result.granted_by == "admin"
        assert result.scope == PermissionScope.SESSION

    # 9. get_grant returns None for missing
    async def test_get_grant_returns_none_for_missing(self, store: PermissionStore) -> None:
        result = await store.get_grant("no.perm", "no_module")
        assert result is None

    # 10. get_all returns all non-expired grants
    async def test_get_all_returns_all_grants(self, store: PermissionStore) -> None:
        await store.grant(_grant("filesystem.read", "filesystem"))
        await store.grant(_grant("filesystem.write", "filesystem"))
        await store.grant(_grant("os.process.execute", "os_exec"))

        grants = await store.get_all()
        assert len(grants) == 3
        perms = {(g.permission, g.module_id) for g in grants}
        assert ("filesystem.read", "filesystem") in perms
        assert ("filesystem.write", "filesystem") in perms
        assert ("os.process.execute", "os_exec") in perms

    # 11. get_for_module returns grants for specific module only
    async def test_get_for_module(self, store: PermissionStore) -> None:
        await store.grant(_grant("filesystem.read", "filesystem"))
        await store.grant(_grant("filesystem.write", "filesystem"))
        await store.grant(_grant("os.process.execute", "os_exec"))

        fs_grants = await store.get_for_module("filesystem")
        assert len(fs_grants) == 2
        assert all(g.module_id == "filesystem" for g in fs_grants)

        os_grants = await store.get_for_module("os_exec")
        assert len(os_grants) == 1
        assert os_grants[0].permission == "os.process.execute"

    # 12. clear_session removes SESSION grants but keeps PERMANENT
    async def test_clear_session_keeps_permanent(self, store: PermissionStore) -> None:
        await store.grant(_grant("filesystem.read", "filesystem", scope=PermissionScope.SESSION))
        await store.grant(_grant("filesystem.write", "filesystem", scope=PermissionScope.PERMANENT))
        await store.grant(_grant("os.process.execute", "os_exec", scope=PermissionScope.SESSION))

        cleared = await store.clear_session()
        assert cleared == 2

        assert await store.is_granted("filesystem.read", "filesystem") is False
        assert await store.is_granted("filesystem.write", "filesystem") is True
        assert await store.is_granted("os.process.execute", "os_exec") is False

    # 13. is_granted returns False for expired grant
    async def test_is_granted_returns_false_for_expired(self, store: PermissionStore) -> None:
        g = _grant("filesystem.write", "filesystem", expires_at=time.time() - 10)
        await store.grant(g)
        assert await store.is_granted("filesystem.write", "filesystem") is False

    # 14. get_grant returns None for expired grant and removes it
    async def test_get_grant_returns_none_for_expired_and_removes(
        self, store: PermissionStore
    ) -> None:
        g = _grant("filesystem.write", "filesystem", expires_at=time.time() - 10)
        await store.grant(g)

        result = await store.get_grant("filesystem.write", "filesystem")
        assert result is None

        # Verify the row was lazily cleaned up
        assert await store.is_granted("filesystem.write", "filesystem") is False

    # 15. get_all filters out expired grants
    async def test_get_all_filters_expired(self, store: PermissionStore) -> None:
        await store.grant(_grant("filesystem.read", "filesystem"))
        await store.grant(
            _grant("filesystem.write", "filesystem", expires_at=time.time() - 10)
        )
        await store.grant(_grant("os.process.execute", "os_exec"))

        grants = await store.get_all()
        assert len(grants) == 2
        perms = {g.permission for g in grants}
        assert "filesystem.read" in perms
        assert "os.process.execute" in perms
        assert "filesystem.write" not in perms

    # 16. init clears session grants from previous run
    async def test_init_clears_session_from_previous_run(self, tmp_path: Path) -> None:
        db_path = tmp_path / "permissions.db"

        # First session: insert grants
        store1 = PermissionStore(db_path)
        await store1.init()
        await store1.grant(
            _grant("filesystem.read", "filesystem", scope=PermissionScope.SESSION)
        )
        await store1.grant(
            _grant("filesystem.write", "filesystem", scope=PermissionScope.PERMANENT)
        )
        await store1.close()

        # Second session: init should clear SESSION grants
        store2 = PermissionStore(db_path)
        await store2.init()
        try:
            assert await store2.is_granted("filesystem.read", "filesystem") is False
            assert await store2.is_granted("filesystem.write", "filesystem") is True
        finally:
            await store2.close()

    # 17. multiple modules can have same permission
    async def test_multiple_modules_same_permission(self, store: PermissionStore) -> None:
        await store.grant(_grant("filesystem.write", "filesystem"))
        await store.grant(_grant("filesystem.write", "custom_module"))

        assert await store.is_granted("filesystem.write", "filesystem") is True
        assert await store.is_granted("filesystem.write", "custom_module") is True

        # Revoking one does not affect the other
        await store.revoke("filesystem.write", "filesystem")
        assert await store.is_granted("filesystem.write", "filesystem") is False
        assert await store.is_granted("filesystem.write", "custom_module") is True

    # 18. close and re-init works
    async def test_close_and_reinit(self, tmp_path: Path) -> None:
        db_path = tmp_path / "permissions.db"
        store = PermissionStore(db_path)
        await store.init()
        await store.grant(
            _grant("filesystem.write", "filesystem", scope=PermissionScope.PERMANENT)
        )
        await store.close()

        # Re-init the same store object
        await store.init()
        try:
            assert await store.is_granted("filesystem.write", "filesystem") is True
        finally:
            await store.close()
