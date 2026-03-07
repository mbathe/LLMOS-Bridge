"""Tests for AgentRuntime — the LLM reactive loop."""

import json
import pytest

from llmos_bridge.apps.agent_runtime import (
    AgentRunResult,
    AgentRuntime,
    AgentTurn,
    LLMProvider,
    StreamEvent,
    ToolCallRequest,
    ToolCallResult,
)
from llmos_bridge.apps.builtins import BuiltinToolExecutor
from llmos_bridge.apps.expression import ExpressionContext, ExpressionEngine
from llmos_bridge.apps.models import AgentConfig, BrainConfig, LoopConfig, LoopType, OnToolError
from llmos_bridge.apps.tool_registry import ResolvedTool


# ─── Mock LLM providers ──────────────────────────────────────────────


class SingleResponseLLM(LLMProvider):
    """Returns a single text response with no tool calls."""

    def __init__(self, text="Done!", done=True):
        self._text = text
        self._done = done

    async def chat(self, *, system, messages, tools, max_tokens=4096, **kwargs):
        return {"text": self._text, "tool_calls": [], "done": self._done}

    async def close(self):
        pass


class ToolCallingLLM(LLMProvider):
    """Makes one tool call then returns done."""

    def __init__(self, tool_name="filesystem__read_file", args=None):
        self._tool_name = tool_name
        self._args = args or {"path": "/tmp/test"}
        self._call_count = 0

    async def chat(self, *, system, messages, tools, max_tokens=4096, **kwargs):
        self._call_count += 1
        if self._call_count == 1:
            return {
                "text": "Let me read that file.",
                "tool_calls": [{"id": "tc1", "name": self._tool_name, "arguments": self._args}],
                "done": False,
            }
        return {"text": "File read successfully.", "tool_calls": [], "done": True}

    async def close(self):
        pass


class MultiTurnLLM(LLMProvider):
    """Makes N tool calls then finishes."""

    def __init__(self, n_tool_turns=3):
        self._n = n_tool_turns
        self._count = 0

    async def chat(self, *, system, messages, tools, max_tokens=4096, **kwargs):
        self._count += 1
        if self._count <= self._n:
            return {
                "text": f"Turn {self._count}",
                "tool_calls": [{"id": f"tc{self._count}", "name": "filesystem__read_file", "arguments": {"path": f"/f{self._count}"}}],
                "done": False,
            }
        return {"text": "All done.", "tool_calls": [], "done": True}

    async def close(self):
        pass


class ErrorLLM(LLMProvider):
    """Always raises an exception."""

    async def chat(self, *, system, messages, tools, max_tokens=4096, **kwargs):
        raise RuntimeError("LLM unavailable")

    async def close(self):
        pass


# ─── Helpers ──────────────────────────────────────────────────────────


def make_config(**overrides) -> AgentConfig:
    defaults = {
        "brain": BrainConfig(provider="test", model="test-model"),
        "system_prompt": "You are a test agent.",
        "loop": LoopConfig(type=LoopType.reactive, max_turns=10),
    }
    defaults.update(overrides)
    return AgentConfig(**defaults)


def make_tool(name="filesystem.read_file", module="filesystem", action="read_file"):
    return ResolvedTool(
        name=name, module=module, action=action,
        description=f"Test tool {name}",
        parameters={"path": {"type": "string", "required": True}},
    )


async def mock_execute_tool(module_id, action, params):
    return {"content": f"result from {module_id}.{action}", "path": params.get("path", "")}


# ─── Tests ────────────────────────────────────────────────────────────


class TestSimpleRun:
    @pytest.mark.asyncio
    async def test_single_response(self):
        agent = AgentRuntime(
            agent_config=make_config(),
            llm=SingleResponseLLM("Hello!"),
            tools=[],
        )
        result = await agent.run("Say hello")
        assert result.success is True
        assert result.output == "Hello!"
        assert result.stop_reason == "task_complete"
        assert result.total_turns == 1

    @pytest.mark.asyncio
    async def test_empty_output(self):
        agent = AgentRuntime(
            agent_config=make_config(),
            llm=SingleResponseLLM(""),
            tools=[],
        )
        result = await agent.run("Test")
        assert result.success is True
        assert result.output == ""

    @pytest.mark.asyncio
    async def test_turn_structure(self):
        agent = AgentRuntime(
            agent_config=make_config(),
            llm=SingleResponseLLM("OK"),
            tools=[],
        )
        result = await agent.run("Do it")
        assert len(result.turns) == 1
        turn = result.turns[0]
        assert turn.turn_number == 1
        assert turn.text == "OK"
        assert turn.tool_calls == []
        assert turn.tool_results == []

    @pytest.mark.asyncio
    async def test_duration_tracked(self):
        agent = AgentRuntime(
            agent_config=make_config(),
            llm=SingleResponseLLM(),
            tools=[],
        )
        result = await agent.run("Test")
        assert result.duration_ms > 0

    @pytest.mark.asyncio
    async def test_tokens_tracked(self):
        agent = AgentRuntime(
            agent_config=make_config(),
            llm=SingleResponseLLM(),
            tools=[],
        )
        result = await agent.run("Test")
        assert result.total_tokens > 0


