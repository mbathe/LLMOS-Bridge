# Recording Module

Record sequences of plan executions into named sessions for later replay.

## Overview

The Recording module (Shadow Recorder Phase A) provides LLMOS-native workflow
recording and replay capabilities. An LLM can start a recording session, execute
a series of plans, then stop the recording to produce a single replay IML plan
that re-runs the entire captured workflow.

This enables:
- **Workflow capture** -- Record complex multi-plan sequences for training data.
- **Replay** -- Re-execute recorded workflows with a single IML plan submission.
- **Audit** -- Inspect exactly which plans were executed in a session.
- **Automation** -- Combine with triggers to record workflows on a schedule.

The module delegates all recording lifecycle management to the injected
WorkflowRecorder instance. It does not perform OS operations directly.

## Actions

| Action | Description | Risk | Permission |
|--------|-------------|------|------------|
| `start_recording` | Start a new named recording session | Low | `local_worker` |
| `stop_recording` | Stop the active session and generate a replay plan | Low | `local_worker` |
| `list_recordings` | List all recordings with optional status filter | Low | `readonly` |
| `get_recording` | Retrieve a recording with captured plans and replay plan | Low | `readonly` |
| `generate_replay_plan` | Regenerate the replay plan for a stopped recording | Low | `local_worker` |
| `delete_recording` | Permanently delete a recording and all captured plans | Medium | `power_user` |

## Quick Start

```yaml
actions:
  - id: start-capture
    module: recording
    action: start_recording
    params:
      title: "Deploy workflow"
      description: "Record the full deploy sequence for replay"

  # ... execute other plans ...

  - id: stop-capture
    module: recording
    action: stop_recording
    depends_on: [start-capture]
    params:
      recording_id: "{{result.start-capture.recording_id}}"
```

## Requirements

No external dependencies required. The WorkflowRecorder must be enabled in the
LLMOS Bridge configuration (`recording.enabled = true`). The recorder is injected
into the module at startup via `set_recorder()`.

## Configuration

The WorkflowRecorder is configured via the `recording` section in the LLMOS Bridge
settings. The module itself requires no separate configuration.

## Platform Support

| Platform | Status |
|----------|--------|
| Linux | Supported |
| macOS | Supported |
| Windows | Supported |

## Related Modules

- **triggers** -- Schedule automated recording sessions via temporal triggers.
- **filesystem** -- Recorded workflows often include file operations.
- **os_exec** -- Command execution steps are captured in recordings.
