"""SQLite-backed persistence for TriggerDefinition objects.

Design mirrors ``orchestration/state.py``:
    - Single aiosqlite connection per store instance
    - All I/O is async
    - JSON serialisation for complex fields
    - No ORM dependency

Triggers persist across daemon restarts.  On startup, TriggerDaemon calls
``TriggerStore.load_active()`` to re-arm all ACTIVE / WATCHING triggers
that were running before the previous shutdown.

Schema
------
One table: ``triggers``
    trigger_id   TEXT PRIMARY KEY
    name         TEXT
    state        TEXT   (TriggerState.value)
    enabled      INTEGER (0/1)
    definition   TEXT   (full JSON serialisation of TriggerDefinition)
    created_at   REAL
    updated_at   REAL
    expires_at   REAL | NULL

Indices on (state) and (enabled) for fast daemon startup queries.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import aiosqlite

from llmos_bridge.logging import get_logger
from llmos_bridge.triggers.models import (
    TriggerCondition,
    TriggerDefinition,
    TriggerHealth,
    TriggerPriority,
    TriggerState,
    TriggerType,
)

log = get_logger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS triggers (
    trigger_id  TEXT PRIMARY KEY,
    name        TEXT NOT NULL DEFAULT '',
    state       TEXT NOT NULL DEFAULT 'registered',
    enabled     INTEGER NOT NULL DEFAULT 1,
    definition  TEXT NOT NULL,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    expires_at  REAL
);
CREATE INDEX IF NOT EXISTS idx_triggers_state   ON triggers(state);
CREATE INDEX IF NOT EXISTS idx_triggers_enabled ON triggers(enabled);
"""


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------


def _to_dict(t: TriggerDefinition) -> dict[str, Any]:
    """Convert a TriggerDefinition to a JSON-serialisable dict."""
    return {
        "trigger_id": t.trigger_id,
        "name": t.name,
        "description": t.description,
        "condition_type": t.condition.type.value,
        "condition_params": t.condition.params,
        "plan_template": t.plan_template,
        "plan_id_prefix": t.plan_id_prefix,
        "state": t.state.value,
        "priority": int(t.priority),
        "enabled": t.enabled,
        "min_interval_seconds": t.min_interval_seconds,
        "max_fires_per_hour": t.max_fires_per_hour,
        "conflict_policy": t.conflict_policy,
        "resource_lock": t.resource_lock,
        "parent_trigger_id": t.parent_trigger_id,
        "chain_depth": t.chain_depth,
        "max_chain_depth": t.max_chain_depth,
        "created_by": t.created_by,
        "tags": t.tags,
        "health_fire_count": t.health.fire_count,
        "health_fail_count": t.health.fail_count,
        "health_throttle_count": t.health.throttle_count,
        "health_last_fired_at": t.health.last_fired_at,
        "health_last_error": t.health.last_error,
        "health_avg_latency_ms": t.health.avg_latency_ms,
        "health_created_at": t.health.created_at,
        "created_at": t.created_at,
        "updated_at": t.updated_at,
        "expires_at": t.expires_at,
    }


def _from_dict(d: dict[str, Any]) -> TriggerDefinition:
    """Reconstruct a TriggerDefinition from its serialised dict."""
    t = TriggerDefinition.__new__(TriggerDefinition)
    t.trigger_id = d["trigger_id"]
    t.name = d.get("name", "")
    t.description = d.get("description", "")
    t.condition = TriggerCondition(
        type=TriggerType(d["condition_type"]),
        params=d.get("condition_params", {}),
    )
    t.plan_template = d.get("plan_template", {})
    t.plan_id_prefix = d.get("plan_id_prefix", "trigger")
    t.state = TriggerState(d.get("state", TriggerState.REGISTERED.value))
    t.priority = TriggerPriority(d.get("priority", TriggerPriority.NORMAL))
    t.enabled = bool(d.get("enabled", True))
    t.min_interval_seconds = float(d.get("min_interval_seconds", 0.0))
    t.max_fires_per_hour = int(d.get("max_fires_per_hour", 0))
    t.conflict_policy = d.get("conflict_policy", "queue")  # type: ignore[assignment]
    t.resource_lock = d.get("resource_lock")
    t.parent_trigger_id = d.get("parent_trigger_id")
    t.chain_depth = int(d.get("chain_depth", 0))
    t.max_chain_depth = int(d.get("max_chain_depth", 5))
    t.created_by = d.get("created_by", "user")
    t.tags = d.get("tags", [])
    t.health = TriggerHealth(
        fire_count=d.get("health_fire_count", 0),
        fail_count=d.get("health_fail_count", 0),
        throttle_count=d.get("health_throttle_count", 0),
        last_fired_at=d.get("health_last_fired_at"),
        last_error=d.get("health_last_error"),
        avg_latency_ms=float(d.get("health_avg_latency_ms", 0.0)),
        created_at=d.get("health_created_at", d.get("created_at", time.time())),
    )
    t.created_at = d.get("created_at", time.time())
    t.updated_at = d.get("updated_at", time.time())
    t.expires_at = d.get("expires_at")
    return t


