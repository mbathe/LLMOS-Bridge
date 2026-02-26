"""Memory layer — Key-value store backed by SQLite.

Provides persistent cross-session key-value storage for action results,
user preferences, and session state.  Separate from the execution state
store (orchestration/state.py) which is ephemeral and plan-scoped.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import aiosqlite

from llmos_bridge.exceptions import StateStoreError
from llmos_bridge.logging import get_logger

log = get_logger(__name__)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS kv_store (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    session_id  TEXT,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    ttl         REAL          -- Unix timestamp after which the entry expires
);
CREATE INDEX IF NOT EXISTS idx_kv_session ON kv_store (session_id);
"""


class KeyValueStore:
    """Async persistent key-value store backed by SQLite.

    Usage::

        store = KeyValueStore(Path("~/.llmos/state.db"))
        await store.init()
        await store.set("api_response", {"data": [1, 2, 3]})
        value = await store.get("api_response")
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path.expanduser()
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def init(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._conn = await aiosqlite.connect(str(self._db_path))
            await self._conn.execute("PRAGMA journal_mode=WAL")
            await self._conn.executescript(_SCHEMA_SQL)
            await self._conn.commit()
        except Exception as exc:
            raise StateStoreError(f"KeyValueStore init failed: {exc}") from exc

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def set(
        self,
        key: str,
        value: Any,
        session_id: str | None = None,
        ttl_seconds: float | None = None,
    ) -> None:
        assert self._conn is not None
        now = time.time()
        ttl = now + ttl_seconds if ttl_seconds else None
        serialised = json.dumps(value, default=str)

        async with self._lock:
            await self._conn.execute(
                """INSERT INTO kv_store (key, value, session_id, created_at, updated_at, ttl)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET
                     value=excluded.value,
                     session_id=excluded.session_id,
                     updated_at=excluded.updated_at,
                     ttl=excluded.ttl""",
                (key, serialised, session_id, now, now, ttl),
            )
            await self._conn.commit()

    async def get(self, key: str) -> Any | None:
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT value, ttl FROM kv_store WHERE key=?", (key,)
        ) as cursor:
            row = await cursor.fetchone()

        if row is None:
            return None
        value_str, ttl = row
        if ttl and time.time() > ttl:
            await self.delete(key)
            return None
        return json.loads(value_str)

    async def delete(self, key: str) -> None:
        assert self._conn is not None
        async with self._lock:
            await self._conn.execute("DELETE FROM kv_store WHERE key=?", (key,))
            await self._conn.commit()

    async def get_many(self, keys: list[str]) -> dict[str, Any]:
        result = {}
        for key in keys:
            value = await self.get(key)
            if value is not None:
                result[key] = value
        return result

    async def list_keys(self, session_id: str | None = None) -> list[str]:
        assert self._conn is not None
        if session_id:
            query = "SELECT key FROM kv_store WHERE session_id=? AND (ttl IS NULL OR ttl > ?)"
            params: tuple[Any, ...] = (session_id, time.time())
        else:
            query = "SELECT key FROM kv_store WHERE ttl IS NULL OR ttl > ?"
            params = (time.time(),)
        async with self._conn.execute(query, params) as cursor:
            rows = await cursor.fetchall()
        return [row[0] for row in rows]

    async def purge_expired(self) -> int:
        assert self._conn is not None
        async with self._lock:
            cursor = await self._conn.execute(
                "DELETE FROM kv_store WHERE ttl IS NOT NULL AND ttl <= ?", (time.time(),)
            )
            await self._conn.commit()
            return cursor.rowcount or 0
