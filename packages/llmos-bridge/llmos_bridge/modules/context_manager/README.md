# Context Manager Module

Intelligent LLM context window management for LLMOS Bridge.

## Problem

LLM context windows are finite. When an agent has 18 modules, memory, objectives, conversation history, and tool schemas loaded, the context explodes. Without management, the LLM either runs out of context or loses awareness of critical state.

## Solution: Hybrid Approach

1. **Objectives are NEVER forgotten** — cognitive state (goals, progress, decisions) is always preserved at full fidelity
2. **Conversation history is compressible** — older messages are summarized by a fast LLM
3. **On-demand fetch** — when context was compressed, the LLM can fetch full details about any topic
4. **Application-aware** — tool summaries respect the Application identity's allowed_modules/allowed_actions
5. **Token budget** — a global allocator distributes tokens across system prompt, cognitive state, memory, tools, and history

## Actions

| Action | Description |
|--------|------------|
| `get_budget` | Current context budget allocation |
| `compress_history` | Summarize older messages via LLM |
| `fetch_context` | Retrieve full details from compressed segments |
| `get_tools_summary` | Compact tool listing filtered by app permissions |
| `get_state` | Context window state and compression history |

## YAML Usage

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

memory:
  context:
    model_context_window: 200000
    output_reserved: 8192
    cognitive_max_tokens: 1500
    compression_trigger_ratio: 0.75
```

## Architecture

The module operates at two levels:

1. **Runtime level** — `compute_budget()`, `bound_cognitive_text()`, `compress_messages()` are called by AgentRuntime automatically
2. **Tool level** — The LLM itself can call `get_budget`, `compress_history`, `fetch_context` when it needs to manage its own context
