# Changelog -- Security Module

## [1.0.0] -- 2026-02-27

### Added
- Initial release with 6 actions.
- `list_permissions` -- List all granted permissions with optional module filter.
- `check_permission` -- Check if a specific permission is granted for a module,
  including risk level and grant details.
- `request_permission` -- Request a permission grant with scope (session/permanent).
  LOW-risk auto-granted; MEDIUM/HIGH/CRITICAL go through approval gate.
- `revoke_permission` -- Revoke a previously granted permission. Decorated with
  `@sensitive_action(RiskLevel.HIGH)` and `@audit_trail("detailed")`.
- `get_security_status` -- Return a security overview: total grants, grouped by
  module and risk level.
- `list_audit_events` -- Stub for Phase 3 full audit event query support.
- Security decorators: `@audit_trail`, `@sensitive_action` on mutation actions.
- `SecurityManager` injection via `set_security_manager()`.
