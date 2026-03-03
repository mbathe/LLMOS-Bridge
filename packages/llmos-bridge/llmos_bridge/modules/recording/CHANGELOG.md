# Changelog -- Recording Module

## [1.0.0] -- 2026-02-15

### Added
- Initial release with 6 actions (Shadow Recorder Phase A).
- `start_recording` -- Begin a named recording session that captures all subsequent plan executions.
- `stop_recording` -- Stop the active session and auto-generate a replay IML plan.
- `list_recordings` -- List all recordings with optional status filter (active/stopped).
- `get_recording` -- Retrieve full recording details including captured plans and replay plan.
- `generate_replay_plan` -- Regenerate the replay plan for a previously stopped recording.
- `delete_recording` -- Permanently delete a recording and its captured plan data.
- Dependency injection via `set_recorder()` for WorkflowRecorder integration.
- Security decorators: `@audit_trail("standard")` on start/stop, `@audit_trail("detailed")` on replay generation.
