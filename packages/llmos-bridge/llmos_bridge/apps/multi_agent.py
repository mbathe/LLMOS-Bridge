"""Multi-agent orchestrator for the LLMOS App Language.

Manages multiple agents defined in the `agents:` block of .app.yaml,
handling communication, delegation, and coordination strategies.

Strategies:
- round_robin: Agents take turns processing tasks
- hierarchical: Coordinator agent delegates to workers
- consensus: All agents run, results are merged
- pipeline: Agents process in sequence, each refining output
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

from .agent_runtime import AgentRunResult, AgentRuntime, LLMProvider
from .builtins import BuiltinToolExecutor
from .expression import ExpressionContext, ExpressionEngine
from .models import AgentConfig, CommunicationMode, MultiAgentConfig, MultiAgentStrategy
from .tool_registry import AppToolRegistry, ResolvedTool

logger = logging.getLogger(__name__)


@dataclass
class AgentInstance:
    """A configured agent ready to run."""
    config: AgentConfig
    llm: LLMProvider
    tools: list[ResolvedTool]


@dataclass
class MultiAgentResult:
    """Result from a multi-agent run."""
    success: bool
    output: str
    agent_results: dict[str, AgentRunResult] = field(default_factory=dict)
    coordinator_id: str = ""
    error: str | None = None


class MultiAgentOrchestrator:
    """Orchestrates multiple agents based on the multi-agent config.

    Each agent has its own AgentConfig (brain, prompt, tools, loop).
    The orchestrator manages their interaction patterns.
    """

    def __init__(
        self,
        config: MultiAgentConfig,
        agents: dict[str, AgentInstance],
        *,
        execute_tool: Callable[[str, str, dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
        expression_engine: ExpressionEngine | None = None,
        expression_context: ExpressionContext | None = None,
    ):
        self._config = config
        self._agents = agents
        self._execute_tool = execute_tool
        self._expr = expression_engine or ExpressionEngine()
        self._ctx = expression_context or ExpressionContext()

    async def run(self, input_text: str) -> MultiAgentResult:
        """Run the multi-agent system with the given input."""
        # Check communication mode first — P2P and blackboard override strategy
        comm_mode = self._config.communication.mode

        if comm_mode == CommunicationMode.peer_to_peer:
            return await self._run_peer_to_peer(input_text)
        elif comm_mode == CommunicationMode.blackboard:
            return await self._run_blackboard(input_text)

        # Orchestrated mode — dispatch by strategy
        strategy = self._config.strategy

        if strategy == MultiAgentStrategy.hierarchical:
            return await self._run_hierarchical(input_text)
        elif strategy == MultiAgentStrategy.round_robin:
            return await self._run_round_robin(input_text)
        elif strategy == MultiAgentStrategy.consensus:
            return await self._run_consensus(input_text)
        elif strategy == MultiAgentStrategy.pipeline:
            return await self._run_pipeline(input_text)
        else:
            return await self._run_hierarchical(input_text)

    async def _run_hierarchical(self, input_text: str) -> MultiAgentResult:
        """Hierarchical: coordinator delegates to workers via 'delegate' builtin."""
        agent_ids = list(self._agents.keys())
        if not agent_ids:
            return MultiAgentResult(success=False, output="", error="No agents configured")

        coordinator_id = agent_ids[0]
        coordinator = self._agents[coordinator_id]
        agent_results: dict[str, AgentRunResult] = {}

        # Create delegate handler that routes to other agents
        async def delegate_handler(target_agent_id: str, task: str) -> Any:
            if target_agent_id not in self._agents:
                return {"error": f"Unknown agent: {target_agent_id}"}
            target = self._agents[target_agent_id]
            result = await self._run_single_agent(target, task)
            agent_results[target_agent_id] = result
            return {"output": result.output, "success": result.success}

        builtins = BuiltinToolExecutor(delegate_handler=delegate_handler)

        runtime = AgentRuntime(
            agent_config=coordinator.config,
            llm=coordinator.llm,
            tools=coordinator.tools,
            execute_tool=self._execute_tool,
            builtin_executor=builtins,
            expression_engine=self._expr,
            expression_context=self._ctx,
        )

        coord_result = await runtime.run(input_text)
        agent_results[coordinator_id] = coord_result

        return MultiAgentResult(
            success=coord_result.success,
            output=coord_result.output,
            agent_results=agent_results,
            coordinator_id=coordinator_id,
        )

    async def _run_round_robin(self, input_text: str) -> MultiAgentResult:
        """Round robin: each agent processes in turn, passing context forward."""
        agent_results: dict[str, AgentRunResult] = {}
        current_input = input_text

        for agent_id, agent in self._agents.items():
            result = await self._run_single_agent(agent, current_input)
            agent_results[agent_id] = result
            if result.output:
                current_input = f"Previous agent ({agent_id}) output:\n{result.output}\n\nContinue the task: {input_text}"

        last_result = list(agent_results.values())[-1] if agent_results else None
        return MultiAgentResult(
            success=all(r.success for r in agent_results.values()),
            output=last_result.output if last_result else "",
            agent_results=agent_results,
        )

    async def _run_consensus(self, input_text: str) -> MultiAgentResult:
        """Consensus: all agents run truly in parallel, outputs are aggregated."""
        agent_ids = list(self._agents.keys())
        agent_list = list(self._agents.values())

        raw_results = await asyncio.gather(
            *(self._run_single_agent(agent, input_text) for agent in agent_list),
            return_exceptions=True,
        )

        agent_results: dict[str, AgentRunResult] = {}
        for i, r in enumerate(raw_results):
            aid = agent_ids[i]
            if isinstance(r, Exception):
                agent_results[aid] = AgentRunResult(
                    success=False, output="", turns=[], total_turns=0,
                    total_tokens=0, duration_ms=0, stop_reason="error",
                    error=str(r),
                )
            else:
                agent_results[aid] = r

        outputs = [f"[{aid}]: {r.output}" for aid, r in agent_results.items() if r.output]
        combined = "\n\n".join(outputs)

        return MultiAgentResult(
            success=any(r.success for r in agent_results.values()),
            output=combined,
            agent_results=agent_results,
        )

    async def _run_pipeline(self, input_text: str) -> MultiAgentResult:
        """Pipeline: agents process sequentially, each refining the output."""
        agent_results: dict[str, AgentRunResult] = {}
        current_input = input_text

        for agent_id, agent in self._agents.items():
            result = await self._run_single_agent(agent, current_input)
            agent_results[agent_id] = result
            if not result.success:
                return MultiAgentResult(
                    success=False,
                    output=result.output,
                    agent_results=agent_results,
                    error=f"Pipeline failed at agent '{agent_id}': {result.error}",
                )
            if result.output:
                current_input = result.output

        last_result = list(agent_results.values())[-1] if agent_results else None
        return MultiAgentResult(
            success=True,
            output=last_result.output if last_result else "",
            agent_results=agent_results,
        )

    async def _run_peer_to_peer(self, input_text: str) -> MultiAgentResult:
        """Peer-to-peer: all agents run concurrently with shared message queues.

        Each agent can send messages to any other agent via a 'send_message'
        builtin. Messages are delivered asynchronously via asyncio.Queue.
        Agents run in parallel and communicate directly without a coordinator.
        """
        agent_ids = list(self._agents.keys())
        if not agent_ids:
            return MultiAgentResult(success=False, output="", error="No agents configured")

        # Create a message queue for each agent
        queues: dict[str, asyncio.Queue] = {
            aid: asyncio.Queue() for aid in agent_ids
        }

        agent_results: dict[str, AgentRunResult] = {}

        async def run_p2p_agent(agent_id: str, agent: AgentInstance) -> AgentRunResult:
            # Create a send_message handler that routes to other agents' queues
            async def send_message(target_id: str, message: str) -> Any:
                if target_id not in queues:
                    return {"error": f"Unknown agent: {target_id}"}
                await queues[target_id].put({
                    "from": agent_id,
                    "content": message,
                })
                return {"sent": True, "to": target_id}

            builtins = BuiltinToolExecutor(send_message_handler=send_message)

            runtime = AgentRuntime(
                agent_config=agent.config,
                llm=agent.llm,
                tools=agent.tools,
                execute_tool=self._execute_tool,
                builtin_executor=builtins,
                expression_engine=self._expr,
                expression_context=self._isolated_context(),
                message_queue=queues[agent_id],
            )
            return await runtime.run(input_text)

        # Run all agents concurrently
        raw_results = await asyncio.gather(
            *(run_p2p_agent(aid, agent) for aid, agent in self._agents.items()),
            return_exceptions=True,
        )

        for i, r in enumerate(raw_results):
            aid = agent_ids[i]
            if isinstance(r, Exception):
                agent_results[aid] = AgentRunResult(
                    success=False, output="", turns=[], total_turns=0,
                    total_tokens=0, duration_ms=0, stop_reason="error",
                    error=str(r),
                )
            else:
                agent_results[aid] = r

        # Combine outputs from all agents
        outputs = [f"[{aid}]: {r.output}" for aid, r in agent_results.items() if r.output]
        combined = "\n\n".join(outputs)

        return MultiAgentResult(
            success=any(r.success for r in agent_results.values()),
            output=combined,
            agent_results=agent_results,
        )

    async def _run_blackboard(self, input_text: str) -> MultiAgentResult:
        """Blackboard: agents share a common state (blackboard) and take turns.

        The blackboard is a shared dict that all agents can read/write.
        Agents run in round-robin order, each reading the blackboard,
        performing work, and updating it. Continues until all agents
        report completion or max rounds are reached.
        """
        agent_ids = list(self._agents.keys())
        if not agent_ids:
            return MultiAgentResult(success=False, output="", error="No agents configured")

        blackboard: dict[str, Any] = {
            "task": input_text,
            "status": "in_progress",
            "contributions": {},
        }
        agent_results: dict[str, AgentRunResult] = {}
        max_rounds = 3  # Prevent infinite loops

        for round_num in range(max_rounds):
            all_done = True
            for agent_id, agent in self._agents.items():
                # Provide blackboard state as context
                bb_context = (
                    f"[Blackboard State - Round {round_num + 1}]\n"
                    f"Task: {blackboard['task']}\n"
                    f"Status: {blackboard['status']}\n"
                    f"Contributions so far: {json.dumps(blackboard['contributions'], default=str)}\n\n"
                    f"Your task: {input_text}"
                )

                result = await self._run_single_agent(agent, bb_context)
                agent_results[agent_id] = result

                # Update blackboard with agent's contribution
                blackboard["contributions"][agent_id] = {
                    "round": round_num + 1,
                    "output": result.output[:500] if result.output else "",
                    "success": result.success,
                }

                if not result.success:
                    all_done = False

            # Check if all agents succeeded this round
            if all_done:
                blackboard["status"] = "complete"
                break

        # Build final output from all contributions
        final_parts = []
        for aid, contribution in blackboard["contributions"].items():
            if contribution.get("output"):
                final_parts.append(f"[{aid}]: {contribution['output']}")

        return MultiAgentResult(
            success=blackboard["status"] == "complete",
            output="\n\n".join(final_parts),
            agent_results=agent_results,
        )

    def _isolated_context(self) -> ExpressionContext:
        """Create an isolated copy of the expression context for an agent.

        Each agent gets its own results/variables dict so concurrent agents
        (consensus strategy) don't corrupt each other's state.
        """
        return ExpressionContext(
            variables=dict(self._ctx.variables),
            results=dict(self._ctx.results),
            trigger=dict(self._ctx.trigger),
            memory=dict(self._ctx.memory),
            secrets=dict(self._ctx.secrets),
            agent=dict(self._ctx.agent),
            run=dict(self._ctx.run),
            app=dict(self._ctx.app),
            loop=dict(self._ctx.loop),
            extra=dict(self._ctx.extra),
        )

    async def _run_single_agent(self, agent: AgentInstance, input_text: str) -> AgentRunResult:
        """Run a single agent with an isolated expression context."""
        runtime = AgentRuntime(
            agent_config=agent.config,
            llm=agent.llm,
            tools=agent.tools,
            execute_tool=self._execute_tool,
            expression_engine=self._expr,
            expression_context=self._isolated_context(),
        )
        return await runtime.run(input_text)
