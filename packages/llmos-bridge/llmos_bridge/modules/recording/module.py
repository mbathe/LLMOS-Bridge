"""RecordingModule — IML module wrapping WorkflowRecorder for LLM access.

MODULE_ID: "recording"

Enables an LLM to start/stop recording sessions, inspect captured workflows,
and generate replay plans — all from within a running IML plan.

Dependency injection
--------------------
RecordingModule stores a reference to WorkflowRecorder injected via
``set_recorder()``.  server.py calls this after both registry and recorder
are initialised.

Actions
-------
start_recording       → WorkflowRecorder.start()
stop_recording        → WorkflowRecorder.stop()
list_recordings       → RecordingStore.list_all()
get_recording         → RecordingStore.get()
generate_replay_plan  → WorkflowReplayer.generate()
delete_recording      → RecordingStore.delete()
"""

from __future__ import annotations

from typing import Any

from llmos_bridge.modules.base import ActionResult, BaseModule
from llmos_bridge.modules.manifest import ActionSpec, ModuleManifest


class RecordingModule(BaseModule):
    """IML module providing LLM access to WorkflowRecorder."""

    MODULE_ID = "recording"
    VERSION = "1.0.0"
    SUPPORTED_PLATFORMS = ["linux", "darwin", "windows"]

    def __init__(self) -> None:
        self._recorder: Any | None = None  # injected via set_recorder()
        super().__init__()

    def set_recorder(self, recorder: Any) -> None:
        """Inject the WorkflowRecorder.  Called by server.py after startup."""
        self._recorder = recorder

    def _check_dependencies(self) -> None:
        pass  # No external dependencies at import time

    def get_manifest(self) -> ModuleManifest:
        return ModuleManifest(
            module_id=self.MODULE_ID,
            version=self.VERSION,
            description=(
                "Record sequences of plan executions into named sessions for later replay. "
                "Shadow Recorder Phase A — LLMOS-native recording."
            ),
            actions=[
                ActionSpec(
                    name="start_recording",
                    description=(
                        "Start a new named recording session. All subsequent plan "
                        "executions will be captured until stop_recording is called."
                    ),
                    permission_required="local_worker",
                    platforms=["all"],
                ),
                ActionSpec(
                    name="stop_recording",
                    description=(
                        "Stop the active recording session and generate a single "
                        "replay IML plan that re-runs the entire captured workflow."
                    ),
                    permission_required="local_worker",
                    platforms=["all"],
                ),
                ActionSpec(
                    name="list_recordings",
                    description="List all workflow recordings with optional status filter.",
                    permission_required="readonly",
                    platforms=["all"],
                ),
                ActionSpec(
                    name="get_recording",
                    description="Retrieve a recording including its captured plans and generated replay plan.",
                    permission_required="readonly",
                    platforms=["all"],
                ),
                ActionSpec(
                    name="generate_replay_plan",
                    description="Regenerate the replay IML plan for a stopped recording.",
                    permission_required="local_worker",
                    platforms=["all"],
                ),
                ActionSpec(
                    name="delete_recording",
                    description="Permanently delete a recording and all its captured plans.",
                    permission_required="power_user",
                    platforms=["all"],
                ),
            ],
        )

    # ------------------------------------------------------------------
    # Action implementations
    # ------------------------------------------------------------------

    async def _action_start_recording(self, params: dict[str, Any]) -> Any:
        if self._recorder is None:
            return ActionResult(success=False, error="WorkflowRecorder not available — recording not enabled")
        recording = await self._recorder.start(
            title=params["title"],
            description=params.get("description", ""),
        )
        return recording.to_summary_dict()

    async def _action_stop_recording(self, params: dict[str, Any]) -> Any:
        if self._recorder is None:
            return ActionResult(success=False, error="WorkflowRecorder not available")
        try:
            recording = await self._recorder.stop(params["recording_id"])
        except KeyError as exc:
            return ActionResult(success=False, error=str(exc))
        d = recording.to_full_dict()
        d["message"] = "Recording stopped. Replay plan generated."
        return d

    async def _action_list_recordings(self, params: dict[str, Any]) -> Any:
        if self._recorder is None:
            return ActionResult(success=False, error="WorkflowRecorder not available")
        status_filter = params.get("status")
        from llmos_bridge.recording.models import RecordingStatus
        status = RecordingStatus(status_filter) if status_filter else None
        recordings = await self._recorder._store.list_all(status=status)
        return {
            "recordings": [r.to_summary_dict() for r in recordings],
            "count": len(recordings),
        }

    async def _action_get_recording(self, params: dict[str, Any]) -> Any:
        if self._recorder is None:
            return ActionResult(success=False, error="WorkflowRecorder not available")
        recording = await self._recorder._store.get(params["recording_id"])
        if recording is None:
            return ActionResult(
                success=False,
                error=f"Recording not found: {params['recording_id']}",
            )
        return recording.to_full_dict()

    async def _action_generate_replay_plan(self, params: dict[str, Any]) -> Any:
        if self._recorder is None:
            return ActionResult(success=False, error="WorkflowRecorder not available")
        recording = await self._recorder._store.get(params["recording_id"])
        if recording is None:
            return ActionResult(
                success=False,
                error=f"Recording not found: {params['recording_id']}",
            )
        plan = self._recorder._replayer.generate(recording)
        return {"recording_id": recording.recording_id, "replay_plan": plan}

    async def _action_delete_recording(self, params: dict[str, Any]) -> Any:
        if self._recorder is None:
            return ActionResult(success=False, error="WorkflowRecorder not available")
        deleted = await self._recorder._store.delete(params["recording_id"])
        return {"recording_id": params["recording_id"], "deleted": deleted}
