# Context Manager Module — Integration Guide

## Daemon Mode

The context_manager module is registered in the module registry like any other module. The daemon's `server.py` startup wires it with:

1. **Module manifests** — for tool summary generation
2. **Application permissions** — from the identity system
3. **Summarizer callback** — an LLM call for conversation compression

```python
from llmos_bridge.modules.context_manager.module import ContextManagerModule

ctx_mgr = ContextManagerModule()
ctx_mgr.set_manifests(registry.all_manifests())
ctx_mgr.set_summarizer(llm_summarize_fn)
registry.register(ctx_mgr)
```

## Standalone Mode (CLI)

In `llmos app run`, the module is created and wired into the StandaloneToolExecutor:

```python
ctx_mgr = ContextManagerModule()
ctx_mgr.set_manifests(...)  # From module_info
executor.set_context_manager_module(ctx_mgr)
```

## YAML App Language

Add context_manager actions to the agent's tool list:

```yaml
agent:
  tools:
    - module: context_manager
      action: get_budget
    - module: context_manager
      action: compress_history
    - module: context_manager
      action: fetch_context
    - module: context_manager
      action: get_tools_summary
    - module: context_manager
      action: get_state
```

Configure budgets in the memory block:

```yaml
memory:
  context:
    model_context_window: 200000
    output_reserved: 8192
    cognitive_max_tokens: 1500
    compression_trigger_ratio: 0.75
```

## Runtime Integration

The AgentRuntime uses the context_manager module at two levels:

### Automatic (runtime-level)

Before each LLM call, the runtime:
1. Updates the module's state (`update_state()`)
2. Checks if compression is needed (`compute_budget().compression_needed`)
3. Auto-compresses if needed
4. Bounds cognitive text (`bound_cognitive_text()`)

### On-demand (tool-level)

The LLM itself can call:
- `get_budget` — to understand its context situation
- `compress_history` — to manually trigger compression
- `fetch_context` — to retrieve details from compressed segments
- `get_tools_summary` — to check available capabilities

## Application Identity Integration

When running under an Application identity, the module filters tools and modules based on `Application.allowed_modules` and `Application.allowed_actions`. This ensures the LLM only sees tools it's actually allowed to use.

```python
ctx_mgr.set_application_permissions(
    allowed_modules=app.allowed_modules,
    allowed_actions=app.allowed_actions,
)
```
