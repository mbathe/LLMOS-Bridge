# OS/Exec Module -- Integration Guide

## Cross-Module Workflows

### Build, Test, and Verify

Run a build command, execute tests, then verify the output artifact exists and
capture its checksum.

```yaml
actions:
  - id: build
    module: os_exec
    action: run_command
    params:
      command: ["make", "build"]
      working_directory: /home/user/project
      timeout: 300

  - id: test
    module: os_exec
    action: run_command
    depends_on: [build]
    params:
      command: ["make", "test"]
      working_directory: /home/user/project
      timeout: 120

  - id: verify-artifact
    module: filesystem
    action: get_file_info
    depends_on: [build]
    params:
      path: /home/user/project/dist/app.bin

  - id: checksum
    module: filesystem
    action: compute_checksum
    depends_on: [verify-artifact]
    params:
      path: /home/user/project/dist/app.bin
      algorithm: sha256
```

### Application Lifecycle Management

Launch an application, wait for it to initialize, then interact with it via
the GUI module.

```yaml
actions:
  - id: launch-app
    module: os_exec
    action: open_application
    params:
      application: firefox
      arguments: ["https://example.com"]

  - id: wait-for-startup
    module: os_exec
    action: run_command
    depends_on: [launch-app]
    params:
      command: ["sleep", "3"]
      capture_output: false

  - id: find-window
    module: window_tracker
    action: find_window
    depends_on: [wait-for-startup]
    params:
      title_pattern: "Example"
```

### Environment Setup and Command Execution

Set up environment variables before running a command that depends on them.

```yaml
actions:
  - id: set-api-key
    module: os_exec
    action: set_env_var
    params:
      name: API_KEY
      value: "{{memory.api_key}}"

  - id: set-env
    module: os_exec
    action: set_env_var
    params:
      name: NODE_ENV
      value: production

  - id: run-deploy
    module: os_exec
    action: run_command
    depends_on: [set-api-key, set-env]
    params:
      command: ["npm", "run", "deploy"]
      working_directory: /home/user/app
      timeout: 300
```

### System Health Monitoring

Query system resources and write a health report to disk.

```yaml
actions:
  - id: get-health
    module: os_exec
    action: get_system_info
    params:
      include: ["cpu", "memory", "disk", "os"]

  - id: list-heavy-procs
    module: os_exec
    action: list_processes
    params:
      name_filter: ""

  - id: write-report
    module: filesystem
    action: write_file
    depends_on: [get-health, list-heavy-procs]
    params:
      path: /var/log/health-report.json
      content: "{{result.get-health}}"
      create_dirs: true
```

### Process Cleanup Workflow

Find runaway processes by name, confirm with the user, then terminate them.

```yaml
actions:
  - id: find-processes
    module: os_exec
    action: list_processes
    params:
      name_filter: "worker"

  - id: kill-worker
    module: os_exec
    action: kill_process
    depends_on: [find-processes]
    requires_approval: true
    params:
      pid: "{{result.find-processes.processes[0].pid}}"
      signal: SIGTERM
```
