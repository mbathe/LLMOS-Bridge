# Module Manager -- Integration Guide

## Cross-Module Workflows

### System Health Dashboard

Combine module manager introspection with security status for a complete
system overview.

```yaml
actions:
  - id: sys-status
    module: module_manager
    action: get_system_status
    params:
      include_health: true

  - id: sec-status
    module: security
    action: get_security_status

  - id: list-services
    module: module_manager
    action: list_services
```

### Safe Module Restart with Health Verification

Restart a module and verify it comes back healthy before proceeding.

```yaml
actions:
  - id: restart-fs
    module: module_manager
    action: restart_module
    params:
      module_id: filesystem
      force: false

  - id: verify-health
    module: module_manager
    action: get_module_health
    depends_on: [restart-fs]
    params:
      module_id: filesystem
```

### Install, Verify, and Enable a Community Module

Complete workflow for safely adding a community module from the hub.

```yaml
actions:
  - id: search
    module: module_manager
    action: search_hub
    params:
      query: "weather"
      limit: 5

  - id: install
    module: module_manager
    action: install_module
    depends_on: [search]
    params:
      source: hub
      module_id: community-weather
      version: ">=1.0.0"

  - id: verify
    module: module_manager
    action: verify_module
    depends_on: [install]
    params:
      module_id: community-weather

  - id: enable
    module: module_manager
    action: enable_module
    depends_on: [verify]
    params:
      module_id: community-weather

  - id: health-check
    module: module_manager
    action: get_module_health
    depends_on: [enable]
    params:
      module_id: community-weather
```

### Disable Dangerous Actions at Runtime

Temporarily disable a risky action while keeping the module active.

```yaml
actions:
  - id: disable-delete
    module: module_manager
    action: disable_action
    params:
      module_id: filesystem
      action: delete_file
      reason: "Temporarily disabled during sensitive operation"

  - id: do-work
    module: filesystem
    action: write_file
    depends_on: [disable-delete]
    params:
      path: /tmp/output.txt
      content: "Safe operation"

  - id: re-enable-delete
    module: module_manager
    action: enable_action
    depends_on: [do-work]
    params:
      module_id: filesystem
      action: delete_file
```

### Module Configuration Update with Restart

Update a module's configuration and restart it to apply changes.

```yaml
actions:
  - id: update-config
    module: module_manager
    action: update_module_config
    params:
      module_id: gui
      config:
        screenshot_format: png
        screenshot_quality: 90

  - id: restart
    module: module_manager
    action: restart_module
    depends_on: [update-config]
    params:
      module_id: gui

  - id: verify
    module: module_manager
    action: get_module_health
    depends_on: [restart]
    params:
      module_id: gui
```

### Graceful Shutdown Sequence

Pause all non-system modules before performing a system-level operation.

```yaml
actions:
  - id: list-active
    module: module_manager
    action: list_modules
    params:
      state: active

  - id: pause-fs
    module: module_manager
    action: pause_module
    depends_on: [list-active]
    params:
      module_id: filesystem

  - id: pause-gui
    module: module_manager
    action: pause_module
    depends_on: [list-active]
    params:
      module_id: gui

  - id: system-op
    module: os_exec
    action: run_command
    depends_on: [pause-fs, pause-gui]
    params:
      command: ["apt", "update"]

  - id: resume-fs
    module: module_manager
    action: resume_module
    depends_on: [system-op]
    params:
      module_id: filesystem

  - id: resume-gui
    module: module_manager
    action: resume_module
    depends_on: [system-op]
    params:
      module_id: gui
```

## Integration with ModuleLifecycleManager

The module manager is a thin IML wrapper around the `ModuleLifecycleManager`.
Direct programmatic access (non-IML) is available for internal subsystems.

### Architecture

```
IML Plan
  -> Executor
    -> ModuleManagerModule._action_enable_module()
      -> ModuleLifecycleManager.start_module()
        -> ModuleRegistry.get() -> BaseModule.on_start()

IML Plan
  -> Executor
    -> ModuleManagerModule._action_install_module()
      -> ModuleInstaller.install_from_hub()
        -> HubClient.download()
        -> Module signature verification
        -> ModuleRegistry.register()
```

### System Module Protection

System modules (identified by `SYSTEM_MODULE_IDS` in `modules/types.py`)
cannot be disabled or uninstalled. The following module IDs are protected:
- `module_manager`
- `security`

Attempting to disable or uninstall a system module returns an error response
without modifying any state.
