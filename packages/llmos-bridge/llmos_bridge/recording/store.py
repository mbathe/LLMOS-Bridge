"""Shadow Recorder — SQLite persistence for WorkflowRecordings."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import aiosqlite

from llmos_bridge.logging import get_logger
from llmos_bridge.recording.models import RecordedPlan, RecordingStatus, WorkflowRecording

log = get_logger(__name__)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS recordings (
    recording_id   TEXT PRIMARY KEY,
    title          TEXT NOT NULL,
    description    TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'active',
    created_at     REAL NOT NULL,
    stopped_at     REAL,
    generated_plan TEXT
);

CREATE TABLE IF NOT EXISTS recorded_plans (
    recording_id  TEXT NOT NULL,
    plan_id       TEXT NOT NULL,
    sequence      INTEGER NOT NULL,
    added_at      REAL NOT NULL,
    plan_data     TEXT NOT NULL,
    final_status  TEXT NOT NULL,
    action_count  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (recording_id, plan_id),
    FOREIGN KEY (recording_id) REFERENCES recordings(recording_id)
);

CREATE INDEX IF NOT EXISTS idx_recorded_plans_rec_id ON recorded_plans (recording_id);
"""


class RecordingStore:
    """Async SQLite store for WorkflowRecordings."""

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = Path(db_path).expanduser()
        self._conn: aiosqlite.Connection | None = None

    async def init(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(str(self._db_path))
        await self._conn.executescript(_SCHEMA_SQL)
        await self._conn.commit()
        log.debug("recording_store_init", path=str(self._db_path))

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    async def save(self, recording: WorkflowRecording) -> None:
        """Insert or replace a recording header row."""
        assert self._conn is not None
        generated = json.dumps(recording.generated_plan) if recording.generated_plan else None
        await self._conn.execute(
            """INSERT OR REPLACE INTO recordings
               (recording_id, title, description, status, created_at, stopped_at, generated_plan)
               VALUES (?,?,?,?,?,?,?)""",
            (
                recording.recording_id, recording.title, recording.description,
                recording.status.value, recording.created_at, recording.stopped_at, generated,
            ),
        )
        await self._conn.commit()

    async def add_plan(self, recording_id: str, plan: RecordedPlan) -> None:
        """Append a RecordedPlan row."""
        assert self._conn is not None
        await self._conn.execute(
            """INSERT OR REPLACE INTO recorded_plans
               (recording_id, plan_id, sequence, added_at, plan_data, final_status, action_count)
               VALUES (?,?,?,?,?,?,?)""",
            (
                recording_id, plan.plan_id, plan.sequence, plan.added_at,
                json.dumps(plan.plan_data), plan.final_status, plan.action_count,
            ),
        )
        await self._conn.commit()

    async def update_status(
        self,
        recording_id: str,
        status: RecordingStatus,
        stopped_at: float | None = None,
        generated_plan: dict[str, Any] | None = None,
    ) -> None:
        assert self._conn is not None
        generated = json.dumps(generated_plan) if generated_plan else None
        await self._conn.execute(
            "UPDATE recordings SET status=?, stopped_at=?, generated_plan=? WHERE recording_id=?",
            (status.value, stopped_at, generated, recording_id),
        )
        await self._conn.commit()

    async def delete(self, recording_id: str) -> bool:
        """Delete a recording and all its captured plans. Returns True if found."""
        assert self._conn is not None
        cursor = await self._conn.execute(
            "DELETE FROM recordings WHERE recording_id=?", (recording_id,)
        )
        await self._conn.execute(
            "DELETE FROM recorded_plans WHERE recording_id=?", (recording_id,)
        )
        await self._conn.commit()
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    async def get(self, recording_id: str) -> WorkflowRecording | None:
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT recording_id, title, description, status, created_at, stopped_at, generated_plan "
            "FROM recordings WHERE recording_id=?",
            (recording_id,),
        ) as cursor:
            row = await cursor.fetchone()

        if row is None:
            return None

        recording = WorkflowRecording(
            recording_id=row[0],
            title=row[1],
            description=row[2],
            status=RecordingStatus(row[3]),
            created_at=row[4],
            stopped_at=row[5],
            generated_plan=json.loads(row[6]) if row[6] else None,
        )

        async with self._conn.execute(
            "SELECT plan_id, sequence, added_at, plan_data, final_status, action_count "
            "FROM recorded_plans WHERE recording_id=? ORDER BY sequence",
            (recording_id,),
        ) as cursor:
            async for prow in cursor:
                recording.plans.append(
                    RecordedPlan(
                        plan_id=prow[0],
                        sequence=prow[1],
                        added_at=prow[2],
                        plan_data=json.loads(prow[3]),
                        final_status=prow[4],
                        action_count=prow[5],
                    )
                )

        return recording

    async def list_all(self, status: RecordingStatus | None = None) -> list[WorkflowRecording]:
        assert self._conn is not None
        if status:
            sql = (
                "SELECT recording_id, title, description, status, created_at, stopped_at, generated_plan "
                "FROM recordings WHERE status=? ORDER BY created_at DESC"
            )
            params: tuple[Any, ...] = (status.value,)
        else:
            sql = (
                "SELECT recording_id, title, description, status, created_at, stopped_at, generated_plan "
                "FROM recordings ORDER BY created_at DESC"
            )
            params = ()

        results: list[WorkflowRecording] = []
        async with self._conn.execute(sql, params) as cursor:
            async for row in cursor:
                results.append(
                    WorkflowRecording(
                        recording_id=row[0],
                        title=row[1],
                        description=row[2],
                        status=RecordingStatus(row[3]),
                        created_at=row[4],
                        stopped_at=row[5],
                        generated_plan=json.loads(row[6]) if row[6] else None,
                    )
                )
        return results
