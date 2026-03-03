# Changelog -- Triggers Module

## [1.0.0] -- 2026-02-15

### Added
- Initial release with 6 actions.
- `register_trigger` -- Create triggers with condition types: temporal, filesystem, process, resource, application, iot, composite.
- `activate_trigger` -- Enable and arm an existing trigger for event watching.
- `deactivate_trigger` -- Pause a trigger without deleting its configuration.
- `delete_trigger` -- Permanently remove a trigger from the TriggerDaemon.
- `list_triggers` -- List all triggers with state, type, tags, and created_by filters.
- `get_trigger` -- Retrieve a single trigger with full health metrics.
- Dependency injection via `set_daemon()` for TriggerDaemon integration.
- Priority system: background, low, normal, high, critical.
- Conflict policies: queue, preempt, reject.
- Rate limiting: `min_interval_seconds` and `max_fires_per_hour` per trigger.
- Chain depth protection with configurable `max_chain_depth` (default 5, max 20).
- Security decorators: `@requires_permission(Permission.PROCESS_EXECUTE)`, `@sensitive_action`, `@audit_trail`.
