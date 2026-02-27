"""Unit tests — ComputerUseAgent with OpenAI-compatible provider."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from langchain_llmos.providers.base import LLMTurn, ToolCall, ToolDefinition, ToolResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_daemon() -> AsyncMock:
    client = AsyncMock()
    client.list_modules.return_value = [
        {"module_id": "computer_control", "available": True},
    ]
    client.get_module_manifest.return_value = {
        "module_id": "computer_control",
        "actions": [
            {
                "name": "read_screen",
                "description": "Read the screen.",
                "params_schema": {"type": "object", "properties": {}},
            },
        ],
    }
    client.submit_plan.return_value = {
        "plan_id": "plan_1",
        "status": "completed",
        "actions": [
            {
                "action_id": "action",
                "status": "completed",
                "result": {"elements": [], "text": "Hello"},
                "error": None,
            }
        ],
    }
    client.get_plan.return_value = {
        "status": "completed",
        "actions": [
            {
                "action_id": "action",
                "status": "completed",
                "result": {"elements": [], "text": "Hello"},
                "error": None,
            }
        ],
    }
    client.get_system_prompt.return_value = "You are an assistant."
    client.close = AsyncMock()
    return client


@pytest.fixture
def mock_openai_provider() -> MagicMock:
    """Create a mock AgentLLMProvider that behaves like OpenAI."""
    provider = MagicMock()
    provider.supports_vision = False  # Ollama-like, no vision

    provider.build_user_message.return_value = [
        {"role": "user", "content": "test task"}
    ]

    # Default: LLM says "Done!" immediately.
    done_turn = LLMTurn(text="Done!", tool_calls=[], is_done=True, raw_response=None)
    provider.create_message = AsyncMock(return_value=done_turn)

    provider.build_assistant_message.return_value = {
        "role": "assistant", "content": "Done!"
    }
    provider.build_tool_results_message.return_value = [
        {"role": "tool", "tool_call_id": "call_1", "content": "{}"}
    ]
    provider.format_tool_definitions.return_value = [
        {"type": "function", "function": {"name": "test", "parameters": {}}}
    ]
    provider.close = AsyncMock()

    return provider


@pytest.fixture
def agent(mock_daemon: AsyncMock, mock_openai_provider: MagicMock) -> Any:
    from langchain_llmos.agent import ComputerUseAgent

    with patch("langchain_llmos.agent.AsyncLLMOSClient", return_value=mock_daemon):
        a = ComputerUseAgent(
            provider=mock_openai_provider,
            daemon_url="http://localhost:40000",
            max_steps=5,
        )
    a._daemon = mock_daemon
    return a


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestOpenAIAgentLoop:
    @pytest.mark.asyncio
    async def test_immediate_end_turn(self, agent: Any, mock_openai_provider: MagicMock) -> None:
        result = await agent.run("What do you see?")
        assert result.success is True
        assert "Done!" in result.output
        assert len(result.steps) == 0

    @pytest.mark.asyncio
    async def test_single_tool_call_then_end(
        self, agent: Any, mock_openai_provider: MagicMock, mock_daemon: AsyncMock
    ) -> None:
        tc = ToolCall(id="call_1", name="computer_control__read_screen", arguments={})
        tool_turn = LLMTurn(text="Let me check.", tool_calls=[tc], is_done=False, raw_response=None)
        done_turn = LLMTurn(text="I see a desktop.", tool_calls=[], is_done=True, raw_response=None)
        mock_openai_provider.create_message = AsyncMock(side_effect=[tool_turn, done_turn])

        result = await agent.run("Describe the screen")
        assert result.success is True
        assert len(result.steps) == 1
        assert result.steps[0].tool_name == "computer_control__read_screen"

    @pytest.mark.asyncio
    async def test_max_steps_exhausted(
        self, agent: Any, mock_openai_provider: MagicMock
    ) -> None:
        tc = ToolCall(id="call_1", name="computer_control__read_screen", arguments={})
        tool_turn = LLMTurn(text="", tool_calls=[tc], is_done=False, raw_response=None)
        mock_openai_provider.create_message = AsyncMock(return_value=tool_turn)

        result = await agent.run("Loop forever", max_steps=3)
        assert result.success is False
        assert len(result.steps) == 3

    @pytest.mark.asyncio
    async def test_no_screenshot_when_no_vision(
        self, agent: Any, mock_openai_provider: MagicMock, mock_daemon: AsyncMock
    ) -> None:
        """Provider without vision → include_screenshot=False."""
        mock_openai_provider.supports_vision = False

        tc = ToolCall(id="call_1", name="computer_control__read_screen", arguments={})
        tool_turn = LLMTurn(text="", tool_calls=[tc], is_done=False, raw_response=None)
        done_turn = LLMTurn(text="Done.", tool_calls=[], is_done=True, raw_response=None)
        mock_openai_provider.create_message = AsyncMock(side_effect=[tool_turn, done_turn])

        await agent.run("Read screen")

        # Check the plan submitted to daemon.
        plan = mock_daemon.submit_plan.call_args[0][0]
        assert plan["actions"][0]["params"]["include_screenshot"] is False

    @pytest.mark.asyncio
    async def test_screenshot_when_vision_supported(
        self, agent: Any, mock_openai_provider: MagicMock, mock_daemon: AsyncMock
    ) -> None:
        """Provider with vision → include_screenshot=True."""
        mock_openai_provider.supports_vision = True

        tc = ToolCall(id="call_1", name="computer_control__read_screen", arguments={})
        tool_turn = LLMTurn(text="", tool_calls=[tc], is_done=False, raw_response=None)
        done_turn = LLMTurn(text="Done.", tool_calls=[], is_done=True, raw_response=None)
        mock_openai_provider.create_message = AsyncMock(side_effect=[tool_turn, done_turn])

        await agent.run("Read screen")

        plan = mock_daemon.submit_plan.call_args[0][0]
        assert plan["actions"][0]["params"]["include_screenshot"] is True

    @pytest.mark.asyncio
    async def test_multi_step_loop(
        self, agent: Any, mock_openai_provider: MagicMock
    ) -> None:
        tc1 = ToolCall(id="c1", name="computer_control__read_screen", arguments={})
        tc2 = ToolCall(id="c2", name="computer_control__read_screen", arguments={})
        turns = [
            LLMTurn(text="", tool_calls=[tc1], is_done=False, raw_response=None),
            LLMTurn(text="", tool_calls=[tc2], is_done=False, raw_response=None),
            LLMTurn(text="All done.", tool_calls=[], is_done=True, raw_response=None),
        ]
        mock_openai_provider.create_message = AsyncMock(side_effect=turns)

        result = await agent.run("Do stuff")
        assert result.success is True
        assert len(result.steps) == 2

    @pytest.mark.asyncio
    async def test_tool_result_strips_screenshot_no_vision(
        self, agent: Any, mock_openai_provider: MagicMock, mock_daemon: AsyncMock
    ) -> None:
        """ToolResult should not contain image_b64 when provider lacks vision."""
        mock_openai_provider.supports_vision = False

        # Daemon returns screenshot_b64 in result
        mock_daemon.get_plan.return_value = {
            "status": "completed",
            "actions": [{
                "status": "completed",
                "result": {"text": "Hello", "screenshot_b64": "FAKE_IMG"},
                "error": None,
            }],
        }

        tc = ToolCall(id="c1", name="computer_control__read_screen", arguments={})
        turns = [
            LLMTurn(text="", tool_calls=[tc], is_done=False, raw_response=None),
            LLMTurn(text="Done", tool_calls=[], is_done=True, raw_response=None),
        ]
        mock_openai_provider.create_message = AsyncMock(side_effect=turns)

        result = await agent.run("Read")
        assert result.success is True

        # Check that build_tool_results_message was called with no image
        call_args = mock_openai_provider.build_tool_results_message.call_args
        tool_results = call_args[0][0]
        assert tool_results[0].image_b64 is None


@pytest.mark.unit
class TestOpenAIBackwardCompat:
    @pytest.mark.asyncio
    async def test_legacy_build_tools(self, agent: Any) -> None:
        """_build_tools still works (returns provider-formatted list)."""
        tools = await agent._build_tools()
        assert isinstance(tools, list)

    @pytest.mark.asyncio
    async def test_legacy_execute_tool(self, agent: Any) -> None:
        """_execute_tool delegates to _execute_tool_with_approval."""
        result = await agent._execute_tool("computer_control__read_screen", {})
        assert isinstance(result, dict)
