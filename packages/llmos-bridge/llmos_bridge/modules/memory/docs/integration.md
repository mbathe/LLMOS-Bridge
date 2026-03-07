# Memory Module -- Integration Guide

## App Language (YAML)

When a `memory:` block is present in the app YAML, the runtime auto-includes
all memory module actions. The agent can then call `memory.store`, `memory.observe`, etc.

```yaml
memory:
  working:
    max_size: "50MB"
  project:
    path: "{{workspace}}/.llmos/MEMORY.md"
    auto_inject: true

agent:
  tools:
    - module: memory
      action: store
    - module: memory
      action: observe
    - module: memory
      action: set_objective
```

## Daemon Mode

In daemon mode (via `llmos-bridge serve`), the memory module is wired
automatically in `server.py`:

1. KV backend injected from the existing `KeyValueStore`
2. File backend pointing to `~/.llmos/state/MEMORY.md`
3. Cognitive backend persisting to `~/.llmos/state/cognitive_state.json`
4. Vector backend (optional, requires ChromaDB)

## Standalone Mode (CLI)

When running `llmos-bridge app run` without a daemon, the CLI creates
a standalone MemoryModule with KV, File, and Cognitive backends.

## Custom Backend Registration

Register a backend at startup or runtime:

```python
from llmos_bridge.modules.memory.module import MemoryModule
from llmos_bridge.modules.memory.backends.base import BaseMemoryBackend

class MyBackend(BaseMemoryBackend):
    BACKEND_ID = "my_store"
    DESCRIPTION = "Custom store"
    # ... implement store, recall, delete, list_keys

memory_module = registry.get("memory")
memory_module.register_backend(MyBackend())
```

## Auto-Injection into LLM Prompts

When the cognitive backend has an active objective, `get_cognitive_prompt()`
returns formatted markdown that should be prepended to the LLM system prompt.
The app runtime does this automatically on every LLM call.
