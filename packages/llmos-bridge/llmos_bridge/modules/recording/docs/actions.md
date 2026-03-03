# Recording Module -- Action Reference

## start_recording

Start a new named recording session. All subsequent plan executions will be
captured until `stop_recording` is called.

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `title` | string | Yes | -- | Human-readable name for this recording session |
| `description` | string | No | `""` | Optional longer description |

### Returns

```json
{
  "recording_id": "string",
  "title": "string",
  "status": "active",
  "started_at": "string"
}
```

### Examples

```yaml
actions:
  - id: start-rec
    module: recording
    action: start_recording
    params:
      title: "Deploy workflow"
      description: "Full production deploy sequence"
```

### Security

- Permission: `local_worker`
- Risk Level: Low
- Audit trail: standard

---

## stop_recording

Stop the active recording session and generate a single replay IML plan
that re-runs the entire captured workflow.

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `recording_id` | string | Yes | -- | ID of the recording to stop |

### Returns

```json
{
  "recording_id": "string",
  "title": "string",
  "status": "stopped",
  "started_at": "string",
  "stopped_at": "string",
  "captured_plans": [],
  "replay_plan": {},
  "message": "Recording stopped. Replay plan generated."
}
```

### Examples

```yaml
actions:
  - id: stop-rec
    module: recording
    action: stop_recording
    params:
      recording_id: "rec_abc123"
```

### Security

- Permission: `local_worker`
- Risk Level: Low
- Audit trail: standard

---

## list_recordings

List all workflow recordings with optional status filter.

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `status` | string | No | `null` | Filter by status: `active` or `stopped` |

### Returns

```json
{
  "recordings": [
    {
      "recording_id": "string",
      "title": "string",
      "status": "string",
      "started_at": "string"
    }
  ],
  "count": 0
}
```

### Examples

```yaml
actions:
  - id: list-active
    module: recording
    action: list_recordings
    params:
      status: active

  - id: list-all
    module: recording
    action: list_recordings
    params: {}
```

### Security

- Permission: `readonly`
- Risk Level: Low

---

## get_recording

Retrieve a recording including its captured plans and generated replay plan.

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `recording_id` | string | Yes | -- | ID of the recording to retrieve |

### Returns

```json
{
  "recording_id": "string",
  "title": "string",
  "description": "string",
  "status": "string",
  "started_at": "string",
  "stopped_at": "string | null",
  "captured_plans": [],
  "replay_plan": "object | null"
}
```

### Examples

```yaml
actions:
  - id: inspect-recording
    module: recording
    action: get_recording
    params:
      recording_id: "rec_abc123"
```

### Security

- Permission: `readonly`
- Risk Level: Low

---

## generate_replay_plan

Regenerate the replay IML plan for a stopped recording. Useful if the
original replay plan needs to be refreshed after template changes.

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `recording_id` | string | Yes | -- | ID of the stopped recording to regenerate replay for |

### Returns

```json
{
  "recording_id": "string",
  "replay_plan": {}
}
```

### Examples

```yaml
actions:
  - id: regen-replay
    module: recording
    action: generate_replay_plan
    params:
      recording_id: "rec_abc123"
```

### Security

- Permission: `local_worker`
- Risk Level: Low
- Audit trail: detailed

---

## delete_recording

Permanently delete a recording and all its captured plans.

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `recording_id` | string | Yes | -- | ID of the recording to permanently delete |

### Returns

```json
{
  "recording_id": "string",
  "deleted": true
}
```

### Examples

```yaml
actions:
  - id: cleanup-recording
    module: recording
    action: delete_recording
    params:
      recording_id: "rec_abc123"
```

### Security

- Permission: `power_user`
- Risk Level: Medium
