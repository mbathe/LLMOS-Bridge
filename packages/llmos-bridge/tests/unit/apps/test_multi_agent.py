"""Tests for MultiAgentOrchestrator — agent communication strategies."""

import pytest

from llmos_bridge.apps.agent_runtime import AgentRunResult, LLMProvider
from llmos_bridge.apps.expression import ExpressionContext, ExpressionEngine
from llmos_bridge.apps.models import AgentConfig, BrainConfig, LoopConfig, MultiAgentConfig, MultiAgentStrategy
from llmos_bridge.apps.multi_agent import AgentInstance, MultiAgentOrchestrator, MultiAgentResult
from llmos_bridge.apps.tool_registry import ResolvedTool


# ─── Mock LLM ─────────────────────────────────────────────────────────


class MockLLM(LLMProvider):
    def __init__(self, response_text="Done"):
        self._text = response_text

    async def chat(self, *, system, messages, tools, max_tokens=4096, **kwargs):
        return {"text": self._text, "tool_calls": [], "done": True}

    async def close(self):
        pass


class DelegatingLLM(LLMProvider):
    """LLM that makes a delegate tool call then finishes."""

    def __init__(self, target_agent="worker", task="Do work"):
        self._target = target_agent
        self._task = task
        self._call_count = 0

    async def chat(self, *, system, messages, tools, max_tokens=4096, **kwargs):
        self._call_count += 1
        if self._call_count == 1:
            return {
                "text": "I'll delegate this.",
                "tool_calls": [{
                    "id": "tc1",
                    "name": "delegate",
                    "arguments": {"agent_id": self._target, "task": self._task},
                }],
                "done": False,
            }
        return {"text": "Task completed via delegation.", "tool_calls": [], "done": True}

    async def close(self):
        pass


# ─── Helpers ──────────────────────────────────────────────────────────


def make_agent_config(agent_id="agent", prompt="You are a test agent."):
    return AgentConfig(
        id=agent_id,
        brain=BrainConfig(provider="test", model="test-model"),
        system_prompt=prompt,
    )


def make_agent_instance(agent_id="agent", response="Done"):
    return AgentInstance(
        config=make_agent_config(agent_id),
        llm=MockLLM(response),
        tools=[],
    )


def make_orchestrator(agents, strategy=MultiAgentStrategy.hierarchical, **kwargs):
    config = MultiAgentConfig(
        agents=[a.config for a in agents.values()],
        strategy=strategy,
    )
    return MultiAgentOrchestrator(config=config, agents=agents, **kwargs)


# ─── Tests ────────────────────────────────────────────────────────────


class TestHierarchical:
    @pytest.mark.asyncio
    async def test_coordinator_runs(self):
        agents = {
            "coordinator": make_agent_instance("coordinator", "Coordinated result"),
            "worker": make_agent_instance("worker", "Worker result"),
        }
        orch = make_orchestrator(agents, MultiAgentStrategy.hierarchical)
        result = await orch.run("Do something")
        assert result.success
        assert "Coordinated result" in result.output
        assert result.coordinator_id == "coordinator"

    @pytest.mark.asyncio
    async def test_delegation(self):
        agents = {
            "coordinator": AgentInstance(
                config=make_agent_config("coordinator"),
                llm=DelegatingLLM(target_agent="worker", task="analyze code"),
                tools=[],
            ),
            "worker": make_agent_instance("worker", "Analysis complete"),
        }
        orch = make_orchestrator(agents, MultiAgentStrategy.hierarchical)
        result = await orch.run("Analyze the codebase")
        assert result.success
        assert "coordinator" in result.agent_results
        # Worker should have been called via delegation
        assert "worker" in result.agent_results

    @pytest.mark.asyncio
    async def test_empty_agents(self):
        orch = make_orchestrator({}, MultiAgentStrategy.hierarchical)
        result = await orch.run("Test")
        assert not result.success
        assert "No agents" in result.error


class TestRoundRobin:
    @pytest.mark.asyncio
    async def test_round_robin(self):
        agents = {
            "a1": make_agent_instance("a1", "Result from A1"),
            "a2": make_agent_instance("a2", "Result from A2"),
        }
        orch = make_orchestrator(agents, MultiAgentStrategy.round_robin)
        result = await orch.run("Process this")
        assert result.success
        assert "a1" in result.agent_results
        assert "a2" in result.agent_results

    @pytest.mark.asyncio
    async def test_round_robin_output_is_last(self):
        agents = {
            "first": make_agent_instance("first", "First output"),
            "second": make_agent_instance("second", "Final output"),
        }
        orch = make_orchestrator(agents, MultiAgentStrategy.round_robin)
        result = await orch.run("Task")
        assert result.output == "Final output"


class TestConsensus:
    @pytest.mark.asyncio
    async def test_consensus(self):
        agents = {
            "expert1": make_agent_instance("expert1", "Opinion A"),
            "expert2": make_agent_instance("expert2", "Opinion B"),
        }
        orch = make_orchestrator(agents, MultiAgentStrategy.consensus)
        result = await orch.run("What should we do?")
        assert result.success
        assert "expert1" in result.output
        assert "expert2" in result.output
        assert "Opinion A" in result.output
        assert "Opinion B" in result.output

    @pytest.mark.asyncio
    async def test_consensus_all_agents_run(self):
        agents = {
            "a": make_agent_instance("a", "A"),
            "b": make_agent_instance("b", "B"),
            "c": make_agent_instance("c", "C"),
        }
        orch = make_orchestrator(agents, MultiAgentStrategy.consensus)
        result = await orch.run("Vote")
        assert len(result.agent_results) == 3


class TestPipeline:
    @pytest.mark.asyncio
    async def test_pipeline(self):
        agents = {
            "draft": make_agent_instance("draft", "Draft version"),
            "review": make_agent_instance("review", "Reviewed version"),
            "final": make_agent_instance("final", "Final version"),
        }
        orch = make_orchestrator(agents, MultiAgentStrategy.pipeline)
        result = await orch.run("Write a document")
        assert result.success
        assert result.output == "Final version"
        assert len(result.agent_results) == 3

    @pytest.mark.asyncio
    async def test_pipeline_failure_stops(self):
        class FailLLM(LLMProvider):
            async def chat(self, **kwargs):
                raise RuntimeError("LLM down")
            async def close(self):
                pass

        agents = {
            "good": make_agent_instance("good", "OK"),
            "bad": AgentInstance(
                config=make_agent_config("bad"),
                llm=FailLLM(),
                tools=[],
            ),
            "never": make_agent_instance("never", "Should not run"),
        }
        orch = make_orchestrator(agents, MultiAgentStrategy.pipeline)
        result = await orch.run("Task")
        assert not result.success
        assert "bad" in result.error


class TestMultiAgentResult:
    def test_result_structure(self):
        result = MultiAgentResult(
            success=True,
            output="Done",
            agent_results={"a": AgentRunResult(
                success=True, output="OK", turns=[], total_turns=1,
                total_tokens=10, duration_ms=100, stop_reason="task_complete",
            )},
            coordinator_id="a",
        )
        assert result.success
        assert result.coordinator_id == "a"
        assert "a" in result.agent_results
