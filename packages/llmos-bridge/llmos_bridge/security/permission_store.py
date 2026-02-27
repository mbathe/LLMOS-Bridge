"""Security layer — SQLite-backed permission grant persistence.

Stores permission grants with two scopes:
  - SESSION   — cleared on daemon restart (via ``clear_session()`` in ``init()``)
  - PERMANENT — persists across restarts until explicitly revoked

The table uses a composite PK of ``(permission, module_id)`` so each
module can hold at most one grant per permission string.

Schema::

    CREATE TABLE permission_grants (
        permission   TEXT NOT NULL,
        module_id    TEXT NOT NULL,
        scope        TEXT NOT NULL DEFAULT 'session',
        granted_at   REAL NOT NULL,
        granted_by   TEXT NOT NULL DEFAULT 'user',
        reason       TEXT NOT NULL DEFAULT '',
        expires_at   REAL,
        PRIMARY KEY (permission, module_id)
    );

Usage::

    store = PermissionStore(Path("~/.llmos/permissions.db"))
    await store.init()                      # creates table, clears session grants
    await store.grant(grant)                # insert or replace
    ok = await store.is_granted("filesystem.write", "filesystem")
    await store.revoke("filesystem.write", "filesystem")
    await store.close()
"""

from __future__ import annotations

import time
from pathlib import Path

import aiosqlite

from llmos_bridge.logging import get_logger
from llmos_bridge.security.models import PermissionGrant, PermissionScope

log = get_logger(__name__)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS permission_grants (
    permission   TEXT NOT NULL,
    module_id    TEXT NOT NULL,
    scope        TEXT NOT NULL DEFAULT 'session',
    granted_at   REAL NOT NULL,
    granted_by   TEXT NOT NULL DEFAULT 'user',
    reason       TEXT NOT NULL DEFAULT '',
    expires_at   REAL,
    PRIMARY KEY (permission, module_id)
);

CREATE INDEX IF NOT EXISTS idx_grants_module ON permission_grants (module_id);
"""


class PermissionStore:
    """Async SQLite store for OS-level permission grants."""

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = Path(db_path).expanduser()
        self._conn: aiosqlite.Connection | None = None

    async def init(self) -> None:
        """Create tables and clear session-scoped grants from previous runs."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(str(self._db_path))
        await self._conn.executescript(_SCHEMA_SQL)
        await self._conn.commit()
        await self.clear_session()
        log.debug("permission_store_init", path=str(self._db_path))

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    async def grant(self, grant: PermissionGrant) -> None:
        """Store a permission grant (insert or replace)."""
        assert self._conn is not None
        await self._conn.execute(
            """INSERT OR REPLACE INTO permission_grants
               (permission, module_id, scope, granted_at, granted_by, reason, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                grant.permission,
                grant.module_id,
                grant.scope.value,
                grant.granted_at,
                grant.granted_by,
                grant.reason,
                grant.expires_at,
            ),
        )
        await self._conn.commit()

    async def revoke(self, permission: str, module_id: str) -> bool:
        """Remove a specific grant. Returns True if a row was deleted."""
        assert self._conn is not None
        cursor = await self._conn.execute(
            "DELETE FROM permission_grants WHERE permission=? AND module_id=?",
            (permission, module_id),
        )
        await self._conn.commit()
        return cursor.rowcount > 0

    async def revoke_all_for_module(self, module_id: str) -> int:
        """Remove all grants for a module. Returns number of rows deleted."""
        assert self._conn is not None
        cursor = await self._conn.execute(
            "DELETE FROM permission_grants WHERE module_id=?",
            (module_id,),
        )
        await self._conn.commit()
        return cursor.rowcount

    async def clear_session(self) -> int:
        """Remove all session-scoped grants (called on daemon startup).

        Returns the number of grants cleared.
        """
        assert self._conn is not None
        cursor = await self._conn.execute(
            "DELETE FROM permission_grants WHERE scope=?",
            (PermissionScope.SESSION.value,),
        )
        await self._conn.commit()
        cleared = cursor.rowcount
        if cleared:
            log.info("permission_store_session_cleared", count=cleared)
        return cleared

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    async def is_granted(self, permission: str, module_id: str) -> bool:
        """Check if a specific permission is currently granted (and not expired)."""
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT expires_at FROM permission_grants WHERE permission=? AND module_id=?",
            (permission, module_id),
        ) as cursor:
            row = await cursor.fetchone()

        if row is None:
            return False
        # Check expiration
        expires_at = row[0]
        if expires_at is not None and time.time() > expires_at:
            # Expired — clean up lazily
            await self.revoke(permission, module_id)
            return False
        return True

    async def get_grant(
        self, permission: str, module_id: str
    ) -> PermissionGrant | None:
        """Retrieve a specific grant record, or None if not found / expired."""
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT permission, module_id, scope, granted_at, granted_by, reason, expires_at "
            "FROM permission_grants WHERE permission=? AND module_id=?",
            (permission, module_id),
        ) as cursor:
            row = await cursor.fetchone()

        if row is None:
            return None

        grant = self._row_to_grant(row)
        if grant.is_expired():
            await self.revoke(permission, module_id)
            return None
        return grant

    async def get_all(self) -> list[PermissionGrant]:
        """Retrieve all non-expired grants."""
        assert self._conn is not None
        results: list[PermissionGrant] = []
        now = time.time()
        expired: list[tuple[str, str]] = []

        async with self._conn.execute(
            "SELECT permission, module_id, scope, granted_at, granted_by, reason, expires_at "
            "FROM permission_grants ORDER BY granted_at DESC",
        ) as cursor:
            async for row in cursor:
                grant = self._row_to_grant(row)
                if grant.is_expired():
                    expired.append((grant.permission, grant.module_id))
                else:
                    results.append(grant)

        # Lazy cleanup of expired grants
        for perm, mod in expired:
            await self.revoke(perm, mod)

        return results

    async def get_for_module(self, module_id: str) -> list[PermissionGrant]:
        """Retrieve all non-expired grants for a specific module."""
        assert self._conn is not None
        results: list[PermissionGrant] = []
        expired: list[tuple[str, str]] = []

        async with self._conn.execute(
            "SELECT permission, module_id, scope, granted_at, granted_by, reason, expires_at "
            "FROM permission_grants WHERE module_id=? ORDER BY granted_at DESC",
            (module_id,),
        ) as cursor:
            async for row in cursor:
                grant = self._row_to_grant(row)
                if grant.is_expired():
                    expired.append((grant.permission, grant.module_id))
                else:
                    results.append(grant)

        for perm, mod in expired:
            await self.revoke(perm, mod)

        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_grant(row: tuple) -> PermissionGrant:  # type: ignore[type-arg]
        return PermissionGrant(
            permission=row[0],
            module_id=row[1],
            scope=PermissionScope(row[2]),
            granted_at=row[3],
            granted_by=row[4],
            reason=row[5],
            expires_at=row[6],
        )
