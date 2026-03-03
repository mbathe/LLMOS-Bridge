# Recording Module -- Integration Guide

## Cross-Module Workflows

### Record and Replay a Multi-Step Workflow

Start a recording session, execute a sequence of actions across modules,
then stop to generate a reusable replay plan.

```yaml
actions:
  - id: start-rec
    module: recording
    action: start_recording
    params:
      title: "Config update workflow"
      description: "Record steps to update app config and restart"

  - id: backup-config
    module: filesystem
    action: copy_file
    depends_on: [start-rec]
    params:
      source: /etc/myapp/config.yaml
      destination: /etc/myapp/config.yaml.bak

  - id: write-new-config
    module: filesystem
    action: write_file
    depends_on: [backup-config]
    params:
      path: /etc/myapp/config.yaml
      content: "new_setting: true\n"

  - id: restart-service
    module: os_exec
    action: run_command
    depends_on: [write-new-config]
    params:
      command: ["systemctl", "restart", "myapp"]
      timeout: 30

  - id: stop-rec
    module: recording
    action: stop_recording
    depends_on: [restart-service]
    params:
      recording_id: "{{result.start-rec.recording_id}}"
```

### Scheduled Recording with Triggers

Use a trigger to automatically start recording sessions at specific times,
capturing real workflows for analysis.

```yaml
actions:
  - id: schedule-capture
    module: triggers
    action: register_trigger
    params:
      name: "morning-workflow-capture"
      condition:
        type: temporal
        params:
          cron: "0 9 * * 1-5"
      plan_template:
        plan_id: "auto-record"
        protocol_version: "2.0"
        description: "Auto-start recording for morning workflow"
        actions:
          - id: start-rec
            module: recording
            action: start_recording
            params:
              title: "Morning workflow {{trigger.fire_id}}"
      priority: background
```

### Visual Workflow Recording with Perception

Record a GUI automation workflow that uses perception to identify
and interact with screen elements.

```yaml
actions:
  - id: start-rec
    module: recording
    action: start_recording
    params:
      title: "GUI form-fill workflow"
      description: "Record visual interaction with web form"

  - id: capture-screen
    module: vision
    action: capture_and_parse
    depends_on: [start-rec]
    params:
      monitor: 0

  - id: find-submit
    module: vision
    action: find_element
    depends_on: [capture-screen]
    params:
      query: "Submit"
      element_type: button

  - id: click-submit
    module: computer_control
    action: click
    depends_on: [find-submit]
    params:
      x: "{{result.find-submit.pixel_x}}"
      y: "{{result.find-submit.pixel_y}}"

  - id: stop-rec
    module: recording
    action: stop_recording
    depends_on: [click-submit]
    params:
      recording_id: "{{result.start-rec.recording_id}}"
```

### Recording Management Pipeline

List, inspect, and clean up old recordings.

```yaml
actions:
  - id: list-stopped
    module: recording
    action: list_recordings
    params:
      status: stopped

  - id: inspect-latest
    module: recording
    action: get_recording
    depends_on: [list-stopped]
    params:
      recording_id: "rec_target"

  - id: regenerate-replay
    module: recording
    action: generate_replay_plan
    depends_on: [inspect-latest]
    params:
      recording_id: "rec_target"

  - id: cleanup-old
    module: recording
    action: delete_recording
    requires_approval: true
    params:
      recording_id: "rec_old_123"
```
