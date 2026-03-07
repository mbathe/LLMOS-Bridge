"""Protocol params for the agent_spawn module."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SpawnAgentParams(BaseModel):
    name: str = Field(description="Human-readable name for the sub-agent")
    objective: str = Field(description="The task/objective for the sub-agent")
    system_prompt: str = Field(
        default="You are an autonomous AI agent. Complete the given objective.",
        description="System prompt defining the agent's role",
    )
    tools: list[str] = Field(
        default_factory=list,
        description='Tools available to the agent (e.g. ["filesystem.read_file"])',
    )
    model: str = Field(default="", description="LLM model (default: same as parent)")
    provider: str = Field(default="", description="LLM provider (default: same as parent)")
    max_turns: int = Field(default=15, ge=1, le=100, description="Max turns for the agent loop")
    context: str = Field(default="", description="Additional context to pass to the agent")


class CheckAgentParams(BaseModel):
    spawn_id: str = Field(description="The spawn_id returned by spawn_agent")


class GetResultParams(BaseModel):
    spawn_id: str = Field(description="The spawn_id returned by spawn_agent")


class ListAgentsParams(BaseModel):
    status_filter: str = Field(
        default="all",
        description="Filter by status: running, completed, failed, cancelled, or all",
    )


class CancelAgentParams(BaseModel):
    spawn_id: str = Field(description="The spawn_id of the agent to cancel")


class WaitAgentParams(BaseModel):
    spawn_id: str = Field(description="The spawn_id of the agent to wait for")
    timeout: float = Field(default=300, ge=1, le=3600, description="Max seconds to wait")


class SendMessageParams(BaseModel):
    spawn_id: str = Field(description="The spawn_id of the target agent")
    message: str = Field(description="Message to send to the agent")


PARAMS_MAP: dict[str, type] = {
    "spawn_agent": SpawnAgentParams,
    "check_agent": CheckAgentParams,
    "get_result": GetResultParams,
    "list_agents": ListAgentsParams,
    "cancel_agent": CancelAgentParams,
    "wait_agent": WaitAgentParams,
    "send_message": SendMessageParams,
}
