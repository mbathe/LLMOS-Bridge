# OS/Exec Module -- Action Reference

## run_command

Run an external command. Command must be a list, never a shell string. Returns
stdout, stderr, and return code.

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `command` | array | Yes | -- | Command and arguments as a list, e.g. `["git", "status"]` |
| `working_directory` | string | No | -- | Working directory for the command |
| `env` | object | No | -- | Additional environment variables (merged with current env) |
| `timeout` | integer | No | `30` | Timeout in seconds (1-600) |
| `capture_output` | boolean | No | `true` | Capture stdout and stderr |
| `stdin` | string | No | -- | Optional data to pipe to stdin |

### Returns

```json
{
  "command": ["string"],
  "return_code": "integer",
  "stdout": "string",
  "stderr": "string",
  "success": "boolean"
}
```

### Examples

```yaml
actions:
  - id: git-status
    module: os_exec
    action: run_command
    params:
      command: ["git", "status", "--short"]
      working_directory: /home/user/project

  - id: run-with-env
    module: os_exec
    action: run_command
    params:
      command: ["python", "script.py"]
      env:
        DEBUG: "1"
        API_KEY: "{{env.MY_API_KEY}}"
      timeout: 120
```

### Security

- Permission: `os.process.execute`
- Risk Level: Medium
- Rate limited: 30 calls/minute
- Audit trail: detailed
- Marked as sensitive action

---

## list_processes

List running processes with optional name filtering.

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `name_filter` | string | No | -- | Filter processes whose name contains this string |
| `include_children` | boolean | No | `false` | Include child processes |

### Returns

```json
{
  "processes": [
    {
      "pid": "integer",
      "name": "string",
      "status": "string",
      "cpu_percent": "float",
      "memory_mb": "float"
    }
  ],
  "count": "integer"
}
```

### Examples

```yaml
actions:
  - id: find-python-procs
    module: os_exec
    action: list_processes
    params:
      name_filter: python
```

### Security

- Permission: readonly
- Risk Level: Low

---

## kill_process

Send a signal (SIGTERM or SIGKILL) to a process by PID.

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `pid` | integer | Yes | -- | PID of the process to kill (must be >= 1) |
| `signal` | string | No | `"SIGTERM"` | Signal to send: `SIGTERM` or `SIGKILL` |

### Returns

```json
{
  "pid": "integer",
  "signal": "string",
  "success": "boolean"
}
```

### Examples

```yaml
actions:
  - id: stop-server
    module: os_exec
    action: kill_process
    params:
      pid: 12345
      signal: SIGTERM
```

### Security

- Permission: `os.process.kill`
- Risk Level: High (irreversible)
- Audit trail: detailed
- Marked as sensitive action

---

## get_process_info

Get detailed information about a running process by PID.

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `pid` | integer | Yes | -- | PID of the process (must be >= 1) |

### Returns

```json
{
  "pid": "integer",
  "name": "string",
  "status": "string",
  "cpu_percent": "float",
  "memory_mb": "float",
  "cmdline": ["string"],
  "cwd": "string",
  "created": "float"
}
```

### Examples

```yaml
actions:
  - id: inspect-process
    module: os_exec
    action: get_process_info
    params:
      pid: 1234
```

### Security

- Permission: readonly
- Risk Level: Low

---

## open_application

Launch an application as a detached process.

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `application` | string | Yes | -- | Application name or full path to the executable |
| `arguments` | array | No | `[]` | Command-line arguments to pass |
| `working_directory` | string | No | -- | Working directory for the application |

### Returns

```json
{
  "application": "string",
  "pid": "integer"
}
```

### Examples

```yaml
actions:
  - id: launch-editor
    module: os_exec
    action: open_application
    params:
      application: code
      arguments: ["/home/user/project"]
```

### Security

- Permission: `os.process.execute`
- Risk Level: Medium
- Audit trail: standard

---

## close_application

Close all processes matching an application name.

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `application_name` | string | Yes | -- | Name of the application window or process to close |
| `force` | boolean | No | `false` | If true, forcibly kill the process (SIGKILL instead of SIGTERM) |

### Returns

```json
{
  "application": "string",
  "closed_pids": ["integer"]
}
```

### Examples

```yaml
actions:
  - id: close-browser
    module: os_exec
    action: close_application
    params:
      application_name: firefox
      force: false
```

### Security

- Permission: `os.process.kill`
- Risk Level: Medium
- Marked as sensitive action

---

## set_env_var

Set an environment variable in the current process scope.

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `name` | string | Yes | -- | Environment variable name |
| `value` | string | Yes | -- | Value to set |
| `scope` | string | No | `"process"` | Scope of the variable (currently only `process` is supported) |

### Returns

```json
{
  "name": "string",
  "scope": "string"
}
```

### Examples

```yaml
actions:
  - id: set-debug
    module: os_exec
    action: set_env_var
    params:
      name: DEBUG
      value: "1"
```

### Security

- Permission: local_worker
- Risk Level: Medium

---

## get_env_var

Read the value of an environment variable.

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `name` | string | Yes | -- | Environment variable name to read |

### Returns

```json
{
  "name": "string",
  "value": "string | null",
  "exists": "boolean"
}
```

### Examples

```yaml
actions:
  - id: read-path
    module: os_exec
    action: get_env_var
    params:
      name: PATH
```

### Security

- Permission: readonly
- Risk Level: Low

---

## get_system_info

Get CPU, memory, disk, and OS information.

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `include` | array | No | `["cpu", "memory", "disk", "os"]` | Categories to include: `cpu`, `memory`, `disk`, `network`, `os` |

### Returns

```json
{
  "os": {
    "system": "string",
    "release": "string",
    "version": "string",
    "machine": "string",
    "python_version": "string"
  },
  "cpu": {
    "count": "integer",
    "percent": "float"
  },
  "memory": {
    "total_gb": "float",
    "available_gb": "float",
    "percent_used": "float"
  },
  "disk": {
    "total_gb": "float",
    "free_gb": "float",
    "percent_used": "float"
  }
}
```

### Examples

```yaml
actions:
  - id: health-check
    module: os_exec
    action: get_system_info
    params:
      include: ["cpu", "memory", "disk"]
```

### Security

- Permission: readonly
- Risk Level: Low
