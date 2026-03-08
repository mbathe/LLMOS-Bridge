"""AppStore — SQLite persistence for installed LLMOS applications.

Stores app metadata (name, version, path, status) in SQLite so that
apps can be listed, started, stopped, and re-loaded across daemon restarts.

Usage:
    store = AppStore(Path("~/.llmos/apps.db"))
    await store.init()
    app_id = await store.register(app_def, "/path/to/app.yaml")
    apps = await store.list_apps()
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import asyncio

import aiosqlite

import logging

logger = logging.getLogger(__name__)


class AppStatus(str, Enum):
    """Status of a registered app."""
    registered = "registered"
    running = "running"
    stopped = "stopped"
    error = "error"


@dataclass
class AppRecord:
    """A registered app in the store."""
    id: str
    name: str
    version: str
    description: str
    author: str
    file_path: str
    status: AppStatus
    tags: list[str] = field(default_factory=list)
    created_at: float = 0.0
    updated_at: float = 0.0
    last_run_at: float = 0.0
    run_count: int = 0
    error_message: str = ""
    config_json: str = ""  # serialized trigger/agent summary
    application_id: str = ""  # linked dashboard Application (identity system)
    prepared: bool = False  # True after daemon prepare() pre-loaded everything

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "file_path": self.file_path,
            "status": self.status.value,
            "tags": self.tags,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_run_at": self.last_run_at,
            "run_count": self.run_count,
            "error_message": self.error_message,
            "application_id": self.application_id,
            "prepared": self.prepared,
        }


_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS apps (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    version         TEXT NOT NULL DEFAULT '1.0',
    description     TEXT NOT NULL DEFAULT '',
    author          TEXT NOT NULL DEFAULT '',
    file_path       TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'registered',
    tags_json       TEXT NOT NULL DEFAULT '[]',
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL,
    last_run_at     REAL NOT NULL DEFAULT 0,
    run_count       INTEGER NOT NULL DEFAULT 0,
    error_message   TEXT NOT NULL DEFAULT '',
    config_json     TEXT NOT NULL DEFAULT '{}',
    application_id  TEXT NOT NULL DEFAULT '',
    prepared        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_apps_name ON apps (name);
CREATE INDEX IF NOT EXISTS idx_apps_status ON apps (status);
CREATE INDEX IF NOT EXISTS idx_apps_application_id ON apps (application_id);
"""

_MIGRATION_SQL = [
    "ALTER TABLE apps ADD COLUMN application_id TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE apps ADD COLUMN prepared INTEGER NOT NULL DEFAULT 0",
]


