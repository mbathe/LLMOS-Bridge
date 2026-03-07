# Agent Spawn Module

Dynamic sub-agent creation, execution, and monitoring for multi-agent workflows.

## Overview

The Agent Spawn module enables any agent to create autonomous sub-agents that
run in parallel. Each sub-agent gets its own LLM loop, tool access, system prompt,
and conversation history. Sub-agents are fully independent asyncio tasks.

Use cases:
- Analyze multiple files simultaneously
- Run tests while implementing fixes
- Research and code in parallel
- Divide complex tasks across specialized agents

## Actions

| Action | Description | Risk | Permission |
|--------|-------------|------|------------|
| `spawn_agent` | Create and launch a sub-agent with its own LLM | Medium | `agent.spawn` |
| `check_agent` | Check status of a spawned agent | Low | `agent.monitor` |
| `get_result` | Get final output of a completed agent | Low | `agent.monitor` |
| `list_agents` | List all agents and statuses | Low | `agent.monitor` |
| `cancel_agent` | Cancel a running agent | Medium | `agent.control` |
| `wait_agent` | Block until agent completes (with timeout) | Low | `agent.monitor` |
| `send_message` | Send message to a running agent | Low | `agent.control` |

## Quick Start

```yaml
agent:
  tools:
    - module: agent_spawn
      action: spawn_agent
    - module: agent_spawn
      action: check_agent
    - module: agent_spawn
      action: get_result
    - module: agent_spawn
      action: wait_agent
```

## Architecture

```
Parent Agent (AgentRuntime)
  └── spawn_agent(name="analyzer", objective="...")
       └── AgentSpawnModule creates child AgentRuntime
       └── Launches as asyncio.Task
       └── Returns spawn_id immediately
  └── wait_agent(spawn_id) / check_agent(spawn_id)
       └── Blocks or polls until child completes
  └── get_result(spawn_id)
       └── Returns final output
```

## Requirements

No external dependencies. Uses the app runtime's LLM provider factory.

## Platform Support

| Platform | Status |
|----------|--------|
| Linux | Supported |
| macOS | Supported |
| Windows | Supported |

## Related Modules

- **memory** — Agents can share state via the memory module
- **filesystem** — Sub-agents often need file access
- **os_exec** — Sub-agents may run commands
