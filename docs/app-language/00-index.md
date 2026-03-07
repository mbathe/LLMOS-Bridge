# LLMOS App Language Reference

The LLMOS App Language is a declarative YAML-based language for building AI applications. Define agents, tools, memory, flows, triggers, and security — all without writing a single line of code.

> **Two Execution Modes**: The App Language is the **Agentique Mode** — the LLM decides what to do autonomously. LLMOS Bridge also provides a **Compiler Mode** via the [IML Protocol](../protocol/iml-protocol.md), where you define the exact execution plan as structured JSON. Both modes share the same 18+ modules (284 actions), security pipeline, event bus, and identity system. Use YAML Apps for autonomous agents; use IML for deterministic pipelines. See the [Architecture Overview](../overview/architecture.md#two-execution-modes) for a detailed comparison.

## Documentation

| # | Guide | Description |
|---|-------|-------------|
| 1 | [Getting Started](01-getting-started.md) | Installation, first app, running |
| 2 | [App Configuration](02-app-config.md) | `app:` block, variables, metadata, interface, `module_config` |
| 3 | [Agents](03-agents.md) | Agent definition, brain, system prompt, loop |
| 4 | [Tools](04-tools.md) | Module tools, builtins, constraints |
| 5 | [Memory](05-memory.md) | Working, conversation, episodic, project, procedural |
| 6 | [Context Management](06-context-management.md) | Token budget, compression, on-demand fetch |
| 7 | [Flows](07-flows.md) | Explicit flows, 18 step types, branching, loops, parallel |
| 8 | [Macros](08-macros.md) | Reusable flow snippets with parameters |
| 9 | [Triggers](09-triggers.md) | CLI, HTTP, schedule, webhook, watch, event |
| 10 | [Expressions](10-expressions.md) | Template syntax `{{}}`, filters, operators |
| 11 | [Security](11-security.md) | Profiles, sandbox, capabilities, approvals, audit |
| 12 | [Multi-Agent](12-multi-agent.md) | Multi-agent orchestration, strategies, communication |
| 13 | [Observability](13-observability.md) | Streaming, logging, tracing, metrics |
| 14 | [API Integration](14-api-integration.md) | Daemon API, app store, running via REST |
| 15 | [Examples](15-examples.md) | Complete real-world application examples |

## Quick Example

```yaml
app:
  name: my-assistant
  version: "1.0"
  description: "A simple AI assistant"

agent:
  brain:
    provider: anthropic
    model: claude-sonnet-4-20250514
  system_prompt: |
    You are a helpful coding assistant.
    Workspace: {{workspace}}
  tools:
    - module: filesystem
      action: read_file
    - module: filesystem
      action: write_file
    - module: os_exec
      action: run_command

variables:
  workspace: "{{env.PWD}}"

triggers:
  - type: cli
    mode: conversation
    greeting: "Hello! How can I help?"

security:
  profile: power_user
```

Run it:

```bash
llmos app run my-assistant.app.yaml
```

## Architecture Overview

```
.app.yaml file
     |
     v
 AppCompiler          Parse YAML, validate schema, check semantics
     |
     v
 AppDefinition        Pydantic model tree (typed, validated)
     |
     v
 AppRuntime           Wire agent, tools, memory, triggers
     |
     +--> AgentRuntime     LLM loop (reactive/single_shot/continuous)
     +--> FlowExecutor     Explicit flow engine (18 step types)
     +--> MemoryManager    Multi-level memory (working/conversation/episodic/project)
     +--> ToolRegistry     Module actions + builtins resolved to LLM tool schemas
     +--> TriggerManager   CLI/HTTP/schedule/webhook/watch/event
     |
     In daemon mode:
     +--> DaemonToolExecutor   Routes through security pipeline + all modules
     +--> Application Identity Auto-created RBAC entity with linked security
     +--> TriggerManager       Background triggers auto-started on "running" status
```

## File Convention

App files use the `.app.yaml` extension:

```
my-app.app.yaml
code-reviewer.app.yaml
research-agent.app.yaml
```
