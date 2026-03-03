# Changelog -- OS/Exec Module

## [1.0.0] -- 2026-01-15

### Added
- Initial release with 9 actions.
- `run_command` -- Execute external commands as list (shell=False, never shell strings).
- `list_processes` -- List running processes with optional name filter.
- `kill_process` -- Send SIGTERM or SIGKILL to a process by PID.
- `get_process_info` -- Get detailed process info (name, status, CPU, memory, cmdline).
- `open_application` -- Launch applications as detached processes.
- `close_application` -- Close all processes matching an application name.
- `set_env_var` -- Set environment variables in process scope.
- `get_env_var` -- Read environment variable values.
- `get_system_info` -- Query CPU, memory, disk, and OS information.
- Security decorators: `@requires_permission`, `@rate_limited`, `@audit_trail`, `@sensitive_action`.
- Subprocess timeout enforcement at the process level.
