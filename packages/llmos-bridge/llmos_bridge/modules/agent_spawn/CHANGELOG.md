# Changelog -- Agent Spawn Module

## [1.0.0] -- 2026-03-06

### Added
- Initial release with 7 actions.
- `spawn_agent` -- Create autonomous sub-agents with own LLM loop and tools.
- `check_agent` -- Poll agent status (running/completed/failed/cancelled).
- `get_result` -- Retrieve final output of completed agents.
- `list_agents` -- List all agents with optional status filter.
- `cancel_agent` -- Cancel running agents via asyncio task cancellation.
- `wait_agent` -- Block until agent completes with configurable timeout.
- `send_message` -- Inter-agent communication via message queue.
- `SpawnedAgentFactory` for runtime sub-agent creation with tool/LLM inheritance.
- Streaming support via event callbacks for real-time progress tracking.
