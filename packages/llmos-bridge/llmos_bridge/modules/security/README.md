# Security Module

Query and manage OS-level permissions. Check which permissions are granted,
request new ones, or revoke existing grants.

## Overview

The Security module exposes the LLMOS Bridge permission system as IML actions,
allowing the LLM (or an admin) to inspect, request, and revoke OS-level
permissions at runtime. It wraps the `SecurityManager` and `PermissionManager`
subsystems, providing a safe, auditable interface for permission governance.

LOW-risk permissions are auto-granted by default. MEDIUM, HIGH, and CRITICAL
risk permissions go through the approval gate (Phase 2). All grant and revoke
operations are recorded via the `@audit_trail` decorator.

## Actions

| Action | Description | Risk | Permission |
|--------|-------------|------|------------|
| `list_permissions` | List all currently granted OS-level permissions | Low | `readonly` |
| `check_permission` | Check if a specific permission is granted for a module | Low | `readonly` |
| `request_permission` | Request a permission grant for a module | Medium | `local_worker` |
| `revoke_permission` | Revoke a previously granted permission | High | `power_user` |
| `get_security_status` | Get a security overview grouped by module and risk level | Low | `readonly` |
| `list_audit_events` | List recent security audit events (stub for Phase 3) | Low | `readonly` |

## Quick Start

```yaml
actions:
  - id: check-fs-write
    module: security
    action: check_permission
    params:
      permission: filesystem.write
      module_id: filesystem

  - id: request-if-needed
    module: security
    action: request_permission
    params:
      permission: filesystem.write
      module_id: filesystem
      reason: "Need to write configuration file"
      scope: session
```

## Requirements

No external dependencies required. The module uses only the built-in LLMOS
Bridge security subsystem (`SecurityManager`, `PermissionManager`,
`PermissionStore`).

## Configuration

The security module requires a `SecurityManager` to be injected at startup via
`set_security_manager()`. This is handled automatically by the LLMOS Bridge
server during module initialization.

Key configuration options in `SecurityAdvancedConfig`:
- `auto_grant_low_risk` -- automatically grant LOW-risk permission requests
- `enable_decorators` -- enable security decorator enforcement
- `enable_rate_limiting` -- enable per-action rate limiting

## Platform Support

| Platform | Status |
|----------|--------|
| Linux | Supported |
| macOS | Supported |
| Windows | Supported |
| Raspberry Pi | Supported |

## Related Modules

- **module_manager** -- Uses permissions to control module lifecycle operations.
- **os_exec** -- Requires OS-level permissions for command execution.
- **filesystem** -- File operations gated by `filesystem.read`/`filesystem.write` permissions.