class TestToolExecution:
    @pytest.mark.asyncio
    async def test_tool_call_and_response(self):
        agent = AgentRuntime(
            agent_config=make_config(),
            llm=ToolCallingLLM(),
            tools=[make_tool()],
            execute_tool=mock_execute_tool,
        )
        result = await agent.run("Read a file")
        assert result.success is True
        assert result.total_turns == 2
        assert len(result.turns[0].tool_calls) == 1
        assert len(result.turns[0].tool_results) == 1
        assert result.output == "File read successfully."

    @pytest.mark.asyncio
    async def test_tool_result_recorded(self):
        agent = AgentRuntime(
            agent_config=make_config(),
            llm=ToolCallingLLM(),
            tools=[make_tool()],
            execute_tool=mock_execute_tool,
        )
        result = await agent.run("Read")
        tr = result.turns[0].tool_results[0]
        assert tr.tool_call_id == "tc1"
        assert "filesystem.read_file" in tr.name
        assert not tr.is_error

    @pytest.mark.asyncio
    async def test_no_executor_returns_error(self):
        agent = AgentRuntime(
            agent_config=make_config(),
            llm=ToolCallingLLM(),
            tools=[make_tool()],
        )
        result = await agent.run("Read")
        tr = result.turns[0].tool_results[0]
        assert tr.is_error is True
        assert "No tool executor" in tr.output

    @pytest.mark.asyncio
    async def test_builtin_tool_execution(self):
        executor = BuiltinToolExecutor()
        agent = AgentRuntime(
            agent_config=make_config(),
            llm=ToolCallingLLM(tool_name="todo", args={"action": "add", "task": "test"}),
            tools=[],
            builtin_executor=executor,
        )
        result = await agent.run("Add a task")
        tr = result.turns[0].tool_results[0]
        assert not tr.is_error
        data = json.loads(tr.output)
        assert data["task"] == "test"


class TestMultiTurn:
    @pytest.mark.asyncio
    async def test_multi_turn_loop(self):
        agent = AgentRuntime(
            agent_config=make_config(),
            llm=MultiTurnLLM(n_tool_turns=3),
            tools=[make_tool()],
            execute_tool=mock_execute_tool,
        )
        result = await agent.run("Do several things")
        assert result.total_turns == 4  # 3 tool turns + 1 final
        assert result.stop_reason == "task_complete"

    @pytest.mark.asyncio
    async def test_max_turns_limit(self):
        config = make_config(loop=LoopConfig(type=LoopType.reactive, max_turns=2))
        agent = AgentRuntime(
            agent_config=config,
            llm=MultiTurnLLM(n_tool_turns=10),
            tools=[make_tool()],
            execute_tool=mock_execute_tool,
        )
        result = await agent.run("Do lots of things")
        assert result.total_turns == 2
        assert result.stop_reason == "max_turns"


