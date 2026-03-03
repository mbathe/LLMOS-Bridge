# Module Manager -- Action Reference

## list_modules

List all registered modules with their state and type.

**Permission required:** `readonly`
**Risk level:** Low

### Parameters

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `module_type` | string | No | `null` | Filter: `system` or `user` |
| `state` | string | No | `null` | Filter by lifecycle state (e.g. `active`, `paused`) |
| `include_health` | boolean | No | `false` | Include health check results for active modules |

### Returns

```json
{
  "modules": [
    {
      "module_id": "filesystem",
      "state": "active",
      "type": "daemon",
      "health": {"status": "healthy"}
    }
  ],
  "count": 1
}
```

---

## get_module_info

Get detailed information about a specific module.

**Permission required:** `readonly`
**Risk level:** Low

### Parameters

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `module_id` | string | Yes | -- | Module ID to inspect |
| `include_health` | boolean | No | `false` | Include health data |
| `include_metrics` | boolean | No | `false` | Include metrics |

### Returns

```json
{
  "module_id": "filesystem",
  "version": "1.0.0",
  "description": "Read, write, move, copy, delete files and directories.",
  "state": "active",
  "type": "daemon",
  "actions": ["read_file", "write_file", "..."],
  "disabled_actions": [],
  "health": {"status": "healthy"},
  "metrics": {"total_executions": 42}
}
```

---

## enable_module

Enable (start) a disabled module.

**Permission required:** `power_user`
**Risk level:** Medium

### Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `module_id` | string | Yes | Module to enable |

### Returns

```json
{
  "module_id": "filesystem",
  "state": "active",
  "success": true
}
```

---

## disable_module

Disable (stop) a module. System modules cannot be disabled.

**Permission required:** `power_user`
**Risk level:** Medium

### Parameters

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `module_id` | string | Yes | -- | Module to disable |
| `reason` | string | No | `""` | Reason for disabling |

### Returns

```json
{
  "module_id": "custom_module",
  "state": "disabled",
  "success": true
}
```

---

## pause_module

Temporarily suspend a module's actions.

**Permission required:** `local_worker`
**Risk level:** Low

### Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `module_id` | string | Yes | Module to pause |

### Returns

```json
{
  "module_id": "filesystem",
  "state": "paused",
  "success": true
}
```

---

## resume_module

Resume a paused module.

**Permission required:** `local_worker`
**Risk level:** Low

### Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `module_id` | string | Yes | Module to resume |

### Returns

```json
{
  "module_id": "filesystem",
  "state": "active",
  "success": true
}
```

---

## restart_module

Restart a module (stop then start).

**Permission required:** `power_user`
**Risk level:** Medium

### Parameters

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `module_id` | string | Yes | -- | Module to restart |
| `force` | boolean | No | `false` | Force restart even if in error state |

### Returns

```json
{
  "module_id": "filesystem",
  "state": "active",
  "success": true
}
```

---

## enable_action

Re-enable a previously disabled action.

**Permission required:** `local_worker`
**Risk level:** Low

### Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `module_id` | string | Yes | Module containing the action |
| `action` | string | Yes | Action to re-enable |

### Returns

```json
{
  "module_id": "filesystem",
  "action": "delete_file",
  "enabled": true
}
```

---

## disable_action

Disable a specific action on a module.

**Permission required:** `power_user`
**Risk level:** Medium

### Parameters

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `module_id` | string | Yes | -- | Module containing the action |
| `action` | string | Yes | -- | Action to disable |
| `reason` | string | No | `""` | Reason for disabling |

### Returns

```json
{
  "module_id": "filesystem",
  "action": "delete_file",
  "enabled": false,
  "reason": "Temporarily disabled for safety"
}
```

---

## get_module_health

Run health_check() on a specific module.

**Permission required:** `readonly`
**Risk level:** Low

### Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `module_id` | string | Yes | Module to health-check |

### Returns

```json
{
  "status": "healthy",
  "details": {}
}
```

---

## get_module_metrics

Get operational metrics from a module.

**Permission required:** `readonly`
**Risk level:** Low

### Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `module_id` | string | Yes | Module ID |

### Returns

```json
{
  "module_id": "filesystem",
  "metrics": {
    "total_executions": 42,
    "failed_executions": 1,
    "avg_duration_ms": 12.5
  }
}
```

---

## get_module_state

