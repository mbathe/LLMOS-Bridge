# Security Module -- Action Reference

## list_permissions

List all currently granted OS-level permissions, optionally filtered by module.

**Permission required:** `readonly`
**Risk level:** Low

### Parameters

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `module_id` | string | No | `null` | Filter by module ID |

### Returns

```json
{
  "grants": [
    {
      "permission": "filesystem.write",
      "module_id": "filesystem",
      "scope": "session",
      "granted_by": "llm",
      "granted_at": "2026-02-27T10:00:00Z"
    }
  ],
  "count": 1
}
```

---

## check_permission

Check if a specific OS-level permission is granted for a module.

**Permission required:** `readonly`
**Risk level:** Low

### Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `permission` | string | Yes | Permission identifier (e.g. `filesystem.write`) |
| `module_id` | string | Yes | Module to check permission for |

### Returns

```json
{
  "permission": "filesystem.write",
  "module_id": "filesystem",
  "granted": true,
  "risk_level": "low",
  "grant": {
    "permission": "filesystem.write",
    "module_id": "filesystem",
    "scope": "session",
    "granted_by": "llm"
  }
}
```

---

## request_permission

Request an OS-level permission for a module. LOW-risk permissions are
auto-granted. MEDIUM/HIGH/CRITICAL permissions require explicit approval.

**Permission required:** `local_worker`
**Risk level:** Medium
**Decorators:** `@audit_trail("detailed")`

### Parameters

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `permission` | string | Yes | -- | Permission identifier to request |
| `module_id` | string | Yes | -- | Module requesting the permission |
| `reason` | string | No | `""` | Why this permission is needed |
| `scope` | string | No | `"session"` | Grant scope: `session` or `permanent` |

### Returns

```json
{
  "permission": "filesystem.write",
  "module_id": "filesystem",
  "granted": true,
  "grant": {
    "permission": "filesystem.write",
    "module_id": "filesystem",
    "scope": "session",
    "granted_by": "llm"
  }
}
```

---

## revoke_permission

Revoke a previously granted OS-level permission.

**Permission required:** `power_user`
**Risk level:** High
**Decorators:** `@audit_trail("detailed")`, `@sensitive_action(RiskLevel.HIGH)`

### Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `permission` | string | Yes | Permission identifier to revoke |
| `module_id` | string | Yes | Module to revoke permission from |

### Returns

```json
{
  "permission": "filesystem.write",
  "module_id": "filesystem",
  "revoked": true
}
```

---

## get_security_status

Get a security overview: total grants, grouped by module and risk level.

**Permission required:** `readonly`
**Risk level:** Low

### Parameters

None.

### Returns

```json
{
  "total_grants": 5,
  "grants_by_module": {
    "filesystem": 2,
    "os_exec": 3
  },
  "grants_by_risk_level": {
    "low": 3,
    "medium": 1,
    "high": 1,
    "critical": 0
  }
}
```

---

## list_audit_events

List recent security audit events. Currently a stub -- full query support is
planned for Phase 3.

**Permission required:** `readonly`
**Risk level:** Low

### Parameters

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `limit` | integer | No | `50` | Maximum events to return (1-1000) |

### Returns

```json
{
  "events": [],
  "message": "Full audit event query support coming in Phase 3."
}
```
