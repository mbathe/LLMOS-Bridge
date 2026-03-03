# Triggers Module -- Action Reference

## register_trigger

Register a new trigger that fires an IML plan when its condition is met.

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `name` | string | Yes | -- | Human-readable trigger name (1-128 chars) |
| `description` | string | No | `""` | Optional description |
| `condition` | object | Yes | -- | Trigger condition: `{type, params}` |
| `condition.type` | string | Yes | -- | One of: `temporal`, `filesystem`, `process`, `resource`, `application`, `iot`, `composite` |
| `condition.params` | object | No | `{}` | Type-specific condition parameters |
| `plan_template` | object | Yes | -- | IML plan JSON template to execute when the trigger fires |
| `plan_id_prefix` | string | No | `"trigger"` | Prefix for auto-generated plan IDs |
| `priority` | string | No | `"normal"` | Execution priority: `background`, `low`, `normal`, `high`, `critical` |
| `min_interval_seconds` | number | No | `0.0` | Minimum seconds between fires (0 = no limit) |
| `max_fires_per_hour` | integer | No | `0` | Max fires per hour (0 = unlimited) |
| `conflict_policy` | string | No | `"queue"` | Action when another plan from this trigger is running: `queue`, `preempt`, `reject` |
| `resource_lock` | string | No | `null` | Optional shared resource name to prevent concurrent execution |
| `enabled` | boolean | No | `true` | Activate immediately after registration |
| `tags` | array | No | `[]` | Optional tags for filtering |
| `expires_at` | number | No | `null` | Auto-delete after this Unix timestamp |
| `max_chain_depth` | integer | No | `5` | Maximum trigger chain depth (1-20, loop protection) |

### Returns

```json
{
  "trigger_id": "string",
  "name": "string",
  "state": "string",
  "enabled": true
}
```

### Examples

```yaml
actions:
  - id: watch-uploads
    module: triggers
    action: register_trigger
    params:
      name: "csv-processor"
      condition:
        type: filesystem
        params:
          path: /var/incoming
          events: [created]
          pattern: "*.csv"
      plan_template:
        plan_id: "process-csv"
        protocol_version: "2.0"
        description: "Auto-process uploaded CSV"
        actions:
          - id: read-file
            module: filesystem
            action: read_file
            params:
              path: "{{trigger.event.path}}"
      min_interval_seconds: 10.0
      max_fires_per_hour: 30

  - id: daily-backup
    module: triggers
    action: register_trigger
    params:
      name: "nightly-backup"
      condition:
        type: temporal
        params:
          cron: "0 2 * * *"
      plan_template:
        plan_id: "backup"
        protocol_version: "2.0"
        description: "Nightly backup"
        actions:
          - id: archive
            module: filesystem
            action: create_archive
            params:
              source: /home/user/data
              destination: /backups/nightly.tar.gz
              format: tar.gz
      priority: background
```

### Security

- Permission: `process.execute`
- Risk Level: High
- Audit trail: standard

---

## activate_trigger

Enable and arm an existing trigger.

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `trigger_id` | string | Yes | -- | ID of the trigger to activate |

### Returns

```json
{
  "trigger_id": "string",
  "state": "active"
}
```

### Examples

```yaml
actions:
  - id: enable-watcher
    module: triggers
    action: activate_trigger
    params:
      trigger_id: "trg_abc123"
```

### Security

- Permission: `power_user`
- Risk Level: Medium
- Audit trail: standard

---

## deactivate_trigger

Pause a trigger without deleting it.

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `trigger_id` | string | Yes | -- | ID of the trigger to deactivate |

### Returns

```json
{
  "trigger_id": "string",
  "state": "inactive"
}
```

### Examples

```yaml
actions:
  - id: pause-watcher
    module: triggers
    action: deactivate_trigger
    params:
      trigger_id: "trg_abc123"
```

### Security

- Permission: `power_user`
- Risk Level: Medium
- Audit trail: standard

---

## delete_trigger

Permanently remove a trigger.

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `trigger_id` | string | Yes | -- | ID of the trigger to delete |

### Returns

```json
{
  "trigger_id": "string",
  "deleted": true
}
```

### Examples

```yaml
actions:
  - id: remove-watcher
    module: triggers
    action: delete_trigger
    params:
      trigger_id: "trg_abc123"
```

### Security

- Permission: `process.execute`
- Risk Level: High
- Marked as sensitive action (MEDIUM risk)
- Audit trail: standard

---

## list_triggers

List all registered triggers with optional state/type filters.

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `state` | string | No | `null` | Filter by lifecycle state: `registered`, `inactive`, `active`, `watching`, `fired`, `throttled`, `failed` |
| `trigger_type` | string | No | `null` | Filter by type: `temporal`, `filesystem`, `process`, `resource`, `application`, `iot`, `composite` |
| `tags` | array | No | `[]` | Filter by tags (all specified tags must match) |
| `created_by` | string | No | `null` | Filter by creator: `user`, `llm`, `system` |
| `include_health` | boolean | No | `true` | Include health metrics in the response |

### Returns

```json
{
  "triggers": [
    {
      "trigger_id": "string",
      "name": "string",
      "type": "string",
      "state": "string",
      "priority": "string",
      "enabled": true,
      "tags": [],
      "created_by": "string",
      "created_at": "string",
      "health": {
        "fire_count": 0,
        "fail_count": 0,
        "last_fired_at": "string | null",
        "avg_latency_ms": 0.0
      }
    }
  ],
  "count": 0
}
```

### Examples

```yaml
actions:
  - id: list-active-triggers
    module: triggers
    action: list_triggers
    params:
      state: active
      include_health: true

  - id: list-fs-triggers
    module: triggers
    action: list_triggers
    params:
      trigger_type: filesystem
      tags: ["production"]
```

### Security

- Permission: `local_worker`
- Risk Level: Low

---

## get_trigger

Retrieve a single trigger by ID with full details and health metrics.

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `trigger_id` | string | Yes | -- | ID of the trigger to retrieve |

### Returns

```json
{
  "trigger_id": "string",
  "name": "string",
  "description": "string",
  "type": "string",
  "condition_params": {},
  "state": "string",
  "priority": "string",
  "enabled": true,
  "min_interval_seconds": 0.0,
  "max_fires_per_hour": 0,
  "conflict_policy": "queue",
  "resource_lock": "string | null",
  "tags": [],
  "created_by": "string",
  "created_at": "string",
  "health": {
    "fire_count": 0,
    "fail_count": 0,
    "throttle_count": 0,
    "last_fired_at": "string | null",
    "last_error": "string | null",
    "avg_latency_ms": 0.0
  }
}
```

### Examples

```yaml
actions:
  - id: inspect-trigger
    module: triggers
    action: get_trigger
    params:
      trigger_id: "trg_abc123"
```

### Security

- Permission: `local_worker`
- Risk Level: Low
