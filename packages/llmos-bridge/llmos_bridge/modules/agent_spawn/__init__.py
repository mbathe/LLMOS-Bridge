"""Agent Spawn module — dynamic sub-agent creation and management.

Gives any agent the power to create autonomous sub-agents at runtime,
each with their own system prompt, tools, and LLM configuration.
Sub-agents run in parallel and can be monitored, awaited, or cancelled.

This is a system module — no external dependencies required.
"""

from .module import AgentSpawnModule

__all__ = ["AgentSpawnModule"]
