# Agent Spawn Module -- Integration Guide

## App Language (YAML)

Add agent_spawn actions to the agent's tool list:

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
      action: list_agents
    - module: agent_spawn
      action: cancel_agent
    - module: agent_spawn
      action: wait_agent
    - module: agent_spawn
      action: send_message
```

## Daemon vs Standalone

In daemon mode, the module is registered in `server.py` with the full module
registry. In standalone CLI mode (`llmos app run`), the module is created by
the `SpawnedAgentFactory` wired into the app runtime.

## Sub-Agent Tool Inheritance

When spawning a sub-agent, the `tools` param accepts a list of
`"module.action"` strings. The child agent gets access only to those tools.
If empty, the child inherits all tools from the parent.

## Streaming Events

If an event callback is set via `set_event_callback()`, the module emits
streaming events for each sub-agent turn, enabling real-time monitoring
of sub-agent progress from the parent.
