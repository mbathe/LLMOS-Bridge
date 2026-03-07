# Context Manager Module — Action Reference

## get_budget

Get the current context budget allocation.

### Parameters

None.

### Returns

```json
{
  "model_context_window": 200000,
  "output_reserved": 8192,
  "budget_breakdown": {
    "tools": 3500,
    "system_prompt": 1200,
    "cognitive_state": 400,
    "memory": 800,
    "conversation_history": {
      "budget": 186108,
      "used": 45000
    }
  },
  "total_used": 50900,
  "utilization": "25.5%",
  "compression_needed": false
}
```

---

## compress_history

Compress older conversation messages into a summary.

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `keep_last_n` | integer | No | 10 | Number of recent messages to keep uncompressed |

### Returns

```json
{
  "compressed": true,
  "messages_before": 45,
  "messages_after": 11,
  "tokens_saved": 12000,
  "summary": "Previous conversation covered..."
}
```

---

## fetch_context

Retrieve full details from compressed conversation segments.

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `query` | string | Yes | What to look for in compressed history |
| `segment_index` | integer | No | Specific segment (0 = most recent) |

### Returns

```json
{
  "found": true,
  "content": "...",
  "segment_count": 3
}
```

---

## get_tools_summary

Get a compact summary of available tools filtered by app permissions.

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `module_filter` | string | No | Only show tools from this module |

### Returns

```json
{
  "summary": "### filesystem\n  - read_file(path*:string) — Read file...",
  "module_count": 5,
  "action_count": 32
}
```

---

## get_state

Get the current context window state.

### Parameters

None.

### Returns

```json
{
  "budget": { ... },
  "compressions_total": 3,
  "compressions_recent": [...],
  "total_tokens_saved": 35000,
  "compressed_segments_available": 3
}
```
