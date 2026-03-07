"""Agent Spawn module — dynamic sub-agent creation, execution, and monitoring.

This system module allows any agent to:
  1. spawn_agent    — Create and launch an autonomous sub-agent with its own
                      system prompt, tools, objectives, and LLM. Runs in parallel.
  2. check_agent    — Check the status of a spawned agent (running/completed/failed).
  3. get_result     — Retrieve the final output of a completed agent.
  4. list_agents    — List all spawned agents and their statuses.
  5. cancel_agent   — Cancel a running agent.
  6. wait_agent     — Block until a specific agent completes (with timeout).
  7. send_message   — Send a message to a running agent (inter-agent communication).

Sub-agents are fully autonomous — they have their own LLM loop, tool access,
and conversation history. They execute in parallel via asyncio tasks.

Architecture:
    Parent Agent (via AgentRuntime)
      └─ calls agent_spawn.spawn_agent(...)
           └─ AgentSpawnModule creates an AgentRuntime for the child
           └─ Launches it as an asyncio.Task
           └─ Returns spawn_id immediately
      └─ calls agent_spawn.check_agent(spawn_id)
           └─ Returns status, turn count, elapsed time
      └─ calls agent_spawn.get_result(spawn_id)
           └─ Returns final output when complete
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Awaitable

from llmos_bridge.modules.base import BaseModule, Platform
from llmos_bridge.modules.manifest import ActionSpec, ModuleManifest, ParamSpec

logger = logging.getLogger(__name__)


class SpawnStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class SpawnedAgent:
    """Tracks a spawned sub-agent."""
    spawn_id: str
    name: str
    objective: str
    system_prompt: str
    tools: list[str]          # ["filesystem.read_file", "os_exec.run_command"]
    model: str
    provider: str
    status: SpawnStatus = SpawnStatus.RUNNING
    task: asyncio.Task | None = None
    result: str = ""
    error: str = ""
    turns: int = 0
    start_time: float = 0.0
    end_time: float = 0.0
    message_queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    events: list[dict[str, Any]] = field(default_factory=list)  # captured stream events


class AgentSpawnModule(BaseModule):
    """System module for dynamic sub-agent creation and management.

    When integrated with the YAML App Language, a parent agent can use this
    module as a tool to create sub-agents on the fly:

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
    """

    MODULE_ID = "agent_spawn"
    VERSION = "1.0.0"
    SUPPORTED_PLATFORMS = [Platform.ALL]
    MODULE_TYPE = "system"

    def __init__(self) -> None:
        super().__init__()
        self._spawned: dict[str, SpawnedAgent] = {}
        self._agent_factory: Callable[..., Awaitable[dict[str, Any]]] | None = None
        self._execute_tool: Callable[[str, str, dict], Awaitable[dict]] | None = None
        self._event_callback: Callable | None = None
        self._kv_store: Any = None
        self._max_concurrent = 10
        self._semaphore = asyncio.Semaphore(self._max_concurrent)

    def set_agent_factory(
        self,
        factory: Callable[..., Awaitable[dict[str, Any]]],
    ) -> None:
        """Inject the agent runner factory.

        The factory signature:
            async def run_agent(
                system_prompt: str,
                input_text: str,
                tools: list[str],
                model: str,
                provider: str,
                max_turns: int,
                execute_tool: Callable,
            ) -> dict[str, Any]

        Returns: {"success": bool, "output": str, "turns": int, "error": str | None}
        """
        self._agent_factory = factory

    def set_execute_tool(
        self, execute_tool: Callable[[str, str, dict], Awaitable[dict]]
    ) -> None:
        """Inject the tool executor (shared with parent agent)."""
        self._execute_tool = execute_tool

    def set_event_callback(
        self, callback: Callable[[str, Any], Awaitable[None]]
    ) -> None:
        """Inject callback for sub-agent streaming events.

        Signature: async def callback(spawn_id: str, event: StreamEvent) -> None
        """
        self._event_callback = callback

    def set_kv_store(self, kv_store: Any) -> None:
        """Inject KV store for persisting agent results across restarts."""
        self._kv_store = kv_store

    def get_manifest(self) -> ModuleManifest:
        return ModuleManifest(
            module_id=self.MODULE_ID,
            version=self.VERSION,
            description=(
                "Dynamic sub-agent creation and management. "
                "Spawn autonomous AI agents that run in parallel, "
                "each with their own objectives, tools, and LLM configuration. "
                "Monitor their progress, retrieve results, or cancel them."
            ),
            actions=[
                ActionSpec(
                    name="spawn_agent",
                    description=(
                        "Create and launch an autonomous sub-agent. "
                        "The agent runs in parallel with its own LLM loop, tools, and objectives. "
                        "Returns a spawn_id to track the agent."
                    ),
                    params=[
                        ParamSpec(name="name", type="string",
                                  description="Human-readable name for the sub-agent",
                                  required=True),
                        ParamSpec(name="objective", type="string",
                                  description="The task/objective for the sub-agent to accomplish",
                                  required=True),
                        ParamSpec(name="system_prompt", type="string",
                                  description="System prompt that defines the agent's role and behavior",
                                  required=False,
                                  default="You are an autonomous AI agent. Complete the given objective."),
                        ParamSpec(name="tools", type="array",
                                  description='List of tools available to the agent (e.g. ["filesystem.read_file", "os_exec.run_command"])',
                                  required=False, default=[]),
                        ParamSpec(name="model", type="string",
                                  description="LLM model to use (default: same as parent)",
                                  required=False, default=""),
                        ParamSpec(name="provider", type="string",
                                  description="LLM provider (anthropic/openai, default: same as parent)",
                                  required=False, default=""),
                        ParamSpec(name="max_turns", type="integer",
                                  description="Maximum turns for the agent loop (default: 15)",
                                  required=False, default=15),
                        ParamSpec(name="context", type="string",
                                  description="Additional context to pass to the agent (files read, previous results, etc.)",
                                  required=False, default=""),
                    ],
                    returns="object",
                    returns_description='{"spawn_id": "...", "name": "...", "status": "running"}',
                    tags=["agent", "orchestration", "parallel"],
                    side_effects=["process_spawn"],
                ),
                ActionSpec(
                    name="check_agent",
                    description="Check the current status of a spawned agent.",
                    params=[
                        ParamSpec(name="spawn_id", type="string",
                                  description="The spawn_id returned by spawn_agent",
                                  required=True),
                    ],
                    returns="object",
                    returns_description='{"spawn_id": "...", "status": "running|completed|failed|cancelled", "turns": N, "elapsed_seconds": N}',
                    tags=["agent", "monitoring"],
                ),
                ActionSpec(
                    name="get_result",
                    description="Get the final result/output of a completed agent. Returns error if agent is still running.",
                    params=[
                        ParamSpec(name="spawn_id", type="string",
                                  description="The spawn_id returned by spawn_agent",
                                  required=True),
                    ],
                    returns="object",
                    returns_description='{"spawn_id": "...", "status": "completed", "output": "...", "turns": N}',
                    tags=["agent", "results"],
                ),
                ActionSpec(
                    name="list_agents",
                    description="List all spawned agents and their current statuses.",
                    params=[
                        ParamSpec(name="status_filter", type="string",
                                  description="Filter by status: running, completed, failed, cancelled, or all (default: all)",
                                  required=False, default="all",
                                  enum=["all", "running", "completed", "failed", "cancelled"]),
                    ],
                    returns="object",
                    returns_description='{"agents": [{"spawn_id": "...", "name": "...", "status": "...", ...}], "total": N}',
                    tags=["agent", "monitoring"],
                ),
                ActionSpec(
                    name="cancel_agent",
                    description="Cancel a running agent. No effect if already completed.",
                    params=[
                        ParamSpec(name="spawn_id", type="string",
                                  description="The spawn_id of the agent to cancel",
                                  required=True),
                    ],
                    returns="object",
                    returns_description='{"spawn_id": "...", "cancelled": true}',
                    tags=["agent", "control"],
                ),
                ActionSpec(
                    name="wait_agent",
                    description="Wait for a spawned agent to complete. Blocks until done or timeout.",
                    params=[
                        ParamSpec(name="spawn_id", type="string",
                                  description="The spawn_id of the agent to wait for",
                                  required=True),
                        ParamSpec(name="timeout", type="number",
                                  description="Maximum seconds to wait (default: 300)",
                                  required=False, default=300),
                    ],
                    returns="object",
                    returns_description='{"spawn_id": "...", "status": "completed", "output": "...", "turns": N}',
                    tags=["agent", "synchronization"],
                ),
                ActionSpec(
                    name="send_message",
                    description="Send a message to a running agent. The message is added to the agent's context.",
                    params=[
                        ParamSpec(name="spawn_id", type="string",
                                  description="The spawn_id of the target agent",
                                  required=True),
                        ParamSpec(name="message", type="string",
                                  description="Message to send to the agent",
                                  required=True),
                    ],
                    returns="object",
                    returns_description='{"delivered": true, "queue_size": N}',
                    tags=["agent", "communication"],
                ),
            ],
            platforms=["all"],
            tags=["agent", "orchestration", "parallel", "system"],
        )

    # ─── Action implementations ──────────────────────────────────────

    async def _action_spawn_agent(self, params: dict[str, Any]) -> dict[str, Any]:
        """Create and launch an autonomous sub-agent."""
        if self._agent_factory is None:
            return {"error": "Agent factory not configured. Cannot spawn agents."}

        name = params.get("name", "unnamed")
        objective = params.get("objective", "")
        system_prompt = params.get("system_prompt", "You are an autonomous AI agent. Complete the given objective.")
        tools = params.get("tools", [])
        model = params.get("model", "")
        provider = params.get("provider", "")
        max_turns = int(params.get("max_turns", 15))
        context = params.get("context", "")

        if not objective:
            return {"error": "objective is required"}

        # Build the full input for the sub-agent
        agent_input = f"## Objective\n{objective}"
        if context:
            agent_input += f"\n\n## Context\n{context}"

        spawn_id = f"agent-{uuid.uuid4().hex[:8]}"
        spawned = SpawnedAgent(
            spawn_id=spawn_id,
            name=name,
            objective=objective,
            system_prompt=system_prompt,
            tools=tools if isinstance(tools, list) else [tools],
            model=model,
            provider=provider,
            start_time=time.time(),
        )
        self._spawned[spawn_id] = spawned

        # Launch the agent as a background task
        task = asyncio.create_task(
            self._run_spawned_agent(spawned, agent_input, max_turns),
            name=f"spawn-{spawn_id}",
        )
        spawned.task = task

        logger.info("Spawned agent %s (%s): %s", spawn_id, name, objective[:100])

        return {
            "spawn_id": spawn_id,
            "name": name,
            "status": "running",
            "message": f"Agent '{name}' launched. Use check_agent or wait_agent to monitor progress.",
        }

    async def _action_check_agent(self, params: dict[str, Any]) -> dict[str, Any]:
        """Check the status of a spawned agent."""
        spawn_id = params.get("spawn_id", "")
        spawned = self._spawned.get(spawn_id)
        if spawned is None:
            return {"error": f"No agent found with spawn_id '{spawn_id}'"}

        elapsed = time.time() - spawned.start_time
        result: dict[str, Any] = {
            "spawn_id": spawn_id,
            "name": spawned.name,
            "objective": spawned.objective,
            "status": spawned.status.value,
            "turns": spawned.turns,
            "elapsed_seconds": round(elapsed, 1),
        }

        if spawned.status == SpawnStatus.COMPLETED:
            result["output_preview"] = spawned.result[:500] if spawned.result else ""
        elif spawned.status == SpawnStatus.FAILED:
            result["error"] = spawned.error

        return result

    async def _action_get_result(self, params: dict[str, Any]) -> dict[str, Any]:
        """Get the final result of a completed agent."""
        spawn_id = params.get("spawn_id", "")
        spawned = self._spawned.get(spawn_id)
        if spawned is None:
            # Try loading from persisted history
            persisted = await self._load_persisted_result(spawn_id)
            if persisted:
                return persisted
            return {"error": f"No agent found with spawn_id '{spawn_id}'"}

        if spawned.status == SpawnStatus.RUNNING:
            return {
                "spawn_id": spawn_id,
                "status": "running",
                "message": "Agent is still running. Use wait_agent to block until complete, or check_agent to poll.",
            }

        elapsed = spawned.end_time - spawned.start_time if spawned.end_time else 0
        result: dict[str, Any] = {
            "spawn_id": spawn_id,
            "name": spawned.name,
            "status": spawned.status.value,
            "turns": spawned.turns,
            "duration_seconds": round(elapsed, 1),
        }

        if spawned.status == SpawnStatus.COMPLETED:
            result["output"] = spawned.result
        elif spawned.status == SpawnStatus.FAILED:
            result["error"] = spawned.error

        return result

    async def _action_list_agents(self, params: dict[str, Any]) -> dict[str, Any]:
        """List all spawned agents."""
        status_filter = params.get("status_filter", "all")

        agents = []
        for s in self._spawned.values():
            if status_filter != "all" and s.status.value != status_filter:
                continue
            elapsed = time.time() - s.start_time
            agents.append({
                "spawn_id": s.spawn_id,
                "name": s.name,
                "objective": s.objective[:200],
                "status": s.status.value,
                "turns": s.turns,
                "elapsed_seconds": round(elapsed, 1),
                "tools": s.tools,
            })

        return {
            "agents": agents,
            "total": len(agents),
            "running": sum(1 for a in agents if a["status"] == "running"),
            "completed": sum(1 for a in agents if a["status"] == "completed"),
            "failed": sum(1 for a in agents if a["status"] == "failed"),
        }

    async def _action_cancel_agent(self, params: dict[str, Any]) -> dict[str, Any]:
        """Cancel a running agent."""
        spawn_id = params.get("spawn_id", "")
        spawned = self._spawned.get(spawn_id)
        if spawned is None:
            return {"error": f"No agent found with spawn_id '{spawn_id}'"}

        if spawned.status != SpawnStatus.RUNNING:
            return {
                "spawn_id": spawn_id,
                "cancelled": False,
                "reason": f"Agent is already {spawned.status.value}",
            }

        if spawned.task and not spawned.task.done():
            spawned.task.cancel()

        spawned.status = SpawnStatus.CANCELLED
        spawned.end_time = time.time()

        return {"spawn_id": spawn_id, "cancelled": True}

    async def _action_wait_agent(self, params: dict[str, Any]) -> dict[str, Any]:
        """Wait for an agent to complete."""
        spawn_id = params.get("spawn_id", "")
        timeout = float(params.get("timeout", 300))
        spawned = self._spawned.get(spawn_id)
        if spawned is None:
            return {"error": f"No agent found with spawn_id '{spawn_id}'"}

        if spawned.status != SpawnStatus.RUNNING:
            # Already done
            return await self._action_get_result({"spawn_id": spawn_id})

        if spawned.task is None:
            return {"error": "Agent task not found"}

        try:
            await asyncio.wait_for(asyncio.shield(spawned.task), timeout=timeout)
        except asyncio.TimeoutError:
            return {
                "spawn_id": spawn_id,
                "status": "running",
                "message": f"Agent still running after {timeout}s timeout. It continues in the background.",
                "turns": spawned.turns,
            }
        except asyncio.CancelledError:
            pass

        return await self._action_get_result({"spawn_id": spawn_id})

    async def _action_send_message(self, params: dict[str, Any]) -> dict[str, Any]:
        """Send a message to a running agent. Delivered into the agent's LLM context between turns."""
        spawn_id = params.get("spawn_id", "")
        message = params.get("message", "")
        spawned = self._spawned.get(spawn_id)
        if spawned is None:
            return {"error": f"No agent found with spawn_id '{spawn_id}'"}

        if spawned.status != SpawnStatus.RUNNING:
            return {
                "delivered": False,
                "reason": f"Agent is {spawned.status.value}, cannot receive messages",
            }

        msg = {
            "from": "parent",
            "content": message,
            "timestamp": time.time(),
        }
        await spawned.message_queue.put(msg)

        return {
            "delivered": True,
            "queue_size": spawned.message_queue.qsize(),
        }

    # ─── Internal: run a spawned agent ───────────────────────────────

    async def _run_spawned_agent(
        self, spawned: SpawnedAgent, input_text: str, max_turns: int
    ) -> None:
        """Execute the spawned agent's loop in a background task."""
        async with self._semaphore:
            try:
                # Event callback captures sub-agent streaming events
                async def _on_event(event: dict) -> None:
                    spawned.events.append({
                        "type": getattr(event, "type", str(event)),
                        "data": getattr(event, "data", {}),
                        "timestamp": time.time(),
                    })
                    # Propagate to parent via EventBus if wired
                    if self._event_callback:
                        import contextlib
                        with contextlib.suppress(Exception):
                            await self._event_callback(spawned.spawn_id, event)

                result = await self._agent_factory(
                    system_prompt=spawned.system_prompt,
                    input_text=input_text,
                    tools=spawned.tools,
                    model=spawned.model,
                    provider=spawned.provider,
                    max_turns=max_turns,
                    execute_tool=self._execute_tool,
                    message_queue=spawned.message_queue,
                    event_callback=_on_event,
                )

                spawned.turns = result.get("turns", 0)
                spawned.end_time = time.time()

                if result.get("success", False):
                    spawned.status = SpawnStatus.COMPLETED
                    spawned.result = result.get("output", "")
                else:
                    spawned.status = SpawnStatus.FAILED
                    spawned.error = result.get("error", "Agent failed without details")

                # Persist result to KV store for history
                await self._persist_result(spawned)

            except asyncio.CancelledError:
                spawned.status = SpawnStatus.CANCELLED
                spawned.end_time = time.time()
                raise

            except Exception as e:
                logger.exception("Spawned agent %s failed: %s", spawned.spawn_id, e)
                spawned.status = SpawnStatus.FAILED
                spawned.error = str(e)
                spawned.end_time = time.time()

    # ─── Persistence ──────────────────────────────────────────────────

    async def _persist_result(self, spawned: SpawnedAgent) -> None:
        """Save completed agent result to KV store for history."""
        if self._kv_store is None:
            return
        try:
            import json
            record = {
                "spawn_id": spawned.spawn_id,
                "name": spawned.name,
                "objective": spawned.objective,
                "status": spawned.status.value,
                "result": spawned.result[:10000],  # cap at 10k chars
                "error": spawned.error,
                "turns": spawned.turns,
                "start_time": spawned.start_time,
                "end_time": spawned.end_time,
                "duration_seconds": round(spawned.end_time - spawned.start_time, 1),
            }
            # Store individual result
            key = f"llmos:agent_spawn:result:{spawned.spawn_id}"
            await self._kv_store.set(key, json.dumps(record))

            # Append to history index
            history_key = "llmos:agent_spawn:history"
            raw = await self._kv_store.get(history_key)
            history = json.loads(raw) if raw else []
            history.append(spawned.spawn_id)
            # Keep last 100 entries
            if len(history) > 100:
                history = history[-100:]
            await self._kv_store.set(history_key, json.dumps(history))
        except Exception as e:
            logger.debug("Could not persist agent result: %s", e)

    async def _load_persisted_result(self, spawn_id: str) -> dict[str, Any] | None:
        """Load a persisted agent result from KV store."""
        if self._kv_store is None:
            return None
        try:
            import json
            key = f"llmos:agent_spawn:result:{spawn_id}"
            raw = await self._kv_store.get(key)
            if raw:
                return json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            pass
        return None

    # ─── Lifecycle ───────────────────────────────────────────────────

    async def on_stop(self) -> None:
        """Cancel all running agents on module shutdown."""
        for spawned in self._spawned.values():
            if spawned.status == SpawnStatus.RUNNING and spawned.task:
                spawned.task.cancel()
        self._spawned.clear()
