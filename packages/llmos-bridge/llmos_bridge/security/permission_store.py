"""Security layer — SQLite-backed permission grant persistence.

Stores permission grants with two scopes:
  - SESSION   — cleared on daemon restart (via ``clear_session()`` in ``init()``)
  - PERMANENT — persists across restarts until explicitly revoked

Permissions are scoped to ``(permission, module_id, app_id)`` so each
application can hold independent grants for the same permission/module pair.
The ``app_id`` defaults to ``"default"`` which represents the global (daemon-wide)
grants used when the identity system is disabled.

Schema::

    CREATE TABLE permission_grants (
        permission   TEXT NOT NULL,
        module_id    TEXT NOT NULL,
        app_id       TEXT NOT NULL DEFAULT 'default',
        scope        TEXT NOT NULL DEFAULT 'session',
        granted_at   REAL NOT NULL,
        granted_by   TEXT NOT NULL DEFAULT 'user',
        reason       TEXT NOT NULL DEFAULT '',
        expires_at   REAL,
        PRIMARY KEY (permission, module_id, app_id)
    );

Usage::

    store = PermissionStore(Path("~/.llmos/permissions.db"))
    await store.init()
    # App-scoped grant (identity system enabled)
    await store.grant(grant, app_id="app-xyz")
    ok = await store.is_granted("filesystem.write", "filesystem", app_id="app-xyz")
    # Global grant (default app / decorators)
    await store.grant(grant)
    ok = await store.is_granted("filesystem.write", "filesystem")
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
    app_id       TEXT NOT NULL DEFAULT 'default',
    scope        TEXT NOT NULL DEFAULT 'session',
    granted_at   REAL NOT NULL,
    granted_by   TEXT NOT NULL DEFAULT 'user',
    reason       TEXT NOT NULL DEFAULT '',
    expires_at   REAL,
    PRIMARY KEY (permission, module_id, app_id)
);

CREATE INDEX IF NOT EXISTS idx_grants_app_id ON permission_grants (app_id);
CREATE INDEX IF NOT EXISTS idx_grants_module ON permission_grants (module_id);
"""


