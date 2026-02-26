"""Orchestration layer — Execution state machine.

Tracks the live status of every plan and action.
Persists to SQLite via aiosqlite for crash recovery.

State transitions:
    Plan:   pending -> running -> completed | failed | cancelled
    Action: pending -> waiting -> running -> completed | failed | skipped
                                         -> awaiting_approval (if required)
                                         -> rolled_back (on rollback)
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiosqlite

from llmos_bridge.exceptions import StateStoreError
from llmos_bridge.logging import get_logger
from llmos_bridge.protocol.models import ActionStatus, IMLPlan, PlanStatus

log = get_logger(__name__)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS plans (
    plan_id     TEXT PRIMARY KEY,
    status      TEXT NOT NULL,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    data        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS actions (
    plan_id     TEXT NOT NULL,
    action_id   TEXT NOT NULL,
    status      TEXT NOT NULL,
    started_at  REAL,
    finished_at REAL,
    result      TEXT,
    error       TEXT,
    attempt     INTEGER NOT NULL DEFAULT 0,
    module      TEXT NOT NULL DEFAULT '',
    action      TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (plan_id, action_id),
    FOREIGN KEY (plan_id) REFERENCES plans(plan_id)
);

CREATE INDEX IF NOT EXISTS idx_actions_plan_id ON actions (plan_id);
"""


@dataclass
class ActionState:
    action_id: str
    status: ActionStatus = ActionStatus.PENDING
    started_at: float | None = None
    finished_at: float | None = None
    result: Any = None
    error: str | None = None
    attempt: int = 0
    module: str = ""    # module_id (e.g. "filesystem") — populated at plan creation
    action: str = ""    # action name (e.g. "read_file")  — populated at plan creation
    approval_metadata: dict[str, Any] | None = None  # decision, approved_by, timestamp


@dataclass
class ExecutionState:
    """In-memory snapshot of a plan's execution state."""

    plan_id: str
    plan_status: PlanStatus = PlanStatus.PENDING
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    actions: dict[str, ActionState] = field(default_factory=dict)

    @classmethod
    def from_plan(cls, plan: IMLPlan) -> "ExecutionState":
        state = cls(plan_id=plan.plan_id)
        for action in plan.actions:
            state.actions[action.id] = ActionState(
                action_id=action.id,
                module=action.module,
                action=action.action,
            )
        return state

    def get_action(self, action_id: str) -> ActionState:
        return self.actions[action_id]

    def all_completed(self) -> bool:
        return all(
            a.status in (ActionStatus.COMPLETED, ActionStatus.SKIPPED)
            for a in self.actions.values()
        )

    def any_failed(self) -> bool:
        return any(a.status == ActionStatus.FAILED for a in self.actions.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "plan_status": self.plan_status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "actions": {
                aid: {
                    "status": a.status.value,
                    "started_at": a.started_at,
                    "finished_at": a.finished_at,
                    "result": a.result,
                    "error": a.error,
                    "attempt": a.attempt,
                    "approval_metadata": a.approval_metadata,
                }
                for aid, a in self.actions.items()
            },
        }


