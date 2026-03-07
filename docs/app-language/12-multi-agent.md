# Multi-Agent

Multi-agent apps use multiple LLM agents, each with their own brain, tools, and system prompt. An orchestration strategy coordinates their work.

## Defining Multiple Agents

Replace the `agent:` block with an `agents:` block containing a list of agents:

```yaml
agents:
  - id: planner
    role: coordinator
    brain:
      provider: anthropic
      model: claude-sonnet-4-20250514
      temperature: 0.3
    system_prompt: |
      You are a research planner. Break down questions into subtasks.
    tools: []

  - id: researcher
    role: specialist
    brain:
      provider: anthropic
      model: claude-sonnet-4-20250514
      temperature: 0.2
    system_prompt: |
      You are a research analyst. Summarize findings accurately.
    tools:
      - module: os_exec
        action: run_command

  - id: writer
    role: specialist
    brain:
      provider: anthropic
      model: claude-sonnet-4-20250514
      temperature: 0.4
      max_tokens: 8192
    system_prompt: |
      You are a technical writer. Create clear, structured reports.
    tools:
      - module: filesystem
        action: write_file
```

### Agent Fields (Multi-Agent)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string | Yes | Unique agent identifier |
| `role` | enum | No | `coordinator`, `specialist`, `reviewer`, `observer` |
| `expertise` | list | No | Areas of expertise |
| `preferred_node` | string | No | Preferred cluster node |
| `brain` | object | No | LLM configuration |
| `system_prompt` | string | No | Agent instructions |
| `tools` | list | No | Available tools |
| `loop` | object | No | Loop configuration |

### Agent Roles

| Role | Description |
|------|-------------|
| `coordinator` | Orchestrates other agents, delegates tasks, synthesizes results |
| `specialist` | Domain expert, focused on specific tools/tasks |
| `reviewer` | Reviews and validates output from other agents |
| `observer` | Monitors execution, logs metrics, doesn't intervene |

## Orchestration Strategies

```yaml
agents:
  strategy: hierarchical           # hierarchical | round_robin | consensus | pipeline
  communication:
    mode: orchestrated             # orchestrated | peer_to_peer | blackboard
  agents:
    - id: planner
      role: coordinator
      ...
```

### Hierarchical (Default)

The coordinator agent delegates tasks to specialists:

```
Coordinator (planner)
    ├── Specialist (researcher) → task 1
    ├── Specialist (researcher) → task 2
    └── Specialist (writer) → final synthesis
```

```yaml
agents:
  strategy: hierarchical
  agents:
    - id: planner
      role: coordinator
    - id: researcher
      role: specialist
    - id: writer
      role: specialist
```

### Round Robin

Agents take turns processing the input:

```
Input → Agent A → Agent B → Agent C → Output
         (turn 1)  (turn 2)  (turn 3)
```

```yaml
agents:
  strategy: round_robin
  agents:
    - id: analyst
    - id: critic
    - id: synthesizer
```

### Consensus

All agents process the same input, results are merged:

```
         ┌── Agent A ──┐
Input ───┤── Agent B ──├── Merge → Output
         └── Agent C ──┘
```

```yaml
agents:
  strategy: consensus
  agents:
    - id: optimist
    - id: pessimist
    - id: realist
```

### Pipeline

Agents process in sequence, each refining the previous output:

```
Input → Agent A → Agent B → Agent C → Output
        (draft)   (review)  (polish)
```

```yaml
agents:
  strategy: pipeline
  agents:
    - id: drafter
    - id: reviewer
    - id: editor
```

## Communication Modes

| Mode | Description |
|------|-------------|
| `orchestrated` | Coordinator controls all communication (default) |
| `peer_to_peer` | Agents communicate directly via message queues |
| `blackboard` | Shared state that all agents read/write in rounds |

### Peer-to-Peer (P2P)

In P2P mode, all agents run **concurrently**. Each agent gets its own `asyncio.Queue` for receiving messages. Agents can send messages to any other agent using the `send_message` builtin.

```yaml
agents:
  strategy: consensus              # Strategy is overridden by P2P mode
  communication:
    mode: peer_to_peer
  agents:
    - id: analyzer
      role: specialist
      tools:
        - builtin: send_message    # Optional — auto-injected if missing
        - module: filesystem
      system_prompt: |
        Analyze code and send findings to the reviewer:
        send_message(target="reviewer", message="Found issue: ...")

    - id: reviewer
      role: reviewer
      tools:
        - builtin: send_message
      system_prompt: |
        Review findings from the analyzer.
        send_message(target="analyzer", message="Please check ...")
```

**How it works:**