class AppStore:
    """Async SQLite store for registered LLMOS applications."""

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = Path(db_path).expanduser()
        self._conn: aiosqlite.Connection | None = None
        self._write_lock = asyncio.Lock()  # serialize writes to prevent SQLite contention

    async def init(self) -> None:
        """Initialize the database."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(str(self._db_path))
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.executescript(_SCHEMA_SQL)
        await self._conn.commit()
        # Run schema migrations for existing databases
        for sql in _MIGRATION_SQL:
            try:
                await self._conn.execute(sql)
                await self._conn.commit()
            except Exception:
                pass  # Column already exists

    async def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def register(
        self,
        app_id: str,
        name: str,
        version: str,
        file_path: str,
        *,
        description: str = "",
        author: str = "",
        tags: list[str] | None = None,
        config_json: str = "{}",
        application_id: str = "",
    ) -> AppRecord:
        """Register a new app or update an existing one."""
        assert self._conn is not None
        now = time.time()
        tags_json = json.dumps(tags or [])

        async with self._write_lock:
            await self._conn.execute(
                """INSERT INTO apps (id, name, version, description, author, file_path,
                                     status, tags_json, created_at, updated_at, config_json,
                                     application_id, prepared)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                   ON CONFLICT(id) DO UPDATE SET
                     name=excluded.name, version=excluded.version,
                     description=excluded.description, author=excluded.author,
                     file_path=excluded.file_path, tags_json=excluded.tags_json,
                     updated_at=excluded.updated_at, config_json=excluded.config_json,
                     application_id=excluded.application_id, prepared=0
                """,
                (app_id, name, version, description, author, file_path,
                 AppStatus.registered.value, tags_json, now, now, config_json,
                 application_id),
            )
            await self._conn.commit()
        return await self.get(app_id)  # type: ignore[return-value]

    async def mark_prepared(self, app_id: str) -> bool:
        """Mark an app as prepared (all resources pre-loaded)."""
        assert self._conn is not None
        now = time.time()
        async with self._write_lock:
            cursor = await self._conn.execute(
                "UPDATE apps SET prepared = 1, updated_at = ? WHERE id = ?",
                (now, app_id),
            )
            await self._conn.commit()
        return cursor.rowcount > 0

    async def get(self, app_id: str) -> AppRecord | None:
        """Get an app by ID."""
        assert self._conn is not None
        cursor = await self._conn.execute("SELECT * FROM apps WHERE id = ?", (app_id,))
        row = await cursor.fetchone()
        if not row:
            return None
        return self._row_to_record(row)

    async def get_by_name(self, name: str) -> AppRecord | None:
        """Get an app by name."""
        assert self._conn is not None
        cursor = await self._conn.execute("SELECT * FROM apps WHERE name = ?", (name,))
        row = await cursor.fetchone()
        if not row:
            return None
        return self._row_to_record(row)

    async def list_apps(
        self,
        *,
        status: AppStatus | None = None,
        tag: str | None = None,
    ) -> list[AppRecord]:
        """List all registered apps, optionally filtered."""
        assert self._conn is not None
        query = "SELECT * FROM apps"
        params: list[Any] = []

        if status:
            query += " WHERE status = ?"
            params.append(status.value)

        query += " ORDER BY updated_at DESC"
        cursor = await self._conn.execute(query, params)
        rows = await cursor.fetchall()
        records = [self._row_to_record(r) for r in rows]

        if tag:
            records = [r for r in records if tag in r.tags]

        return records

    async def update_status(self, app_id: str, status: AppStatus, error_message: str = "") -> bool:
        """Update the status of an app."""
        assert self._conn is not None
        now = time.time()
        async with self._write_lock:
            cursor = await self._conn.execute(
                "UPDATE apps SET status = ?, error_message = ?, updated_at = ? WHERE id = ?",
                (status.value, error_message, now, app_id),
            )
            await self._conn.commit()
        return cursor.rowcount > 0

    async def record_run(self, app_id: str) -> bool:
        """Increment run count, update last_run_at, and clear any previous error state."""
        assert self._conn is not None
        now = time.time()
        async with self._write_lock:
            cursor = await self._conn.execute(
                """UPDATE apps
                   SET run_count = run_count + 1,
                       last_run_at = ?,
                       updated_at = ?,
                       error_message = '',
                       status = CASE WHEN status = 'error' THEN 'registered' ELSE status END
                   WHERE id = ?""",
                (now, now, app_id),
            )
            await self._conn.commit()
        return cursor.rowcount > 0

    async def update_yaml(self, app_id: str, yaml_text: str) -> bool:
        """Update the stored YAML text and reset prepared flag (capabilities may have changed)."""
        assert self._conn is not None
        now = time.time()
        async with self._write_lock:
            cursor = await self._conn.execute(
                "UPDATE apps SET config_json = ?, updated_at = ?, prepared = 0 WHERE id = ?",
                (yaml_text, now, app_id),
            )
            await self._conn.commit()
        return cursor.rowcount > 0

    async def get_by_application_id(self, application_id: str) -> AppRecord | None:
        """Find the YAML app linked to a given Application identity ID."""
        assert self._conn is not None
        cursor = await self._conn.execute(
            "SELECT * FROM apps WHERE application_id = ?", (application_id,)
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return self._row_to_record(row)

    async def delete(self, app_id: str) -> bool:
        """Delete an app from the store."""
        assert self._conn is not None
        async with self._write_lock:
            cursor = await self._conn.execute("DELETE FROM apps WHERE id = ?", (app_id,))
            await self._conn.commit()
        return cursor.rowcount > 0

    def _row_to_record(self, row: Any) -> AppRecord:
        d = dict(row)
        return AppRecord(
            id=d["id"],
            name=d["name"],
            version=d["version"],
            description=d["description"],
            author=d["author"],
            file_path=d["file_path"],
            status=AppStatus(d["status"]),
            tags=json.loads(d.get("tags_json", "[]")),
            created_at=d["created_at"],
            updated_at=d["updated_at"],
            last_run_at=d.get("last_run_at", 0),
            run_count=d.get("run_count", 0),
            error_message=d.get("error_message", ""),
            config_json=d.get("config_json", "{}"),
            application_id=d.get("application_id", ""),
            prepared=bool(d.get("prepared", 0)),
        )
