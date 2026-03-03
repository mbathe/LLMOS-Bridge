# Changelog -- Module Manager

## [2.0.0] -- 2026-02-27

### Added
- 22 IML-callable actions for comprehensive module governance.
- **Listing and inspection:** `list_modules`, `get_module_info`.
- **Lifecycle management:** `enable_module`, `disable_module`, `pause_module`,
  `resume_module`, `restart_module`.
- **Action toggles:** `enable_action`, `disable_action`.
- **Introspection:** `get_module_health`, `get_module_metrics`, `get_module_state`,
  `list_services`, `get_system_status`.
- **Configuration:** `update_module_config`.
- **Hub / Package Manager (v3):** `install_module`, `uninstall_module`,
  `upgrade_module`, `search_hub`, `list_installed`, `verify_module`,
  `describe_module`.
- System module protection: `disable_module` and `uninstall_module` reject
  operations on system modules.
- `ModuleLifecycleManager`, `ServiceBus`, `ModuleInstaller`, and `HubClient`
  injection points.
- Health check aggregation in `list_modules` and `get_system_status`.
- Module type and state filtering in `list_modules`.

### Changed
- Upgraded from v1.0.0 to v2.0.0 with full hub integration and lifecycle
  management.