class PermissionStore:
    """Async SQLite store for OS-level permission grants.

    All methods accept an optional ``app_id`` parameter (default ``"default"``).
    When the identity system is disabled, all grants use ``app_id="default"``.
    When the identity system is enabled, each application has its own isolated
    grant namespace.
    """

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = Path(db_path).expanduser()
        self._conn: aiosqlite.Connection | None = None

    async def init(self) -> None:
        """Create tables and clear session-scoped grants from previous runs."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(str(self._db_path))
        await self._migrate()
        await self._conn.executescript(_SCHEMA_SQL)
        await self._conn.commit()
        await self.clear_session()
        log.debug("permission_store_init", path=str(self._db_path))

    async def _migrate(self) -> None:
        """Migrate legacy schema (PK was (permission, module_id)) to new schema.

        Detects the old schema by checking whether ``app_id`` is part of the
        primary key.  If not, renames the old table, creates the new one, and
        copies existing rows with ``app_id='default'``.
        """
        assert self._conn is not None

        # Does the table exist at all?
        async with self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='permission_grants'"
        ) as cur:
            if await cur.fetchone() is None:
                return  # Fresh database — schema will be created below.

        # Check if app_id is a primary key column (pk > 0).
        async with self._conn.execute("PRAGMA table_info(permission_grants)") as cur:
            columns = {row[1]: {"pk": row[5]} for row in await cur.fetchall()}

        app_id_pk = columns.get("app_id", {}).get("pk", 0)
        if app_id_pk > 0:
            return  # Already using the new schema.

        # Legacy schema detected — migrate.
        log.info("permission_store_migrating", reason="old PK=(permission,module_id)")
        await self._conn.executescript("""
            ALTER TABLE permission_grants RENAME TO permission_grants_legacy;
            CREATE TABLE permission_grants (
                permission   TEXT NOT NULL,
                module_id    TEXT NOT NULL,
                app_id       TEXT NOT NULL DEFAULT 'default',
                scope        TEXT NOT NULL DEFAULT 'session',
                granted_at   REAL NOT NULL,
                granted_by   TEXT NOT NULL DEFAULT 'user',
                reason       TEXT NOT NULL DEFAULT '',
                expires_at   REAL,
                PRIMARY KEY (permission, module_id, app_id)
            );
            INSERT INTO permission_grants
                (permission, module_id, app_id, scope, granted_at, granted_by, reason, expires_at)
            SELECT permission, module_id, COALESCE(app_id, 'default'), scope,
                   granted_at, granted_by, reason, expires_at
            FROM permission_grants_legacy;
            DROP TABLE permission_grants_legacy;
        """)
        await self._conn.commit()
        log.info("permission_store_migrated", to_schema="(permission,module_id,app_id)")

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    async def grant(self, grant: PermissionGrant, app_id: str = "default") -> None:
        """Store a permission grant (insert or replace) for a specific app."""
        assert self._conn is not None
        effective_app = grant.app_id if grant.app_id != "default" else app_id
        await self._conn.execute(
            """INSERT OR REPLACE INTO permission_grants
               (permission, module_id, app_id, scope, granted_at, granted_by, reason, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                grant.permission,
                grant.module_id,
                effective_app,
                grant.scope.value,
                grant.granted_at,
                grant.granted_by,
                grant.reason,
                grant.expires_at,
            ),
        )
        await self._conn.commit()

    async def revoke(self, permission: str, module_id: str, app_id: str = "default") -> bool:
        """Remove a specific grant. Returns True if a row was deleted."""
        assert self._conn is not None
        cursor = await self._conn.execute(
            "DELETE FROM permission_grants WHERE permission=? AND module_id=? AND app_id=?",
            (permission, module_id, app_id),
        )
        await self._conn.commit()
        return cursor.rowcount > 0

    async def revoke_all_for_module(self, module_id: str, app_id: str = "default") -> int:
        """Remove all grants for a module within an app. Returns number of rows deleted."""
        assert self._conn is not None
        cursor = await self._conn.execute(
            "DELETE FROM permission_grants WHERE module_id=? AND app_id=?",
            (module_id, app_id),
        )
        await self._conn.commit()
        return cursor.rowcount

    async def clear_session(self) -> int:
        """Remove all session-scoped grants across all apps (called on daemon startup).

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

    async def is_granted(self, permission: str, module_id: str, app_id: str = "default") -> bool:
        """Check if a specific permission is currently granted for an app (not expired)."""
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT expires_at FROM permission_grants WHERE permission=? AND module_id=? AND app_id=?",
            (permission, module_id, app_id),
        ) as cursor:
            row = await cursor.fetchone()

        if row is None:
            return False
        expires_at = row[0]
        if expires_at is not None and time.time() > expires_at:
            await self.revoke(permission, module_id, app_id)
            return False
        return True

    async def get_grant(
        self, permission: str, module_id: str, app_id: str = "default"
    ) -> PermissionGrant | None:
        """Retrieve a specific grant record, or None if not found / expired."""
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT permission, module_id, app_id, scope, granted_at, granted_by, reason, expires_at "
            "FROM permission_grants WHERE permission=? AND module_id=? AND app_id=?",
            (permission, module_id, app_id),
        ) as cursor:
            row = await cursor.fetchone()

        if row is None:
            return None

        grant = self._row_to_grant(row)
        if grant.is_expired():
            await self.revoke(permission, module_id, app_id)
            return None
        return grant

    async def get_all(self) -> list[PermissionGrant]:
        """Retrieve all non-expired grants across all applications."""
        assert self._conn is not None
        return await self._fetch_grants(
            "SELECT permission, module_id, app_id, scope, granted_at, granted_by, reason, expires_at "
            "FROM permission_grants ORDER BY app_id, granted_at DESC"
        )

    async def list_all(self) -> list[PermissionGrant]:
        """Alias for get_all() — retrieve all non-expired grants."""
        return await self.get_all()

    async def get_for_app(self, app_id: str) -> list[PermissionGrant]:
        """Retrieve all non-expired grants for a specific application."""
        assert self._conn is not None
        return await self._fetch_grants(
            "SELECT permission, module_id, app_id, scope, granted_at, granted_by, reason, expires_at "
            "FROM permission_grants WHERE app_id=? ORDER BY granted_at DESC",
            (app_id,),
        )

    async def list_grants(self, module_id: str | None = None, app_id: str = "default") -> list[PermissionGrant]:
        """Retrieve grants for an app, optionally filtered by module."""
        assert self._conn is not None
        if module_id:
            return await self._fetch_grants(
                "SELECT permission, module_id, app_id, scope, granted_at, granted_by, reason, expires_at "
                "FROM permission_grants WHERE module_id=? AND app_id=? ORDER BY granted_at DESC",
                (module_id, app_id),
            )
        return await self.get_for_app(app_id)

    async def get_for_module(self, module_id: str, app_id: str = "default") -> list[PermissionGrant]:
        """Retrieve all non-expired grants for a module within an app (backward compat alias)."""
        return await self._fetch_grants(
            "SELECT permission, module_id, app_id, scope, granted_at, granted_by, reason, expires_at "
            "FROM permission_grants WHERE module_id=? AND app_id=? ORDER BY granted_at DESC",
            (module_id, app_id),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _fetch_grants(
        self, sql: str, params: tuple = ()
    ) -> list[PermissionGrant]:
        """Execute a SELECT and return non-expired PermissionGrant objects."""
        assert self._conn is not None
        results: list[PermissionGrant] = []
        to_revoke: list[tuple[str, str, str]] = []

        async with self._conn.execute(sql, params) as cursor:
            async for row in cursor:
                grant = self._row_to_grant(row)
                if grant.is_expired():
                    to_revoke.append((grant.permission, grant.module_id, grant.app_id))
                else:
                    results.append(grant)

        for perm, mod, aid in to_revoke:
            await self.revoke(perm, mod, aid)

        return results

    @staticmethod
    def _row_to_grant(row: tuple) -> PermissionGrant:  # type: ignore[type-arg]
        return PermissionGrant(
            permission=row[0],
            module_id=row[1],
            app_id=row[2],
            scope=PermissionScope(row[3]),
            granted_at=row[4],
            granted_by=row[5],
            reason=row[6],
            expires_at=row[7],
        )
