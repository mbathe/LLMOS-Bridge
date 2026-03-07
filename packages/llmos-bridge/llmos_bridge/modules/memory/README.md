# Memory Module

Multi-backend memory system with cognitive persistence, real-time state awareness, and pluggable backends.

## Overview

The Memory module provides a unified interface for storing, recalling, and searching information across multiple memory backends. It goes beyond simple key-value storage with:

- **Cognitive Persistence**: Set an objective that stays in permanent "mental RAM" — every LLM prompt automatically includes the objective context
- **Real-time State Awareness**: The `observe` action gives a complete snapshot of all memory state without knowing specific keys
- **Auto-injection**: Cognitive context is automatically prepended to every LLM call
- **Pluggable Backends**: Register custom backends at runtime by subclassing `BaseMemoryBackend`

## Built-in Backends

| Backend | ID | Description |
|---------|-----|-------------|
| KV | `kv` | Fast persistent key-value store (SQLite) |
| Vector | `vector` | Semantic search via ChromaDB embeddings |
| File | `file` | Markdown file storage (sections as keys) |
| Cognitive | `cognitive` | Objective-driven 3-layer memory (HOT/WARM/COLD) |

## Actions

| Action | Description | Risk | Permission |
|--------|-------------|------|------------|
| `store` | Store a key-value pair in any backend | Low | `data.memory.write` |
| `recall` | Recall a value by exact key | Low | `data.memory.read` |
| `search` | Semantic/fuzzy search across backends | Low | `data.memory.read` |
| `delete` | Delete a key from a backend | Medium | `data.memory.write` |
| `list_keys` | List keys with optional prefix filter | Low | `data.memory.read` |
| `clear` | Clear all entries in a backend | High | `data.memory.write` |
| `list_backends` | List available backends | Low | `data.memory.read` |
| `set_objective` | Set cognitive objective (permanent until completed) | Low | `data.memory.write` |
| `get_context` | Get full cognitive context (auto-injected into prompts) | Low | `data.memory.read` |
| `update_progress` | Update objective progress | Low | `data.memory.write` |
| `observe` | Real-time snapshot of ALL memory state (no key lookup needed) | Low | `data.memory.read` |

## Quick Start

```yaml
# In an app YAML — memory actions are auto-included when memory: is configured
agent:
  tools:
    - module: memory
      action: store
    - module: memory
      action: observe  # gives the LLM full state awareness

memory:
  working:
    max_size: "50MB"
  project:
    path: "{{workspace}}/.llmos/MEMORY.md"
```

## Cognitive Persistence

The cognitive backend implements a 3-layer architecture inspired by human cognition:

- **HOT (0ms)**: Core objective + active context — always in Python dict
- **WARM (0ms)**: Recent decisions (deque, capped at 50) — session state
- **COLD (~1ms)**: Objective history, archived objectives — JSON persistence

When an objective is set, it is NEVER forgotten until completed. The `format_for_prompt()` method generates text that is auto-injected into every LLM system prompt, ensuring the agent always acts in service of its objective.

## Custom Backends

Create a custom backend by subclassing `BaseMemoryBackend`:

```python
from llmos_bridge.modules.memory.backends.base import BaseMemoryBackend, MemoryEntry

class RedisBackend(BaseMemoryBackend):
    BACKEND_ID = "redis"
    DESCRIPTION = "Redis-backed memory for distributed agents"

    async def store(self, key, value, *, metadata=None, ttl_seconds=None):
        await self.redis.set(key, value, ex=ttl_seconds)
        return MemoryEntry(key=key, value=value, backend=self.BACKEND_ID)

    async def recall(self, key):
        val = await self.redis.get(key)
        if val is None:
            return None
        return MemoryEntry(key=key, value=val, backend=self.BACKEND_ID)

    # ... implement delete, list_keys, search (optional)
```

Register at runtime:
```python
memory_module.register_backend(RedisBackend())
```

## Requirements

No external dependencies for core functionality. Optional:
- `chromadb` for vector backend (install with `pip install llmos-bridge[memory]`)

## Platform Support

| Platform | Status |
|----------|--------|
| Linux | Supported |
| macOS | Supported |
| Windows | Supported |

## Related Modules

- **database** — Raw SQL storage for structured data
- **filesystem** — File I/O for reading/writing memory files
