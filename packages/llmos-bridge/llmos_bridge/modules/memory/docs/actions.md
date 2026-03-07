# Memory Module -- Action Reference

## store

Store a key-value pair in a memory backend.

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `key` | string | Yes | -- | Key to store under |
| `value` | string | Yes | -- | Value to store |
| `backend` | string | No | `"kv"` | Backend to use: kv, vector, file, cognitive |
| `metadata` | object | No | -- | Optional metadata dict |
| `ttl_seconds` | number | No | -- | Time-to-live in seconds (0 = forever) |

### Returns

```json
{"stored": true, "key": "string", "value": "any", "backend": "string"}
```

---

## recall

Recall a value by exact key from a memory backend.

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `key` | string | Yes | -- | Key to recall |
| `backend` | string | No | `"kv"` | Backend to query |

### Returns

```json
{"found": true, "key": "string", "value": "any", "metadata": {}, "backend": "string"}
```

---

## search

Semantic or fuzzy search across one or all backends.

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `query` | string | Yes | -- | Search query |
| `backend` | string | No | all | Backend to search (omit to search all) |
| `top_k` | integer | No | 5 | Max results to return |

### Returns

```json
{"results": [{"key": "str", "value": "any", "score": 0.95, "backend": "str"}], "count": 3}
```

---

## observe

Get a real-time snapshot of ALL memory state across ALL backends. The LLM receives a human-readable summary without needing to know specific keys.

### Parameters

None.

### Returns

```json
{
  "cognitive": {"has_objective": true, "goal": "...", "progress": "50%", "decisions": 3},
  "backends": {
    "kv": {"key_count": 12, "sample_keys": ["user_pref", "project_lang"]},
    "file": {"key_count": 5, "sample_keys": ["architecture", "conventions"]}
  },
  "summary": "Active objective: Build fitness app (50%). 12 KV entries, 5 file entries."
}
```

---

## set_objective

Set a cognitive objective. Stays in permanent memory until completed. Auto-injected into every LLM prompt.

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `goal` | string | Yes | -- | The primary objective |
| `sub_goals` | array | No | [] | Sub-goals to track |
| `success_criteria` | array | No | [] | Criteria for completion |

### Returns

```json
{"objective": {"goal": "str", "sub_goals": [], "progress": 0.0}}
```

---

## get_context

Get the full cognitive context (objective + active context + recent decisions).

### Parameters

None.

### Returns

```json
{
  "objective": {"goal": "str", "progress": "50%", "context_tags": ["fitness", "app"]},
  "active_context": {"tech_stack": "React Native"},
  "recent_decisions": [{"action": "chose_framework", "relevance": "directly related"}]
}
```

---

## update_progress

Update progress toward the current cognitive objective.

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `progress` | number | Yes | -- | Progress from 0.0 to 1.0 |
| `completed_sub_goal` | string | No | -- | Sub-goal just completed |
| `complete` | boolean | No | false | Mark objective as fully completed |

---

## delete

Delete a key from a memory backend.

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `key` | string | Yes | -- | Key to delete |
| `backend` | string | No | `"kv"` | Backend to delete from |

---

## list_keys

List stored keys in a backend.

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `backend` | string | No | `"kv"` | Backend to list keys from |
| `prefix` | string | No | -- | Filter by key prefix |
| `limit` | integer | No | 100 | Max keys to return |

---

## clear

Clear all entries from a backend.

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `backend` | string | Yes | -- | Backend to clear |

---

## list_backends

List all registered memory backends.

### Parameters

None.
