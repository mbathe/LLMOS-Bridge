"""Typed parameter models for the 'recording' module actions."""

from __future__ import annotations

from pydantic import BaseModel, Field


class StartRecordingParams(BaseModel):
    title: str = Field(description="Human-readable name for this recording session")
    description: str = Field(default="", description="Optional longer description")


class StopRecordingParams(BaseModel):
    recording_id: str = Field(description="ID of the recording to stop")


class ListRecordingsParams(BaseModel):
    status: str | None = Field(
        default=None,
        description="Filter by status: 'active' or 'stopped'",
    )


class GetRecordingParams(BaseModel):
    recording_id: str = Field(description="ID of the recording to retrieve")


class GenerateReplayPlanParams(BaseModel):
    recording_id: str = Field(description="ID of the stopped recording to regenerate replay for")


class DeleteRecordingParams(BaseModel):
    recording_id: str = Field(description="ID of the recording to permanently delete")


PARAMS_MAP: dict[str, type[BaseModel]] = {
    "start_recording": StartRecordingParams,
    "stop_recording": StopRecordingParams,
    "list_recordings": ListRecordingsParams,
    "get_recording": GetRecordingParams,
    "generate_replay_plan": GenerateReplayPlanParams,
    "delete_recording": DeleteRecordingParams,
}