class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_llm_error(self):
        agent = AgentRuntime(
            agent_config=make_config(),
            llm=ErrorLLM(),
            tools=[],
        )
        result = await agent.run("Test")
        assert result.success is False
        assert result.stop_reason == "error"
        assert "LLM unavailable" in result.error

    @pytest.mark.asyncio
    async def test_tool_error_show_to_agent(self):
        async def failing_tool(mod, act, params):
            raise ValueError("Tool broke")

        config = make_config(loop=LoopConfig(on_tool_error=OnToolError.show_to_agent))
        agent = AgentRuntime(
            agent_config=config,
            llm=ToolCallingLLM(),
            tools=[make_tool()],
            execute_tool=failing_tool,
        )
        result = await agent.run("Try")
        tr = result.turns[0].tool_results[0]
        assert tr.is_error is True
        assert "Tool broke" in tr.output

    @pytest.mark.asyncio
    async def test_tool_error_skip(self):
        async def failing_tool(mod, act, params):
            raise ValueError("Tool broke")

        config = make_config(loop=LoopConfig(on_tool_error=OnToolError.skip))
        agent = AgentRuntime(
            agent_config=config,
            llm=ToolCallingLLM(),
            tools=[make_tool()],
            execute_tool=failing_tool,
        )
        result = await agent.run("Try")
        tr = result.turns[0].tool_results[0]
        assert tr.is_error is False
        data = json.loads(tr.output)
        assert data["skipped"] is True

    @pytest.mark.asyncio
    async def test_tool_error_fail(self):
        async def failing_tool(mod, act, params):
            raise ValueError("Tool broke")

        config = make_config(loop=LoopConfig(on_tool_error=OnToolError.fail))
        agent = AgentRuntime(
            agent_config=config,
            llm=ToolCallingLLM(),
            tools=[make_tool()],
            execute_tool=failing_tool,
        )
        result = await agent.run("Try")
        assert result.success is False
        assert result.stop_reason == "error"


class TestSystemPrompt:
    @pytest.mark.asyncio
    async def test_template_resolved(self):
        config = make_config(system_prompt="Agent for {{app.name}}")
        ctx = ExpressionContext(app={"name": "my-app"})
        agent = AgentRuntime(
            agent_config=config,
            llm=SingleResponseLLM(),
            tools=[],
            expression_context=ctx,
        )
        result = await agent.run("Hi")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_empty_prompt(self):
        config = make_config(system_prompt="")
        agent = AgentRuntime(
            agent_config=config,
            llm=SingleResponseLLM(),
            tools=[],
        )
        result = await agent.run("Hi")
        assert result.success is True


class TestStopConditions:
    @pytest.mark.asyncio
    async def test_no_tool_calls_stops(self):
        """Agent stops when LLM makes no tool calls."""
        agent = AgentRuntime(
            agent_config=make_config(),
            llm=SingleResponseLLM(done=False),
            tools=[],
        )
        result = await agent.run("Test")
        assert result.stop_reason == "task_complete"

    @pytest.mark.asyncio
    async def test_stop_signal(self):
        """Manual stop signal."""

        class SlowLLM(LLMProvider):
            def __init__(self, agent_ref):
                self._agent = agent_ref
                self._count = 0

            async def chat(self, *, system, messages, tools, max_tokens=4096, **kwargs):
                self._count += 1
                if self._count == 2:
                    self._agent.stop()
                return {
                    "text": f"turn {self._count}",
                    "tool_calls": [{"id": f"t{self._count}", "name": "filesystem__read_file", "arguments": {}}],
                    "done": False,
                }

            async def close(self):
                pass

        agent = AgentRuntime(
            agent_config=make_config(),
            llm=LLMProvider(),  # placeholder, replaced below
            tools=[make_tool()],
            execute_tool=mock_execute_tool,
        )
        agent._llm = SlowLLM(agent)
        result = await agent.run("Go")
        assert result.stop_reason == "stopped"


class TestStreaming:
    @pytest.mark.asyncio
    async def test_stream_events(self):
        agent = AgentRuntime(
            agent_config=make_config(),
            llm=SingleResponseLLM("Hello"),
            tools=[],
        )
        events = []
        async for event in agent.stream("Test"):
            events.append(event)

        types = [e.type for e in events]
        assert "text" in types  # user message
        assert "done" in types

    @pytest.mark.asyncio
    async def test_stream_tool_events(self):
        agent = AgentRuntime(
            agent_config=make_config(),
            llm=ToolCallingLLM(),
            tools=[make_tool()],
            execute_tool=mock_execute_tool,
        )
        events = []
        async for event in agent.stream("Read"):
            events.append(event)

        types = [e.type for e in events]
        assert "tool_call" in types
        assert "tool_result" in types
        assert "done" in types


class TestBuildToolDefs:
    def test_builds_openai_format(self):
        agent = AgentRuntime(
            agent_config=make_config(),
            llm=SingleResponseLLM(),
            tools=[make_tool()],
        )
        defs = agent._build_tool_defs()
        assert len(defs) == 1
        func = defs[0]["function"]
        assert func["name"] == "filesystem__read_file"
        assert "path" in func["parameters"]["properties"]

    def test_empty_tools(self):
        agent = AgentRuntime(
            agent_config=make_config(),
            llm=SingleResponseLLM(),
            tools=[],
        )
        assert agent._build_tool_defs() == []
