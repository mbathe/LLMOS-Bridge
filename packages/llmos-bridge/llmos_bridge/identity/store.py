"""Identity layer — SQLite persistence for applications, agents, API keys, sessions.

Follows the same ``init()`` / ``close()`` pattern as ``PlanStateStore``
in ``orchestration/state.py``.  All methods are async and use an asyncio
lock to serialise writes (SQLite WAL mode for concurrent reads).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import time
import uuid
from pathlib import Path
from typing import Any

import aiosqlite

from llmos_bridge.identity.models import Agent, ApiKey, Application, Role, Session
from llmos_bridge.logging import get_logger

log = get_logger(__name__)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS applications (
    app_id                TEXT PRIMARY KEY,
    name                  TEXT NOT NULL UNIQUE,
    description           TEXT NOT NULL DEFAULT '',
    created_at            REAL NOT NULL,
    updated_at            REAL NOT NULL,
    enabled               INTEGER NOT NULL DEFAULT 1,
    max_concurrent_plans  INTEGER NOT NULL DEFAULT 10,
    max_actions_per_plan  INTEGER NOT NULL DEFAULT 50,
    allowed_modules       TEXT NOT NULL DEFAULT '[]',
    allowed_actions       TEXT NOT NULL DEFAULT '{}',
    tags                  TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS agents (
    agent_id    TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    app_id      TEXT NOT NULL REFERENCES applications(app_id),
    role        TEXT NOT NULL DEFAULT 'agent',
    created_at  REAL NOT NULL,
    enabled     INTEGER NOT NULL DEFAULT 1,
    metadata    TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_agents_app ON agents (app_id);

CREATE TABLE IF NOT EXISTS api_keys (
    key_id      TEXT PRIMARY KEY,
    agent_id    TEXT NOT NULL REFERENCES agents(agent_id),
    app_id      TEXT NOT NULL REFERENCES applications(app_id),
    prefix      TEXT NOT NULL DEFAULT '',
    key_hash    TEXT NOT NULL,
    created_at  REAL NOT NULL,
    expires_at  REAL,
    revoked     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_api_keys_prefix ON api_keys (prefix);
CREATE INDEX IF NOT EXISTS idx_api_keys_app ON api_keys (app_id);

CREATE TABLE IF NOT EXISTS sessions (
    session_id           TEXT PRIMARY KEY,
    app_id               TEXT NOT NULL REFERENCES applications(app_id),
    agent_id             TEXT,
    created_at           REAL NOT NULL,
    last_active          REAL NOT NULL,
    expires_at           REAL,
    idle_timeout_seconds INTEGER,
    allowed_modules      TEXT NOT NULL DEFAULT '[]',
    permission_grants    TEXT NOT NULL DEFAULT '[]',
    permission_denials   TEXT NOT NULL DEFAULT '[]',
    metadata             TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_sessions_app ON sessions (app_id);
"""


def _hash_key(cleartext: str) -> str:
    """Hash an API key with SHA-256 (fast, deterministic — suitable for token lookup)."""
    return hashlib.sha256(cleartext.encode()).hexdigest()


def _generate_api_key() -> tuple[str, str]:
    """Generate a random API key and return (cleartext, prefix).

    Format: ``llmos_<32 hex chars>`` (prefix = first 12 chars).
    """
    raw = secrets.token_hex(16)
    key = f"llmos_{raw}"
    prefix = key[:12]
    return key, prefix


