# API Integration

LLMOS apps can be managed and executed through the daemon's REST API.

## App Store

The App Store is a SQLite-backed registry that tracks registered applications. When the daemon is running, apps can be registered, listed, run, and managed via the API.

### App Lifecycle

```
.app.yaml file
     │
     ▼
POST /apps/register     →  registered
     │
     ▼
POST /apps/{id}/run     →  running → completed
     │
     ▼
DELETE /apps/{id}       →  removed
```

### App Status

| Status | Description |
|--------|-------------|
| `registered` | App is registered and ready to run |
| `running` | App is currently executing |
| `stopped` | App was stopped |
| `error` | App encountered an error |

## API Endpoints

All endpoints are prefixed with `/apps`.

### Register an App

```http
POST /apps/register
Content-Type: application/json

{
  "file_path": "/path/to/my-app.app.yaml"
}
```

**Response** (201):
```json
{
  "id": "abc123",
  "name": "my-app",
  "version": "1.0",
  "description": "My application",
  "status": "registered",
  "file_path": "/path/to/my-app.app.yaml",
  "created_at": 1709750400.0
}
```

### List Apps

```http
GET /apps
```

**Response** (200):
```json
[
  {
    "id": "abc123",
    "name": "my-app",
    "version": "1.0",
    "status": "registered",
    "run_count": 5,
    "last_run_at": 1709750400.0
  }
]
```

### Get App Details

```http
GET /apps/{app_id}
```

### Run an App

```http
POST /apps/{app_id}/run
Content-Type: application/json

{
  "input": "Review the latest changes"
}
```

**Response** (200):
```json
{
  "run_id": "run-xyz",
  "app_id": "abc123",
  "status": "completed",
  "output": "Here's my review...",
  "duration_ms": 15230
}
```

### Run with Streaming

```http
POST /apps/{app_id}/run
Content-Type: application/json
Accept: text/event-stream

{
  "input": "Analyze the codebase",
  "stream": true
}
```

Returns Server-Sent Events:

```
event: thought
data: {"content": "I'll start by examining the project structure..."}

event: tool_call
data: {"module": "filesystem", "action": "list_directory", "params": {"path": "."}}

event: tool_result
data: {"result": {"files": ["src/", "tests/", "README.md"]}}

event: response
data: {"content": "The project has the following structure..."}

event: done
data: {"success": true, "output": "Analysis complete."}
```

### Validate an App

```http
POST /apps/{app_id}/validate
```

Re-validates the app's YAML against the schema and semantic rules.

### Update App Status

```http
PUT /apps/{app_id}/status
Content-Type: application/json

{
  "status": "stopped"
}
```

### Delete an App

```http
DELETE /apps/{app_id}
```

Returns `204 No Content` on success.

### Execute a Tool Call

Route a single tool call through the daemon's security pipeline. Used internally by the CLI in daemon mode.

```http
POST /apps/execute-tool
Content-Type: application/json

{
  "module_id": "filesystem",
  "action": "read_file",
  "params": {"path": "/home/user/project/README.md"},
  "app_id": "abc123"
}
```

**Response** (200):

```json
{
  "success": true,
  "result": {"content": "# My Project\n...", "size": 1234}
}
```

When `app_id` is provided, the daemon loads the app's YAML security settings (profile, sandbox, capabilities) and applies them to this specific call. This ensures CLI daemon mode uses the same security pipeline as the API.

## Variables and Input

### Passing Variables

Variables can be passed at runtime:

```http
POST /apps/{app_id}/run
Content-Type: application/json

{
  "input": "Review changes",
  "variables": {
    "workspace": "/home/user/project",
    "review_depth": "thorough"
  }
}
```

### Input from CLI

```bash
# Direct input
llmos app run my-app.app.yaml --input "Fix the login bug"

# Variable overrides
llmos app run my-app.app.yaml --var workspace=/tmp/project
```

## Daemon Integration

When the LLMOS daemon is running, apps gain access to the full infrastructure:

### Module Access

| Mode | Available Modules |
|------|-------------------|
| Standalone | `filesystem`, `os_exec` |
| Daemon | All 20 modules (250+ actions) |

### Security Pipeline

In daemon mode, every tool call goes through the full 15-step security pipeline:

```text
App tool call
    → DaemonToolExecutor
        1.  Rate limiting (per-tool rate_limit_per_minute check)
        2.  Intent verification (LLM-based semantic analysis, if enabled)
        3.  Authorization check (Application identity allowlist)
        4.  Tool constraints (paths, domains, forbidden commands/patterns)
        5.  Sandbox enforcement (allowed_paths, blocked_commands)
        6.  Capability check (capabilities.grant / deny evaluation)
        7.  Approval gates (capabilities.approval_required + when: conditions)
        8.  SecurityScanner.scan() (prompt injection detection)
        9.  PermissionGuard.check_action() (profile enforcement)
       10.  ModuleRegistry.get(module).execute()
       11.  Perception injection (if action has perception config)
       12.  Post-execution perception capture
       13.  OutputSanitizer.sanitize() (remove prompt injection from output)
       14.  EventBus.emit("llmos.actions.results") (audit trail)
       15.  Action count tracking (for count-based approval triggers)
```

Each step can independently reject the call. Steps 1-9 run **before** execution; steps 11-15 run **after**.

### Cognitive Memory Auto-Injection

When the daemon's `memory` module is available and configured with a cognitive backend, objectives and context are **automatically injected** into the agent's system prompt on every LLM call. You don't need to configure this — it happens transparently when the memory module is wired.

```yaml
# Just declare memory usage — cognitive injection is automatic in daemon mode
memory:
  levels:
    working:
      backend: kv
    episodic:
      backend: chromadb
```

### Event Bus

Apps in daemon mode can emit and receive events:

```yaml
# Emit events
flow:
  - emit:
      topic: "app.review.complete"
      event: { status: "done", findings: 5 }

# React to events
triggers:
  - type: event
    topic: "llmos.modules.installed"
```

### Identity & RBAC

Apps run under an Application identity with:
- **Allowed modules** — Only granted modules are accessible
- **Allowed actions** — Fine-grained action control per module
- **Session management** — Time-limited sessions with constraints
- **RBAC** — Role hierarchy: ADMIN > APP_ADMIN > OPERATOR > VIEWER > AGENT

## CLI Reference

```bash
# Run an app (connects to daemon if running, falls back to standalone)
llmos app run my-app.app.yaml

# Run with input
llmos app run my-app.app.yaml --input "..."

# Validate YAML
llmos app validate my-app.app.yaml

# List registered apps (requires daemon)
llmos app list

# Show app info
llmos app info my-app.app.yaml

# Register with daemon
llmos app register my-app.app.yaml
```

## Programmatic Usage (Python)

```python
from llmos_bridge.apps.compiler import AppCompiler
from llmos_bridge.apps.runtime import AppRuntime

# Compile
compiler = AppCompiler()
app_def = compiler.compile_file("my-app.app.yaml")

# Run
runtime = AppRuntime(
    module_info=module_info,
    llm_provider_factory=provider_factory,
    execute_tool=tool_executor,
)
result = await runtime.run(app_def, "Process this input")
print(result.output)
```
