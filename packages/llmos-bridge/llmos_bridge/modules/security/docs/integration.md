# Security Module -- Integration Guide

## Cross-Module Workflows

### Pre-flight Permission Check

Before executing a sensitive action in another module, use the security module
to verify and request the necessary permissions.

```yaml
actions:
  - id: check-write-perm
    module: security
    action: check_permission
    params:
      permission: filesystem.write
      module_id: filesystem

  - id: request-write-perm
    module: security
    action: request_permission
    depends_on: [check-write-perm]
    params:
      permission: filesystem.write
      module_id: filesystem
      reason: "Need to save generated report"
      scope: session

  - id: write-report
    module: filesystem
    action: write_file
    depends_on: [request-write-perm]
    params:
      path: /tmp/report.txt
      content: "{{result.generate-report.output}}"
```

### Security Status Dashboard

Combine security status with module manager to get a full system overview.

```yaml
actions:
  - id: sec-status
    module: security
    action: get_security_status

  - id: sys-status
    module: module_manager
    action: get_system_status
    params:
      include_health: true
```

### Permission Lifecycle for IoT

IoT actions require GPIO permissions. Use the security module to grant them
before hardware operations.

```yaml
actions:
  - id: grant-gpio
    module: security
    action: request_permission
    params:
      permission: gpio.write
      module_id: iot
      reason: "Need to control LED on pin 18"
      scope: session

  - id: setup-led
    module: iot
    action: set_pin_mode
    depends_on: [grant-gpio]
    params:
      pin: 18
      mode: output

  - id: turn-on-led
    module: iot
    action: digital_write
    depends_on: [setup-led]
    params:
      pin: 18
      value: 1
```

### Audit Trail for Compliance

After a sequence of sensitive operations, retrieve audit events for logging.

```yaml
actions:
  - id: do-sensitive-work
    module: os_exec
    action: run_command
    params:
      command: ["systemctl", "restart", "myservice"]

  - id: get-audit
    module: security
    action: list_audit_events
    depends_on: [do-sensitive-work]
    params:
      limit: 10
```

## Integration with SecurityManager

The security module is a thin IML wrapper around the `SecurityManager`
subsystem. Modules that need programmatic (non-IML) permission checks should
use `SecurityManager.permission_manager` directly via dependency injection.

### Architecture

```
IML Plan
  -> Executor
    -> SecurityModule._action_request_permission()
      -> SecurityManager.permission_manager.grant()
        -> PermissionStore (SQLite)
```

### Event Bus Integration

Permission grant/revoke operations emit events on the following topics:
- `llmos.security.permission_granted`
- `llmos.security.permission_revoked`
- `llmos.security.permission_checked`

These events are routed through the `EventBus` (via `AuditLogger`) and can be
consumed by external systems in Phase 4 (RedisStreamsBus).