class IdentityStore:
    """Async SQLite store for identity entities (applications, agents, keys, sessions)."""

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = Path(db_path).expanduser()
        self._db: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def init(self) -> None:
        """Open the database and create tables if needed."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(self._db_path))
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.executescript(_SCHEMA_SQL)
        await self._migrate(self._db)
        await self._db.commit()
        log.info("identity_store_init", db_path=str(self._db_path))

    @staticmethod
    async def _migrate(db: aiosqlite.Connection) -> None:
        """Run forward-only schema migrations for existing databases."""
        # Phase 6: add allowed_actions column to applications if it doesn't exist.
        cursor = await db.execute("PRAGMA table_info(applications)")
        app_columns = {row[1] for row in await cursor.fetchall()}
        if "allowed_actions" not in app_columns:
            await db.execute(
                "ALTER TABLE applications ADD COLUMN allowed_actions TEXT NOT NULL DEFAULT '{}'"
            )

        # Phase 6 (sessions): add expiry and security constraint columns.
        cursor = await db.execute("PRAGMA table_info(sessions)")
        sess_columns = {row[1] for row in await cursor.fetchall()}
        if "expires_at" not in sess_columns:
            await db.execute("ALTER TABLE sessions ADD COLUMN expires_at REAL")
        if "idle_timeout_seconds" not in sess_columns:
            await db.execute("ALTER TABLE sessions ADD COLUMN idle_timeout_seconds INTEGER")
        if "allowed_modules" not in sess_columns:
            await db.execute(
                "ALTER TABLE sessions ADD COLUMN allowed_modules TEXT NOT NULL DEFAULT '[]'"
            )
        if "permission_grants" not in sess_columns:
            await db.execute(
                "ALTER TABLE sessions ADD COLUMN permission_grants TEXT NOT NULL DEFAULT '[]'"
            )
        if "permission_denials" not in sess_columns:
            await db.execute(
                "ALTER TABLE sessions ADD COLUMN permission_denials TEXT NOT NULL DEFAULT '[]'"
            )

    async def close(self) -> None:
        """Close the database connection."""
        if self._db is not None:
            await self._db.close()
            self._db = None

    # ------------------------------------------------------------------
    # Default application
    # ------------------------------------------------------------------

    async def ensure_default_app(self, name: str = "default") -> Application:
        """Create the default application if it doesn't exist. Return it."""
        existing = await self.get_application_by_name(name)
        if existing is not None:
            return existing
        return await self.create_application(
            name=name,
            description="Default application (auto-created)",
            app_id="default",
        )

    # ------------------------------------------------------------------
    # Applications
    # ------------------------------------------------------------------

    async def create_application(
        self,
        name: str,
        description: str = "",
        app_id: str | None = None,
        max_concurrent_plans: int = 10,
        max_actions_per_plan: int = 50,
        allowed_modules: list[str] | None = None,
        allowed_actions: dict[str, list[str]] | None = None,
        tags: dict[str, str] | None = None,
    ) -> Application:
        """Create a new application."""
        assert self._db is not None
        now = time.time()
        app = Application(
            app_id=app_id or str(uuid.uuid4()),
            name=name,
            description=description,
            created_at=now,
            updated_at=now,
            max_concurrent_plans=max_concurrent_plans,
            max_actions_per_plan=max_actions_per_plan,
            allowed_modules=allowed_modules or [],
            allowed_actions=allowed_actions or {},
            tags=tags or {},
        )
        async with self._lock:
            await self._db.execute(
                """INSERT INTO applications
                   (app_id, name, description, created_at, updated_at, enabled,
                    max_concurrent_plans, max_actions_per_plan, allowed_modules,
                    allowed_actions, tags)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    app.app_id,
                    app.name,
                    app.description,
                    app.created_at,
                    app.updated_at,
                    int(app.enabled),
                    app.max_concurrent_plans,
                    app.max_actions_per_plan,
                    json.dumps(app.allowed_modules),
                    json.dumps(app.allowed_actions),
                    json.dumps(app.tags),
                ),
            )
            await self._db.commit()
        log.info("application_created", app_id=app.app_id, name=app.name)
        return app

    async def get_application(self, app_id: str) -> Application | None:
        """Get an application by ID."""
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT * FROM applications WHERE app_id = ?", (app_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_application(row)

    async def get_application_by_name(self, name: str) -> Application | None:
        """Get an application by name."""
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT * FROM applications WHERE name = ?", (name,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_application(row)

    async def list_applications(self, include_disabled: bool = False) -> list[Application]:
        """List all applications."""
        assert self._db is not None
        if include_disabled:
            cursor = await self._db.execute(
                "SELECT * FROM applications ORDER BY created_at DESC"
            )
        else:
            cursor = await self._db.execute(
                "SELECT * FROM applications WHERE enabled = 1 ORDER BY created_at DESC"
            )
        rows = await cursor.fetchall()
        return [self._row_to_application(row) for row in rows]

    async def update_application(self, app_id: str, **kwargs: Any) -> Application | None:
        """Update application fields. Returns the updated application or None."""
        assert self._db is not None
        allowed_fields = {
            "name", "description", "enabled", "max_concurrent_plans",
            "max_actions_per_plan", "allowed_modules", "allowed_actions", "tags",
        }
        updates = {k: v for k, v in kwargs.items() if k in allowed_fields}
        if not updates:
            return await self.get_application(app_id)

        updates["updated_at"] = time.time()

        # Serialise JSON fields.
        if "allowed_modules" in updates:
            updates["allowed_modules"] = json.dumps(updates["allowed_modules"])
        if "allowed_actions" in updates:
            updates["allowed_actions"] = json.dumps(updates["allowed_actions"])
        if "tags" in updates:
            updates["tags"] = json.dumps(updates["tags"])
        if "enabled" in updates:
            updates["enabled"] = int(updates["enabled"])

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [app_id]

        async with self._lock:
            await self._db.execute(
                f"UPDATE applications SET {set_clause} WHERE app_id = ?",  # noqa: S608
                values,
            )
            await self._db.commit()
        return await self.get_application(app_id)

    async def delete_application(self, app_id: str, hard: bool = False) -> bool:
        """Soft-delete (disable) or hard-delete an application.

        Returns True if the application was found and deleted/disabled.
        """
        assert self._db is not None
        async with self._lock:
            if hard:
                # Cascade: delete agents, keys, sessions first.
                await self._db.execute("DELETE FROM api_keys WHERE app_id = ?", (app_id,))
                await self._db.execute("DELETE FROM sessions WHERE app_id = ?", (app_id,))
                await self._db.execute("DELETE FROM agents WHERE app_id = ?", (app_id,))
                cursor = await self._db.execute(
                    "DELETE FROM applications WHERE app_id = ?", (app_id,)
                )
            else:
                cursor = await self._db.execute(
                    "UPDATE applications SET enabled = 0, updated_at = ? WHERE app_id = ?",
                    (time.time(), app_id),
                )
            await self._db.commit()
        return cursor.rowcount > 0

    def _row_to_application(self, row: aiosqlite.Row) -> Application:
        return Application(
            app_id=row["app_id"],
            name=row["name"],
            description=row["description"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            enabled=bool(row["enabled"]),
            max_concurrent_plans=row["max_concurrent_plans"],
            max_actions_per_plan=row["max_actions_per_plan"],
            allowed_modules=json.loads(row["allowed_modules"]),
            allowed_actions=json.loads(row["allowed_actions"]),
            tags=json.loads(row["tags"]),
        )

    # ------------------------------------------------------------------
    # Agents
    # ------------------------------------------------------------------

    async def create_agent(
        self,
        name: str,
        app_id: str,
        role: Role = Role.AGENT,
        agent_id: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> Agent:
        """Create a new agent within an application."""
        assert self._db is not None
        agent = Agent(
            agent_id=agent_id or str(uuid.uuid4()),
            name=name,
            app_id=app_id,
            role=role,
            created_at=time.time(),
            metadata=metadata or {},
        )
        async with self._lock:
            await self._db.execute(
                """INSERT INTO agents (agent_id, name, app_id, role, created_at, enabled, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    agent.agent_id,
                    agent.name,
                    agent.app_id,
                    agent.role.value,
                    agent.created_at,
                    int(agent.enabled),
                    json.dumps(agent.metadata),
                ),
            )
            await self._db.commit()
        log.info("agent_created", agent_id=agent.agent_id, app_id=app_id, role=role.value)
        return agent

    async def get_agent(self, agent_id: str) -> Agent | None:
        """Get an agent by ID."""
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT * FROM agents WHERE agent_id = ?", (agent_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_agent(row)

    async def list_agents(self, app_id: str, include_disabled: bool = False) -> list[Agent]:
        """List agents within an application."""
        assert self._db is not None
        if include_disabled:
            cursor = await self._db.execute(
                "SELECT * FROM agents WHERE app_id = ? ORDER BY created_at DESC",
                (app_id,),
            )
        else:
            cursor = await self._db.execute(
                "SELECT * FROM agents WHERE app_id = ? AND enabled = 1 ORDER BY created_at DESC",
                (app_id,),
            )
        rows = await cursor.fetchall()
        return [self._row_to_agent(row) for row in rows]

    async def delete_agent(self, agent_id: str) -> bool:
        """Delete an agent and revoke all its API keys."""
        assert self._db is not None
        async with self._lock:
            await self._db.execute(
                "UPDATE api_keys SET revoked = 1 WHERE agent_id = ?", (agent_id,)
            )
            cursor = await self._db.execute(
                "DELETE FROM agents WHERE agent_id = ?", (agent_id,)
            )
            await self._db.commit()
        return cursor.rowcount > 0

    def _row_to_agent(self, row: aiosqlite.Row) -> Agent:
        return Agent(
            agent_id=row["agent_id"],
            name=row["name"],
            app_id=row["app_id"],
            role=Role(row["role"]),
            created_at=row["created_at"],
            enabled=bool(row["enabled"]),
            metadata=json.loads(row["metadata"]),
        )

    # ------------------------------------------------------------------
    # API Keys
    # ------------------------------------------------------------------

    async def create_api_key(
        self,
        agent_id: str,
        app_id: str,
        expires_at: float | None = None,
    ) -> tuple[ApiKey, str]:
        """Create a new API key for an agent.

        Returns (ApiKey, cleartext_key).  The cleartext key is only
        available at creation time — it is NOT stored.
        """
        assert self._db is not None
        cleartext, prefix = _generate_api_key()
        key_hash = _hash_key(cleartext)
        api_key = ApiKey(
            agent_id=agent_id,
            app_id=app_id,
            prefix=prefix,
            key_hash=key_hash,
            created_at=time.time(),
            expires_at=expires_at,
        )
        async with self._lock:
            await self._db.execute(
                """INSERT INTO api_keys
                   (key_id, agent_id, app_id, prefix, key_hash, created_at, expires_at, revoked)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    api_key.key_id,
                    api_key.agent_id,
                    api_key.app_id,
                    api_key.prefix,
                    api_key.key_hash,
                    api_key.created_at,
                    api_key.expires_at,
                    int(api_key.revoked),
                ),
            )
            await self._db.commit()
        log.info("api_key_created", key_id=api_key.key_id, agent_id=agent_id, prefix=prefix)
        return api_key, cleartext

    async def resolve_api_key(self, cleartext: str) -> tuple[str, str, Role] | None:
        """Resolve a cleartext API key to (app_id, agent_id, role).

        Returns None if the key is invalid, revoked, or expired.
        """
        assert self._db is not None
        key_hash = _hash_key(cleartext)
        cursor = await self._db.execute(
            """SELECT k.app_id, k.agent_id, a.role, k.expires_at, k.revoked,
                      a.enabled AS agent_enabled, ap.enabled AS app_enabled
               FROM api_keys k
               JOIN agents a ON k.agent_id = a.agent_id
               JOIN applications ap ON k.app_id = ap.app_id
               WHERE k.key_hash = ?""",
            (key_hash,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        if row["revoked"]:
            return None
        if not row["agent_enabled"]:
            return None
        if not row["app_enabled"]:
            return None
        if row["expires_at"] is not None and row["expires_at"] < time.time():
            return None
        return (row["app_id"], row["agent_id"], Role(row["role"]))

    async def list_api_keys(self, agent_id: str) -> list[ApiKey]:
        """List API keys for an agent (excludes revoked)."""
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT * FROM api_keys WHERE agent_id = ? AND revoked = 0 ORDER BY created_at DESC",
            (agent_id,),
        )
        rows = await cursor.fetchall()
        return [self._row_to_api_key(row) for row in rows]

    async def revoke_api_key(self, key_id: str) -> bool:
        """Revoke an API key."""
        assert self._db is not None
        async with self._lock:
            cursor = await self._db.execute(
                "UPDATE api_keys SET revoked = 1 WHERE key_id = ?", (key_id,)
            )
            await self._db.commit()
        return cursor.rowcount > 0

    def _row_to_api_key(self, row: aiosqlite.Row) -> ApiKey:
        return ApiKey(
            key_id=row["key_id"],
            agent_id=row["agent_id"],
            app_id=row["app_id"],
            prefix=row["prefix"],
            key_hash=row["key_hash"],
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            revoked=bool(row["revoked"]),
        )

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------

    async def create_session(
        self,
        app_id: str,
        agent_id: str | None = None,
        session_id: str | None = None,
        expires_at: float | None = None,
        idle_timeout_seconds: int | None = None,
        allowed_modules: list[str] | None = None,
        permission_grants: list[str] | None = None,
        permission_denials: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Session:
        """Create a new session."""
        assert self._db is not None
        now = time.time()
        session = Session(
            session_id=session_id or str(uuid.uuid4()),
            app_id=app_id,
            agent_id=agent_id,
            created_at=now,
            last_active=now,
            expires_at=expires_at,
            idle_timeout_seconds=idle_timeout_seconds,
            allowed_modules=allowed_modules or [],
            permission_grants=permission_grants or [],
            permission_denials=permission_denials or [],
            metadata=metadata or {},
        )
        async with self._lock:
            await self._db.execute(
                """INSERT INTO sessions
                   (session_id, app_id, agent_id, created_at, last_active,
                    expires_at, idle_timeout_seconds, allowed_modules,
                    permission_grants, permission_denials, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session.session_id,
                    session.app_id,
                    session.agent_id,
                    session.created_at,
                    session.last_active,
                    session.expires_at,
                    session.idle_timeout_seconds,
                    json.dumps(session.allowed_modules),
                    json.dumps(session.permission_grants),
                    json.dumps(session.permission_denials),
                    json.dumps(session.metadata),
                ),
            )
            await self._db.commit()
        return session

    async def get_session(self, session_id: str) -> Session | None:
        """Get a session by ID."""
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_session(row)

    async def list_sessions(
        self,
        app_id: str,
        limit: int = 100,
    ) -> list[Session]:
        """List sessions for an application, most recent first."""
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT * FROM sessions WHERE app_id = ? ORDER BY last_active DESC LIMIT ?",
            (app_id, limit),
        )
        rows = await cursor.fetchall()
        return [self._row_to_session(row) for row in rows]

    async def update_session(self, session_id: str, **kwargs: Any) -> Session | None:
        """Update session fields. Returns the updated session or None if not found."""
        assert self._db is not None
        allowed_fields = {
            "expires_at", "idle_timeout_seconds", "allowed_modules",
            "permission_grants", "permission_denials", "metadata",
        }
        updates = {k: v for k, v in kwargs.items() if k in allowed_fields}
        if not updates:
            return await self.get_session(session_id)

        # Serialise JSON fields.
        for field in ("allowed_modules", "permission_grants", "permission_denials", "metadata"):
            if field in updates:
                updates[field] = json.dumps(updates[field])

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [session_id]
        async with self._lock:
            await self._db.execute(
                f"UPDATE sessions SET {set_clause} WHERE session_id = ?",  # noqa: S608
                values,
            )
            await self._db.commit()
        return await self.get_session(session_id)

    async def touch_session(self, session_id: str) -> None:
        """Update the last_active timestamp of a session."""
        assert self._db is not None
        async with self._lock:
            await self._db.execute(
                "UPDATE sessions SET last_active = ? WHERE session_id = ?",
                (time.time(), session_id),
            )
            await self._db.commit()

    async def delete_session(self, session_id: str) -> bool:
        """Hard-delete a session. Returns True if it was found and deleted."""
        assert self._db is not None
        async with self._lock:
            cursor = await self._db.execute(
                "DELETE FROM sessions WHERE session_id = ?", (session_id,)
            )
            await self._db.commit()
        return cursor.rowcount > 0

    async def cleanup_expired_sessions(self, max_age_seconds: float) -> int:
        """Delete sessions older than max_age_seconds. Returns count deleted."""
        assert self._db is not None
        cutoff = time.time() - max_age_seconds
        async with self._lock:
            cursor = await self._db.execute(
                "DELETE FROM sessions WHERE last_active < ?", (cutoff,)
            )
            await self._db.commit()
        return cursor.rowcount

    def _row_to_session(self, row: aiosqlite.Row) -> Session:
        d = dict(row)
        return Session(
            session_id=d["session_id"],
            app_id=d["app_id"],
            agent_id=d["agent_id"],
            created_at=d["created_at"],
            last_active=d["last_active"],
            expires_at=d.get("expires_at"),
            idle_timeout_seconds=d.get("idle_timeout_seconds"),
            allowed_modules=json.loads(d.get("allowed_modules") or "[]"),
            permission_grants=json.loads(d.get("permission_grants") or "[]"),
            permission_denials=json.loads(d.get("permission_denials") or "[]"),
            metadata=json.loads(d["metadata"]),
        )

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    async def app_stats(self, app_id: str) -> dict[str, int]:
        """Return aggregate counts for an application."""
        assert self._db is not None
        agent_count = 0
        key_count = 0
        session_count = 0

        cursor = await self._db.execute(
            "SELECT COUNT(*) FROM agents WHERE app_id = ? AND enabled = 1", (app_id,)
        )
        row = await cursor.fetchone()
        if row:
            agent_count = row[0]

        cursor = await self._db.execute(
            "SELECT COUNT(*) FROM api_keys WHERE app_id = ? AND revoked = 0", (app_id,)
        )
        row = await cursor.fetchone()
        if row:
            key_count = row[0]

        cursor = await self._db.execute(
            "SELECT COUNT(*) FROM sessions WHERE app_id = ?", (app_id,)
        )
        row = await cursor.fetchone()
        if row:
            session_count = row[0]

        return {
            "agent_count": agent_count,
            "key_count": key_count,
            "session_count": session_count,
        }
