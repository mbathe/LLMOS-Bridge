# Agent Spawn Module -- Action Reference

## spawn_agent

Create and launch an autonomous sub-agent with its own LLM loop.

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `name` | string | Yes | -- | Human-readable name for the sub-agent |
| `objective` | string | Yes | -- | The task for the sub-agent to accomplish |
| `system_prompt` | string | No | generic | System prompt defining the agent's role |
| `tools` | array | No | [] | Tools available (e.g. `["filesystem.read_file"]`) |
| `model` | string | No | parent's | LLM model to use |
| `provider` | string | No | parent's | LLM provider (anthropic/openai) |
| `max_turns` | integer | No | 15 | Maximum LLM turns |
| `context` | string | No | "" | Additional context (files, results, etc.) |

### Returns

```json
{"spawn_id": "abc123", "name": "analyzer", "status": "running"}
```

---

## check_agent

Check the current status of a spawned agent.

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `spawn_id` | string | Yes | The spawn_id from spawn_agent |

### Returns

```json
{"spawn_id": "abc123", "status": "running", "turns": 5, "elapsed_seconds": 12.3}
```

---

## get_result

Get the final result of a completed agent.

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `spawn_id` | string | Yes | The spawn_id from spawn_agent |

---

## list_agents

List all spawned agents.

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `status_filter` | string | No | "all" | Filter: running, completed, failed, cancelled, all |

---

## cancel_agent

Cancel a running agent.

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `spawn_id` | string | Yes | The spawn_id of the agent to cancel |

---

## wait_agent

Block until an agent completes or timeout.

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `spawn_id` | string | Yes | -- | Agent to wait for |
| `timeout` | number | No | 300 | Max seconds to wait |

---

## send_message

Send a message to a running agent.

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `spawn_id` | string | Yes | Target agent |
| `message` | string | Yes | Message content |
