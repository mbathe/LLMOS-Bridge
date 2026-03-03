# Triggers Module

Manage reactive triggers that fire IML plans in response to OS events, schedules, and conditions.

## Overview

The Triggers module provides an IML interface to the TriggerDaemon, enabling LLMs to
programmatically create, manage, and inspect event-driven triggers. When a trigger
condition is met (filesystem change, cron schedule, process event, resource threshold,
etc.), the TriggerDaemon automatically submits the associated IML plan for execution.

This enables **trigger chaining**: a plan running in response to trigger A can create
trigger B that will fire future plans automatically, building sophisticated reactive
workflows without manual intervention.

The module itself does not perform OS operations directly -- it delegates all
trigger lifecycle management to the injected TriggerDaemon instance.

## Actions

| Action | Description | Risk | Permission |
|--------|-------------|------|------------|
| `register_trigger` | Register a new trigger that fires an IML plan when its condition is met | High | `process.execute` |
| `activate_trigger` | Enable and arm an existing trigger | Medium | `power_user` |
| `deactivate_trigger` | Pause a trigger without deleting it | Medium | `power_user` |
| `delete_trigger` | Permanently remove a trigger | High | `process.execute` |
| `list_triggers` | List all registered triggers with optional state/type filters | Low | `local_worker` |
| `get_trigger` | Retrieve a single trigger by ID | Low | `local_worker` |

## Quick Start

```yaml
actions:
  - id: create-file-watcher
    module: triggers
    action: register_trigger
    params:
      name: "watch-uploads"
      description: "Process new CSV files in the uploads directory"
      condition:
        type: filesystem
        params:
          path: /var/incoming
          events: [created]
          pattern: "*.csv"
      plan_template:
        plan_id: "process-upload-{{trigger.fire_id}}"
        protocol_version: "2.0"
        description: "Process uploaded CSV"
        actions:
          - id: read-csv
            module: filesystem
            action: read_file
            params:
              path: "{{trigger.event.path}}"
      priority: normal
      min_interval_seconds: 5.0
```

## Trigger Types

| Type | Description |
|------|-------------|
| `temporal` | Cron expressions, intervals, one-shot timers |
| `filesystem` | File/directory creation, modification, deletion |
| `process` | Process start, stop, crash detection |
| `resource` | CPU, memory, disk threshold alerts |
| `application` | Application-specific events |
| `iot` | IoT device state changes |
| `composite` | Boolean combination of other trigger conditions |

## Requirements

No external dependencies required. The TriggerDaemon must be enabled in the LLMOS
Bridge configuration (`triggers.enabled = true`). The daemon is injected into the
module at startup via `set_daemon()`.

## Configuration

The TriggerDaemon is configured via the `triggers` section in the LLMOS Bridge
settings. The module itself requires no separate configuration.

## Platform Support

| Platform | Status |
|----------|--------|
| Linux | Supported |
| macOS | Supported |
| Windows | Supported |

## Related Modules

- **recording** -- Record trigger-initiated workflows for later replay.
- **filesystem** -- Filesystem events are a common trigger condition type.
- **os_exec** -- Process events can trigger plans via the `process` condition type.