Get a state snapshot from a module.

**Permission required:** `readonly`
**Risk level:** Low

### Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `module_id` | string | Yes | Module ID |

### Returns

```json
{
  "module_id": "filesystem",
  "state_snapshot": {}
}
```

---

## list_services

List all registered services on the ServiceBus.

**Permission required:** `readonly`
**Risk level:** Low

### Parameters

None.

### Returns

```json
{
  "services": ["filesystem.watcher", "os_exec.runner"],
  "count": 2
}
```

---

## get_system_status

Get overall system health summary.

**Permission required:** `readonly`
**Risk level:** Low

### Parameters

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `include_health` | boolean | No | `false` | Include per-module health checks |

### Returns

```json
{
  "total_modules": 12,
  "by_state": {"active": 10, "paused": 1, "disabled": 1},
  "by_type": {"system": 3, "daemon": 7, "user": 2},
  "failed": [],
  "platform_excluded": ["iot"],
  "health": {},
  "service_count": 5
}
```

---

## update_module_config

Update a module's runtime configuration.

**Permission required:** `power_user`
**Risk level:** Medium

### Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `module_id` | string | Yes | Module to configure |
| `config` | object | Yes | Configuration dict to apply |

### Returns

```json
{
  "module_id": "filesystem",
  "success": true
}
```

---

## install_module

Install a module from the hub or a local path.

**Permission required:** `power_user`
**Risk level:** Medium
**Side effects:** `filesystem_write`, `network_request`

### Parameters

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `source` | string | No | `"hub"` | `hub` or `local` |
| `module_id` | string | No | `""` | Module ID (for hub install) |
| `path` | string | No | `""` | Local path (for local install) |
| `version` | string | No | `"latest"` | Version constraint |

### Returns

```json
{
  "success": true,
  "module_id": "community-weather",
  "version": "1.2.0",
  "error": null,
  "installed_deps": ["requests"]
}
```

---

## uninstall_module

Uninstall a community module. This action is irreversible.

**Permission required:** `power_user`
**Risk level:** High
**Irreversible:** Yes
**Side effects:** `filesystem_write`

### Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `module_id` | string | Yes | Module to uninstall |

### Returns

```json
{
  "success": true,
  "module_id": "community-weather",
  "version": "1.2.0",
  "error": null
}
```

---

## upgrade_module

Upgrade an installed module to a new version.

**Permission required:** `power_user`
**Risk level:** Medium
**Side effects:** `filesystem_write`

### Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `module_id` | string | Yes | Module to upgrade |
| `path` | string | Yes | Path to new version package directory |

### Returns

```json
{
  "success": true,
  "module_id": "community-weather",
  "version": "2.0.0",
  "error": null
}
```

---

## search_hub

Search the module hub for available modules.

**Permission required:** `readonly`
**Risk level:** Low
**Side effects:** `network_request`

### Parameters

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `query` | string | Yes | -- | Search query |
| `limit` | integer | No | `20` | Max results to return |

### Returns

```json
{
  "results": [
    {
      "module_id": "community-weather",
      "version": "1.2.0",
      "description": "Weather data from multiple providers.",
      "author": "community",
      "downloads": 1500,
      "tags": ["weather", "api"]
    }
  ],
  "count": 1
}
```

---

## list_installed

List all installed community modules.

**Permission required:** `readonly`
**Risk level:** Low

### Parameters

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `enabled_only` | boolean | No | `false` | Only show enabled modules |

### Returns

```json
{
  "modules": [
    {
      "module_id": "community-weather",
      "version": "1.2.0",
      "install_path": "/home/user/.llmos/modules/community-weather",
      "enabled": true,
      "sandbox_level": "basic"
    }
  ],
  "count": 1
}
```

---

## verify_module

Verify an installed module's integrity (signature + hash).

**Permission required:** `readonly`
**Risk level:** Low

### Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `module_id` | string | Yes | Module to verify |

### Returns

```json
{
  "module_id": "community-weather",
  "verified": true,
  "signature_valid": true,
  "hash_match": true
}
```

---

## describe_module

Get dynamic self-description from a module (v3 `describe()` method).

**Permission required:** `readonly`
**Risk level:** Low

### Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `module_id` | string | Yes | Module to describe |

### Returns

The return value depends on the module's `describe()` implementation. Typically
includes capabilities, current state, and usage hints.
