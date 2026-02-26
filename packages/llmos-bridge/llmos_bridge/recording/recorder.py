"""Shadow Recorder — WorkflowRecorder.

Manages the lifecycle of recording sessions.  Plans that complete while a
session is active are automatically appended to it.
"""

from __future__ import annotations

import time
from typing import Any

from llmos_bridge.logging import get_logger
from llmos_bridge.recording.models import RecordedPlan, RecordingStatus, WorkflowRecording
from llmos_bridge.recording.replayer import WorkflowReplayer
from llmos_bridge.recording.store import RecordingStore

log = get_logger(__name__)


class WorkflowRecorder:
    """Single-asyncio-loop manager for WorkflowRecording sessions."""

    def __init__(self, store: RecordingStore) -> None:
        self._store = store
        self._replayer = WorkflowReplayer()
        self._active_recording_id: str | None = None

    @property
    def active_recording_id(self) -> str | None:
        return self._active_recording_id

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self, title: str, description: str = "") -> WorkflowRecording:
        """Start a new recording session.

        If a session is already active it is stopped first (auto-stop).
        """
        if self._active_recording_id:
            log.warning(
                "recording_auto_stopped_for_new_session",
                previous_id=self._active_recording_id,
            )
            try:
                await self.stop(self._active_recording_id)
            except KeyError:
                self._active_recording_id = None

        recording = WorkflowRecording.create(title=title, description=description)
        await self._store.save(recording)
        self._active_recording_id = recording.recording_id
        log.info("recording_started", recording_id=recording.recording_id, title=title)
        return recording

    async def stop(self, recording_id: str) -> WorkflowRecording:
        """Stop a recording and generate its replay plan.

        Idempotent: stopping an already-stopped recording returns it unchanged.
        """
        recording = await self._store.get(recording_id)
        if recording is None:
            raise KeyError(f"Recording not found: {recording_id}")

        if recording.status == RecordingStatus.STOPPED:
            return recording  # idempotent

        generated_plan = self._replayer.generate(recording)
        now = time.time()
        await self._store.update_status(
            recording_id,
            RecordingStatus.STOPPED,
            stopped_at=now,
            generated_plan=generated_plan,
        )

        if self._active_recording_id == recording_id:
            self._active_recording_id = None

        recording.status = RecordingStatus.STOPPED
        recording.stopped_at = now
        recording.generated_plan = generated_plan

        log.info(
            "recording_stopped",
            recording_id=recording_id,
            plan_count=len(recording.plans),
        )
        return recording

    # ------------------------------------------------------------------
    # Auto-tagging
    # ------------------------------------------------------------------

    async def add_plan(
        self,
        recording_id: str,
        plan_data: dict[str, Any],
        final_status: str,
        action_count: int,
    ) -> None:
        """Append a completed plan to an active recording.

        Silently no-ops if the recording is stopped or not found.
        """
        recording = await self._store.get(recording_id)
        if recording is None or recording.status == RecordingStatus.STOPPED:
            return

        recorded = RecordedPlan(
            plan_id=plan_data.get("plan_id", "unknown"),
            sequence=len(recording.plans) + 1,
            added_at=time.time(),
            plan_data=plan_data,
            final_status=final_status,
            action_count=action_count,
        )
        await self._store.add_plan(recording_id, recorded)
        log.debug(
            "plan_added_to_recording",
            recording_id=recording_id,
            plan_id=recorded.plan_id,
            sequence=recorded.sequence,
        )
