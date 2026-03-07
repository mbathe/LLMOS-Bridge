"""Agent factory — creates real AgentRuntime instances for spawned agents.

This bridges the agent_spawn module to the apps/ layer, enabling
spawned agents to use the same LLM providers, tool execution pipeline,
and security layers as the parent agent.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Awaitable

logger = logging.getLogger(__name__)


class SpawnedAgentFactory:
    """Creates and runs AgentRuntime instances for spawned sub-agents.

    Receives the same LLM factory and tool executor as the parent runtime,
    ensuring sub-agents go through the full security pipeline.
    """

    def __init__(
        self,
        llm_factory: Callable | None = None,
        execute_tool: Callable[[str, str, dict], Awaitable[dict]] | None = None,
    ):
        self._llm_factory = llm_factory
        self._execute_tool = execute_tool

    async def run_agent(
        self,
        *,
        system_prompt: str,
        input_text: str,
        tools: list[str],
        model: str,
        provider: str,
        max_turns: int,
        execute_tool: Callable | None = None,
        message_queue: Any = None,
        event_callback: Callable | None = None,
    ) -> dict[str, Any]:
        """Create and run a complete agent loop, returning the result.

        This is the callback passed to AgentSpawnModule.set_agent_factory().
        """
        from llmos_bridge.apps.agent_runtime import AgentRuntime, LLMProvider
        from llmos_bridge.apps.models import (
            AgentConfig,
            BrainConfig,
            ContextConfig,
            LoopConfig,
            ToolDefinition,
        )
        from llmos_bridge.apps.tool_registry import AppToolRegistry, ResolvedTool

        # Determine which tool executor to use
        tool_executor = execute_tool or self._execute_tool

        # Build BrainConfig
        brain = BrainConfig(
            provider=provider or "anthropic",
            model=model or "claude-sonnet-4-20250514",
        )

        # Create LLM provider
        llm: LLMProvider | None = None
        if self._llm_factory:
            llm = self._llm_factory(brain)

        if llm is None:
            return {
                "success": False,
                "output": "",
                "turns": 0,
                "error": "No LLM provider available for spawned agent",
            }

        # Build tool definitions from the tools list
        tool_defs: list[ToolDefinition] = []
        for tool_spec in tools:
            if "." in tool_spec:
                module, action = tool_spec.split(".", 1)
                tool_defs.append(ToolDefinition(module=module, action=action))
            else:
                # Module-level: all actions
                tool_defs.append(ToolDefinition(module=tool_spec))

        # Resolve tools against available modules (if we have module_info)
        resolved_tools: list[ResolvedTool] = []
        for td in tool_defs:
            if td.module and td.action:
                resolved_tools.append(ResolvedTool(
                    name=f"{td.module}.{td.action}",
                    module=td.module,
                    action=td.action,
                    description=f"{td.module}.{td.action}",
                    parameters={},
                ))
            elif td.module:
                resolved_tools.append(ResolvedTool(
                    name=td.module,
                    module=td.module,
                    action="*",
                    description=f"All actions from {td.module}",
                    parameters={},
                ))

        # Build the execute_tool callback
        async def _execute(module_id: str, action: str, params: dict) -> dict:
            if tool_executor:
                return await tool_executor(module_id, action, params)
            return {"error": "No tool executor configured"}

        # Build agent config
        agent_config = AgentConfig(
            id="spawned",
            system_prompt=system_prompt,
            brain=brain,
            loop=LoopConfig(max_turns=max_turns),
        )

        # Create and run the agent runtime
        runtime = AgentRuntime(
            agent_config=agent_config,
            llm=llm,
            tools=resolved_tools,
            execute_tool=_execute,
            message_queue=message_queue,
            event_callback=event_callback,
        )

        try:
            result = await runtime.run(input_text)
            return {
                "success": result.success,
                "output": result.output,
                "turns": result.total_turns,
                "error": result.error,
                "stop_reason": result.stop_reason,
            }
        except Exception as e:
            logger.exception("Spawned agent execution failed")
            return {
                "success": False,
                "output": "",
                "turns": runtime.turn_count,
                "error": str(e),
            }
        finally:
            try:
                await llm.close()
            except Exception:
                pass
