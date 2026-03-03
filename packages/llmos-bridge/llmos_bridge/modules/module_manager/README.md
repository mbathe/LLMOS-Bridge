# Module Manager

Central control plane for the module system. List, enable, disable, pause,
resume, and introspect all registered modules and services.

## Overview

The Module Manager is a system module that provides 22 IML-callable actions for
full module lifecycle governance. It is the primary interface for an LLM or
admin to manage the LLMOS Bridge module system at runtime, including:

- **Listing and inspection** -- enumerate modules, check health, metrics, and state.
- **Lifecycle management** -- enable, disable, pause, resume, and restart modules.
- **Action toggles** -- selectively enable or disable individual actions on any module.
- **Hub operations** -- search, install, upgrade, uninstall, and verify community modules.
- **System status** -- aggregate health and state across all modules and services.

As a system module (`MODULE_TYPE = "system"`), the module manager cannot itself
be disabled or uninstalled.

## Actions

| Action | Description | Risk | Permission |
|--------|-------------|------|------------|
| `list_modules` | List all registered modules with state and type | Low | `readonly` |
| `get_module_info` | Get detailed information about a specific module | Low | `readonly` |
| `enable_module` | Enable (start) a disabled module | Medium | `power_user` |
| `disable_module` | Disable (stop) a module (system modules protected) | Medium | `power_user` |
| `pause_module` | Temporarily suspend a module's actions | Low | `local_worker` |
| `resume_module` | Resume a paused module | Low | `local_worker` |
| `restart_module` | Restart a module (stop then start) | Medium | `power_user` |
| `enable_action` | Re-enable a previously disabled action | Low | `local_worker` |
| `disable_action` | Disable a specific action on a module | Medium | `power_user` |
| `get_module_health` | Run health_check() on a specific module | Low | `readonly` |
| `get_module_metrics` | Get operational metrics from a module | Low | `readonly` |
| `get_module_state` | Get a state snapshot from a module | Low | `readonly` |
| `list_services` | List all registered services on the ServiceBus | Low | `readonly` |
| `get_system_status` | Get overall system health summary | Low | `readonly` |
| `update_module_config` | Update a module's runtime configuration | Medium | `power_user` |
| `install_module` | Install a module from the hub or local path | Medium | `power_user` |
| `uninstall_module` | Uninstall a community module | High | `power_user` |
| `upgrade_module` | Upgrade an installed module to a new version | Medium | `power_user` |
| `search_hub` | Search the module hub for available modules | Low | `readonly` |
| `list_installed` | List all installed community modules | Low | `readonly` |
| `verify_module` | Verify an installed module's integrity | Low | `readonly` |
| `describe_module` | Get dynamic self-description from a module | Low | `readonly` |

## Quick Start

```yaml
actions:
  - id: list-all
    module: module_manager
    action: list_modules

  - id: check-health
    module: module_manager
    action: get_module_health
    params:
      module_id: filesystem
```

## Requirements

No external dependencies required. The module uses only the built-in LLMOS
Bridge lifecycle, registry, and service bus subsystems.

Hub operations (`install_module`, `search_hub`) require network connectivity
and a configured `HubClient`.

## Configuration

The module manager requires injection of:
- `ModuleLifecycleManager` via `set_lifecycle_manager()` -- for all lifecycle operations
- `ServiceBus` via `set_service_bus()` -- for service listing
- `ModuleInstaller` via `set_installer()` -- for hub/package operations
- `HubClient` via `set_hub_client()` -- for remote hub queries

These are injected automatically by the LLMOS Bridge server at startup.

## Platform Support

| Platform | Status |
|----------|--------|
| Linux | Supported |
| macOS | Supported |
| Windows | Supported |
| Raspberry Pi | Supported |

## Related Modules

- **security** -- Permission checks for module lifecycle operations.
- **filesystem** -- Module installation writes to the filesystem.
- **os_exec** -- Module processes may spawn system commands.