1. All agents start simultaneously via `asyncio.gather`
2. Each agent has its own message queue
3. `send_message(target="reviewer", message="...")` puts the message in the reviewer's queue
4. Messages are delivered asynchronously — agents don't block waiting for replies
5. Results from all agents are combined in the final output

> **Note:** P2P mode requires at least 2 agents. The compiler warns if only 1 agent is configured.

### Blackboard

In blackboard mode, agents share a common state dict and take turns in round-robin order. Each agent reads the blackboard, performs work, and updates it.

```yaml
agents:
  communication:
    mode: blackboard
  agents:
    - id: researcher
      system_prompt: "Research the topic. Previous findings: {{blackboard}}"
    - id: critic
      system_prompt: "Critique the findings. Previous work: {{blackboard}}"
    - id: synthesizer
      system_prompt: "Synthesize everything into a final answer."
```

**How it works:**

1. A shared blackboard dict is created: `{task, status, contributions}`
2. Agents run in round-robin order (researcher → critic → synthesizer)
3. Each agent sees the full blackboard state including all prior contributions
4. Continues for up to 3 rounds or until all agents succeed
5. Final output combines all agent contributions

## Using Agents in Flows

In multi-agent apps with explicit flows, reference agents by their ID:

```yaml
flow:
  - id: plan
    agent: planner
    input: "Break down: {{trigger.input}}"

  - id: research
    parallel:
      steps:
        - id: research_1
          agent: researcher
          input: "Research aspect 1: {{result.plan}}"
        - id: research_2
          agent: researcher
          input: "Research aspect 2: {{result.plan}}"

  - id: synthesize
    agent: writer
    input: |
      Compile findings:
      {{result.research_1}}
      {{result.research_2}}
```

## Delegation

In the agent loop (without explicit flows), agents can delegate to each other using the `delegate` builtin:

```yaml
agents:
  - id: lead
    role: coordinator
    tools:
      - builtin: delegate
    system_prompt: |
      You can delegate tasks to specialists:
      - delegate(agent_id="researcher", task="...")
      - delegate(agent_id="writer", task="...")
```

## Sub-Agent Spawning

For single-agent apps, use the `agent_spawn` module to create autonomous sub-agents at runtime:

```yaml
agent:
  tools:
    - module: agent_spawn
      action: spawn_agent
    - module: agent_spawn
      action: wait_agent
    - module: agent_spawn
      action: get_result
    - module: agent_spawn
      action: send_message
  system_prompt: |
    Spawn sub-agents for parallel work:
    - spawn_agent(name="analyzer", objective="...", tools=["filesystem.read_file"])
    - wait_agent(spawn_id="...")
    - get_result(spawn_id="...")
    - send_message(spawn_id="...", message="...")
```

Spawned agents are fully autonomous — they have their own LLM loop and run independently.

## Complete Multi-Agent Example

```yaml
app:
  name: research-agent
  version: "1.0"
  description: "Multi-agent research assistant"

variables:
  workspace: "{{env.PWD}}"
  output_dir: "{{workspace}}/research-output"

agents:
  - id: planner
    role: coordinator
    brain:
      provider: anthropic
      model: claude-sonnet-4-20250514
      temperature: 0.3
      max_tokens: 2048
    system_prompt: "Break down research questions into subtasks. Output JSON."
    tools: []

  - id: researcher
    role: specialist
    brain:
      provider: anthropic
      model: claude-sonnet-4-20250514
      temperature: 0.2
      max_tokens: 4096
    system_prompt: "Summarize search results accurately. Cite sources."
    tools:
      - module: os_exec
        action: run_command

  - id: writer
    role: specialist
    brain:
      provider: anthropic
      model: claude-sonnet-4-20250514
      temperature: 0.4
      max_tokens: 8192
    system_prompt: "Write clear, structured reports from research findings."
    tools:
      - module: filesystem
        action: write_file

flow:
  - id: plan
    agent: planner
    input: "Break down: {{trigger.input}}"

  - id: research
    parallel:
      max_concurrent: 3
      steps:
        - id: r1
          agent: researcher
          input: "Research overview: {{trigger.input}}"
        - id: r2
          agent: researcher
          input: "Research pitfalls: {{trigger.input}}"
        - id: r3
          agent: researcher
          input: "Research examples: {{trigger.input}}"

  - id: report
    agent: writer
    input: |
      Write report from:
      Overview: {{result.r1}}
      Pitfalls: {{result.r2}}
      Examples: {{result.r3}}

  - id: save
    action: filesystem.write_file
    params:
      path: "{{output_dir}}/report.md"
      content: "{{result.report}}"

triggers:
  - type: cli
    mode: one_shot
```