class PlanStateStore:
    """Async SQLite-backed store for plan execution states.

    Usage::

        store = PlanStateStore(Path("~/.llmos/state.db"))
        await store.init()
        await store.create(state)
        await store.update_plan_status(plan_id, PlanStatus.RUNNING)
        await store.update_action(plan_id, action_id, status=ActionStatus.COMPLETED, result={})
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path.expanduser()
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def init(self) -> None:
        """Open the database and create tables if they do not exist."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._conn = await aiosqlite.connect(str(self._db_path))
            await self._conn.execute("PRAGMA journal_mode=WAL")
            await self._conn.execute("PRAGMA foreign_keys=ON")
            await self._conn.executescript(_SCHEMA_SQL)
            await self._conn.commit()
            log.info("state_store_ready", db=str(self._db_path))
        except Exception as exc:
            raise StateStoreError(f"Failed to initialise state store: {exc}") from exc

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def create(self, state: ExecutionState) -> None:
        """Persist a new ExecutionState (plan + all actions)."""
        now = time.time()
        async with self._lock:
            assert self._conn is not None
            await self._conn.execute(
                "INSERT INTO plans (plan_id, status, created_at, updated_at, data) VALUES (?,?,?,?,?)",
                (state.plan_id, state.plan_status.value, now, now, json.dumps({})),
            )
            for action_id, action_state in state.actions.items():
                await self._conn.execute(
                    "INSERT INTO actions (plan_id, action_id, status, module, action) VALUES (?,?,?,?,?)",
                    (state.plan_id, action_id, action_state.status.value,
                     action_state.module, action_state.action),
                )
            await self._conn.commit()

    async def update_plan_status(self, plan_id: str, status: PlanStatus) -> None:
        async with self._lock:
            assert self._conn is not None
            await self._conn.execute(
                "UPDATE plans SET status=?, updated_at=? WHERE plan_id=?",
                (status.value, time.time(), plan_id),
            )
            await self._conn.commit()

    async def update_action(
        self,
        plan_id: str,
        action_id: str,
        status: ActionStatus,
        result: Any = None,
        error: str | None = None,
        attempt: int | None = None,
    ) -> None:
        now = time.time()
        async with self._lock:
            assert self._conn is not None
            started_at = now if status == ActionStatus.RUNNING else None
            finished_at = (
                now
                if status
                in (
                    ActionStatus.COMPLETED,
                    ActionStatus.FAILED,
                    ActionStatus.SKIPPED,
                    ActionStatus.ROLLED_BACK,
                )
                else None
            )
            await self._conn.execute(
                """UPDATE actions
                   SET status=?, result=?, error=?, attempt=COALESCE(?,attempt),
                       started_at=COALESCE(?,started_at), finished_at=COALESCE(?,finished_at)
                   WHERE plan_id=? AND action_id=?""",
                (
                    status.value,
                    json.dumps(result) if result is not None else None,
                    error,
                    attempt,
                    started_at,
                    finished_at,
                    plan_id,
                    action_id,
                ),
            )
            await self._conn.commit()

    async def get(self, plan_id: str) -> ExecutionState | None:
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT plan_id, status, created_at, updated_at FROM plans WHERE plan_id=?",
            (plan_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None

        state = ExecutionState(
            plan_id=row[0],
            plan_status=PlanStatus(row[1]),
            created_at=row[2],
            updated_at=row[3],
        )
        async with self._conn.execute(
            "SELECT action_id, status, started_at, finished_at, result, error, attempt, module, action "
            "FROM actions WHERE plan_id=?",
            (plan_id,),
        ) as cursor:
            async for arow in cursor:
                state.actions[arow[0]] = ActionState(
                    action_id=arow[0],
                    status=ActionStatus(arow[1]),
                    started_at=arow[2],
                    finished_at=arow[3],
                    result=json.loads(arow[4]) if arow[4] else None,
                    error=arow[5],
                    attempt=arow[6],
                    module=arow[7],
                    action=arow[8],
                )
        return state

    async def list_plans(
        self, status: PlanStatus | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        assert self._conn is not None
        if status:
            query = "SELECT plan_id, status, created_at, updated_at FROM plans WHERE status=? ORDER BY created_at DESC LIMIT ?"
            params: tuple[Any, ...] = (status.value, limit)
        else:
            query = "SELECT plan_id, status, created_at, updated_at FROM plans ORDER BY created_at DESC LIMIT ?"
            params = (limit,)
        async with self._conn.execute(query, params) as cursor:
            rows = await cursor.fetchall()
        return [
            {"plan_id": r[0], "status": r[1], "created_at": r[2], "updated_at": r[3]}
            for r in rows
        ]
