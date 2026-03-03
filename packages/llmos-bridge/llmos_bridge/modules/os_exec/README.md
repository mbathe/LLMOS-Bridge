# OS/Exec Module

Execute system commands, manage processes, and query system information.

## Overview

The OS/Exec module provides safe command execution and process management for IML
plans. Commands are always passed as lists (never shell strings) to prevent
shell-injection vulnerabilities. The module supports process listing, killing,
application lifecycle management, environment variable access, and system
resource monitoring. The PermissionGuard blocks this module entirely for READONLY
security profiles.

## Actions

| Action | Description | Risk | Permission |
|--------|-------------|------|------------|
| `run_command` | Run an external command with stdout/stderr capture | Medium | `os.process.execute` |
| `list_processes` | List running processes with optional name filtering | Low | readonly |
| `kill_process` | Send SIGTERM or SIGKILL to a process by PID | High | `os.process.kill` |
| `get_process_info` | Get detailed info about a running process by PID | Low | readonly |
| `open_application` | Launch an application as a detached process | Medium | `os.process.execute` |
| `close_application` | Close all processes matching an application name | Medium | `os.process.kill` |
| `set_env_var` | Set an environment variable in the process scope | Medium | local_worker |
| `get_env_var` | Read the value of an environment variable | Low | readonly |
| `get_system_info` | Get CPU, memory, disk, and OS information | Low | readonly |

## Quick Start

```yaml
actions:
  - id: check-status
    module: os_exec
    action: run_command
    params:
      command: ["git", "status", "--short"]
      working_directory: /home/user/project
```

## Requirements

- **psutil** -- Required for process management and system info queries.
  Install with `pip install psutil`.

## Configuration

Uses default LLMOS Bridge configuration. The PermissionGuard enforces profile-based
access control. Commands are always executed with `shell=False` for security.

## Platform Support

| Platform | Status |
|----------|--------|
| Linux | Supported |
| macOS | Supported |
| Windows | Supported |

## Related Modules

- **filesystem** -- File and directory operations; use `os_exec` for commands
  like `chmod` or `chown` not covered by the filesystem module.
- **gui** -- GUI automation for application interaction beyond process lifecycle.
- **window_tracker** -- Track and manage application windows after launching.
