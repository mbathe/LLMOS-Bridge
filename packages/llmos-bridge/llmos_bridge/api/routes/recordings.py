"""API routes for Shadow Recorder (WorkflowRecorder).

REST endpoints::

    GET    /recordings                      — list all recordings
    POST   /recordings                      — start a new recording
    GET    /recordings/{recording_id}       — get recording details + plans
    POST   /recordings/{recording_id}/stop  — stop a recording
    GET    /recordings/{recording_id}/replay — get the generated replay plan
    DELETE /recordings/{recording_id}       — delete permanently

All endpoints require recording.enabled=true in config.
If not enabled they return 503 Service Unavailable.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from llmos_bridge.api.dependencies import AuthDep

router = APIRouter(prefix="/recordings", tags=["recordings"])


def _get_recorder(request: Request) -> Any:
    """FastAPI dependency — retrieve WorkflowRecorder from app state."""
    recorder = getattr(request.app.state, "workflow_recorder", None)
    if recorder is None:
        raise HTTPException(
            status_code=503,
            detail="WorkflowRecorder is not enabled. Set recording.enabled=true in config.",
        )
    return recorder


# ---------------------------------------------------------------------------
# Request models (inline — keeps this file self-contained like triggers.py)
# ---------------------------------------------------------------------------


class StartRecordingRequest(BaseModel):
    title: str
    description: str = ""


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("")
async def list_recordings(
    _auth: AuthDep,
    request: Request,
    status: str | None = None,
) -> dict[str, Any]:
    """List all recordings with optional status filter ('active' | 'stopped')."""
    recorder = _get_recorder(request)
    from llmos_bridge.recording.models import RecordingStatus
    st = RecordingStatus(status) if status else None
    recordings = await recorder._store.list_all(status=st)
    return {
        "recordings": [r.to_summary_dict() for r in recordings],
        "count": len(recordings),
        "active_recording_id": recorder.active_recording_id,
    }


@router.post("", status_code=201)
async def start_recording(
    body: StartRecordingRequest,
    _auth: AuthDep,
    request: Request,
) -> dict[str, Any]:
    """Start a new recording session."""
    recorder = _get_recorder(request)
    recording = await recorder.start(title=body.title, description=body.description)
    return {**recording.to_summary_dict(), "message": "Recording started"}


@router.get("/{recording_id}")
async def get_recording(
    recording_id: str,
    _auth: AuthDep,
    request: Request,
) -> dict[str, Any]:
    """Get recording details including all captured plans."""
    recorder = _get_recorder(request)
    recording = await recorder._store.get(recording_id)
    if recording is None:
        raise HTTPException(status_code=404, detail=f"Recording not found: {recording_id}")
    return recording.to_full_dict()


@router.post("/{recording_id}/stop")
async def stop_recording(
    recording_id: str,
    _auth: AuthDep,
    request: Request,
) -> dict[str, Any]:
    """Stop a recording and generate its replay plan."""
    recorder = _get_recorder(request)
    try:
        recording = await recorder.stop(recording_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Recording not found: {recording_id}")
    return {**recording.to_full_dict(), "message": "Recording stopped"}


@router.get("/{recording_id}/replay")
async def get_replay_plan(
    recording_id: str,
    _auth: AuthDep,
    request: Request,
) -> dict[str, Any]:
    """Get the generated replay IML plan for a stopped recording."""
    recorder = _get_recorder(request)
    recording = await recorder._store.get(recording_id)
    if recording is None:
        raise HTTPException(status_code=404, detail=f"Recording not found: {recording_id}")
    if recording.generated_plan is None:
        raise HTTPException(
            status_code=409,
            detail="Replay plan not yet available. Stop the recording first.",
        )
    return recording.generated_plan


@router.delete("/{recording_id}", status_code=204, response_model=None)
async def delete_recording(
    recording_id: str,
    _auth: AuthDep,
    request: Request,
) -> None:
    """Permanently delete a recording and all its captured plans."""
    recorder = _get_recorder(request)
    deleted = await recorder._store.delete(recording_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Recording not found: {recording_id}")
