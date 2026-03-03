# Triggers Module -- Integration Guide

## Cross-Module Workflows

### File Watcher with Processing Pipeline

Create a trigger that watches for new files and processes them automatically
using the filesystem module.

```yaml
actions:
  - id: setup-csv-watcher
    module: triggers
    action: register_trigger
    params:
      name: "csv-auto-processor"
      description: "Automatically process CSV files dropped into /var/incoming"
      condition:
        type: filesystem
        params:
          path: /var/incoming
          events: [created]
          pattern: "*.csv"
      plan_template:
        plan_id: "process-csv-{{trigger.fire_id}}"
        protocol_version: "2.0"
        description: "Process new CSV upload"
        actions:
          - id: read-csv
            module: filesystem
            action: read_file
            params:
              path: "{{trigger.event.path}}"
          - id: archive-csv
            module: filesystem
            action: move_file
            depends_on: [read-csv]
            params:
              source: "{{trigger.event.path}}"
              destination: "/var/archive/{{trigger.event.filename}}"
      min_interval_seconds: 5.0
      max_fires_per_hour: 60
```

### Trigger Chaining -- Build and Deploy

A build trigger fires a plan that, on success, registers a deploy trigger.
This creates a reactive CI/CD pipeline entirely within IML.

```yaml
actions:
  - id: create-build-trigger
    module: triggers
    action: register_trigger
    params:
      name: "git-push-build"
      condition:
        type: filesystem
        params:
          path: /home/user/project/.git/refs/heads/main
          events: [modified]
      plan_template:
        plan_id: "build-{{trigger.fire_id}}"
        protocol_version: "2.0"
        description: "Build on git push"
        actions:
          - id: run-build
            module: os_exec
            action: run_command
            params:
              command: ["make", "build"]
              working_directory: /home/user/project
          - id: register-deploy
            module: triggers
            action: register_trigger
            depends_on: [run-build]
            params:
              name: "deploy-after-build"
              condition:
                type: temporal
                params:
                  delay_seconds: 30
              plan_template:
                plan_id: "deploy"
                protocol_version: "2.0"
                description: "Deploy built artifact"
                actions:
                  - id: deploy
                    module: os_exec
                    action: run_command
                    params:
                      command: ["./deploy.sh"]
                      working_directory: /home/user/project
      tags: ["ci", "build"]

  - id: activate-build-trigger
    module: triggers
    action: activate_trigger
    depends_on: [create-build-trigger]
    params:
      trigger_id: "{{result.create-build-trigger.trigger_id}}"
```

### Scheduled Recording Sessions

Combine triggers with the recording module to automatically record
workflows at specific times.

```yaml
actions:
  - id: schedule-recording
    module: triggers
    action: register_trigger
    params:
      name: "morning-workflow-capture"
      condition:
        type: temporal
        params:
          cron: "0 9 * * 1-5"
      plan_template:
        plan_id: "morning-record"
        protocol_version: "2.0"
        description: "Record morning workflow for training data"
        actions:
          - id: start-rec
            module: recording
            action: start_recording
            params:
              title: "Morning workflow capture"
              description: "Automated capture for workflow analysis"
      priority: background
      tags: ["recording", "scheduled"]
```

### Resource Monitoring with Window Context

Use resource triggers with the window tracker to capture full context
when system resources spike.

```yaml
actions:
  - id: cpu-spike-monitor
    module: triggers
    action: register_trigger
    params:
      name: "cpu-spike-context"
      condition:
        type: resource
        params:
          metric: cpu_percent
          threshold: 90
          duration_seconds: 30
      plan_template:
        plan_id: "cpu-investigation"
        protocol_version: "2.0"
        description: "Investigate CPU spike"
        actions:
          - id: get-windows
            module: window_tracker
            action: list_windows
            params: {}
          - id: get-processes
            module: os_exec
            action: run_command
            params:
              command: ["ps", "aux", "--sort=-%cpu"]
              timeout: 10
      min_interval_seconds: 300
      max_fires_per_hour: 4
      tags: ["monitoring", "performance"]
```

### Trigger Lifecycle Management

List, inspect, and clean up triggers as part of a maintenance plan.

```yaml
actions:
  - id: list-all
    module: triggers
    action: list_triggers
    params:
      include_health: true

  - id: check-failing
    module: triggers
    action: list_triggers
    params:
      state: failed

  - id: inspect-trigger
    module: triggers
    action: get_trigger
    params:
      trigger_id: "trg_abc123"

  - id: disable-broken
    module: triggers
    action: deactivate_trigger
    depends_on: [inspect-trigger]
    params:
      trigger_id: "trg_abc123"
```
