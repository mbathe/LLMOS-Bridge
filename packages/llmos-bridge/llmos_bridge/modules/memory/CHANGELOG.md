# Changelog -- Memory Module

## [1.0.0] -- 2026-03-06

### Added
- Initial release with 11 actions and 4 built-in backends.
- `store` -- Store key-value pairs in any registered backend.
- `recall` -- Recall values by exact key lookup.
- `search` -- Semantic/fuzzy search across one or all backends.
- `delete` -- Delete keys from a backend.
- `list_keys` -- List keys with optional prefix filtering.
- `clear` -- Clear all entries in a backend.
- `list_backends` -- List registered backends and their capabilities.
- `set_objective` -- Set cognitive objective (permanent mental RAM).
- `get_context` -- Get full cognitive context for prompt injection.
- `update_progress` -- Update objective progress tracking.
- `observe` -- Real-time snapshot of all memory state across all backends.
- KV backend wrapping SQLite KeyValueStore.
- Vector backend wrapping ChromaDB VectorStore (optional).
- File backend using Markdown sections as key-value pairs.
- Cognitive backend with 3-layer architecture (HOT/WARM/COLD).
- `BaseMemoryBackend` ABC for custom backend plugins.
- Auto-injection of cognitive context into LLM prompts.