# ---------------------------------------------------------------------------
# TriggerStore
# ---------------------------------------------------------------------------


class TriggerStore:
    """Async SQLite store for TriggerDefinition objects.

    Usage::

        store = TriggerStore(Path("~/.llmos/triggers.db"))
        await store.init()

        await store.save(trigger)
        trigger = await store.get("trigger_id")
        active = await store.load_active()
        await store.update_state("trigger_id", TriggerState.FIRED)
        await store.delete("trigger_id")
        count = await store.purge_expired()

        await store.close()
    """

    def __init__(self, db_path: Path) -> None:
        self._path = db_path.expanduser()
        self._conn: aiosqlite.Connection | None = None

    # ---------------------------------------------------------------------------
    # Lifecycle
    # ---------------------------------------------------------------------------

    async def init(self) -> None:
        """Open the database and create tables if needed."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(str(self._path))
        await self._conn.executescript(_SCHEMA)
        await self._conn.commit()
        log.info("trigger_store_initialized", path=str(self._path))

    async def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    # ---------------------------------------------------------------------------
    # CRUD
    # ---------------------------------------------------------------------------

    async def save(self, trigger: TriggerDefinition) -> None:
        """Insert or update a trigger (upsert by trigger_id).

        ``updated_at`` is refreshed automatically.
        """
        trigger.updated_at = time.time()
        definition_json = json.dumps(_to_dict(trigger), default=str)
        assert self._conn is not None
        await self._conn.execute(
            """
            INSERT INTO triggers
                (trigger_id, name, state, enabled, definition, created_at, updated_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(trigger_id) DO UPDATE SET
                name       = excluded.name,
                state      = excluded.state,
                enabled    = excluded.enabled,
                definition = excluded.definition,
                updated_at = excluded.updated_at,
                expires_at = excluded.expires_at
            """,
            (
                trigger.trigger_id,
                trigger.name,
                trigger.state.value,
                int(trigger.enabled),
                definition_json,
                trigger.created_at,
                trigger.updated_at,
                trigger.expires_at,
            ),
        )
        await self._conn.commit()

    async def get(self, trigger_id: str) -> TriggerDefinition | None:
        """Load a trigger by ID.  Returns None if not found."""
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT definition, state FROM triggers WHERE trigger_id = ?", (trigger_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        t = _from_dict(json.loads(row[0]))
        # The state column is authoritative (fast-path updates only update the column)
        t.state = TriggerState(row[1])
        return t

    async def list_all(self) -> list[TriggerDefinition]:
        """Return all triggers regardless of state."""
        assert self._conn is not None
        async with self._conn.execute("SELECT definition, state FROM triggers") as cursor:
            rows = await cursor.fetchall()
        result = []
        for r in rows:
            t = _from_dict(json.loads(r[0]))
            t.state = TriggerState(r[1])
            result.append(t)
        return result

    async def load_active(self) -> list[TriggerDefinition]:
        """Return all enabled triggers in ACTIVE or WATCHING state.

        Called at daemon startup to re-arm triggers after a restart.
        """
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT definition, state FROM triggers WHERE enabled = 1 AND state IN ('active', 'watching')"
        ) as cursor:
            rows = await cursor.fetchall()
        result = []
        for r in rows:
            t = _from_dict(json.loads(r[0]))
            t.state = TriggerState(r[1])
            result.append(t)
        return result

    async def list_by_state(self, state: TriggerState) -> list[TriggerDefinition]:
        """Return all triggers in the given state."""
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT definition, state FROM triggers WHERE state = ?", (state.value,)
        ) as cursor:
            rows = await cursor.fetchall()
        result = []
        for r in rows:
            t = _from_dict(json.loads(r[0]))
            t.state = TriggerState(r[1])
            result.append(t)
        return result

    async def update_state(self, trigger_id: str, state: TriggerState) -> None:
        """Fast-path state update — avoids re-serialising the full definition."""
        assert self._conn is not None
        now = time.time()
        await self._conn.execute(
            "UPDATE triggers SET state = ?, updated_at = ? WHERE trigger_id = ?",
            (state.value, now, trigger_id),
        )
        await self._conn.commit()

    async def delete(self, trigger_id: str) -> bool:
        """Delete a trigger.  Returns True if it existed."""
        assert self._conn is not None
        cursor = await self._conn.execute(
            "DELETE FROM triggers WHERE trigger_id = ?", (trigger_id,)
        )
        await self._conn.commit()
        return cursor.rowcount > 0

    async def purge_expired(self) -> int:
        """Delete all triggers past their ``expires_at``.

        Returns the number of deleted rows.
        """
        assert self._conn is not None
        cursor = await self._conn.execute(
            "DELETE FROM triggers WHERE expires_at IS NOT NULL AND expires_at < ?",
            (time.time(),),
        )
        await self._conn.commit()
        return cursor.rowcount
