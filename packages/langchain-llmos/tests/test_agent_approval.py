"""Unit tests — ComputerUseAgent approval flow."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from langchain_llmos.providers.base import LLMTurn, ToolCall


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_plan_response(status: str = "completed", action_status: str = "completed") -> dict:
    return {
        "plan_id": "plan_1",
        "status": status,
        "actions": [
            {
                "action_id": "action",
                "id": "action",
                "module": "computer_control",
                "action": "read_screen",
                "status": action_status,
                "result": {"elements": [], "text": "Hello"},
                "error": None,
            }
        ],
    }


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
    client.submit_plan.return_value = {"plan_id": "plan_1", "status": "pending"}
    client.get_plan.return_value = _make_plan_response()
    client.approve_action = AsyncMock(return_value={"applied": True})
    client.get_system_prompt.return_value = "You are an assistant."
    client.close = AsyncMock()
    return client


@pytest.fixture
def mock_provider() -> MagicMock:
    provider = MagicMock()
    provider.supports_vision = False
    provider.build_user_message.return_value = [{"role": "user", "content": "test"}]
    provider.build_assistant_message.return_value = {"role": "assistant", "content": ""}
    provider.build_tool_results_message.return_value = [{"role": "tool", "content": "{}"}]
    provider.format_tool_definitions.return_value = []
    provider.close = AsyncMock()
    return provider


def _make_agent(mock_daemon: AsyncMock, mock_provider: MagicMock, **kwargs: Any) -> Any:
    from langchain_llmos.agent import ComputerUseAgent

    with patch("langchain_llmos.agent.AsyncLLMOSClient", return_value=mock_daemon):
        a = ComputerUseAgent(
            provider=mock_provider,
            daemon_url="http://localhost:40000",
            max_steps=5,
            **kwargs,
        )
    a._daemon = mock_daemon
    return a


# ---------------------------------------------------------------------------
# Approval mode tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestApprovalAutoMode:
    @pytest.mark.asyncio
    async def test_auto_approve_when_awaiting(
        self, mock_daemon: AsyncMock, mock_provider: MagicMock
    ) -> None:
        """Agent auto-approves when plan has awaiting_approval action."""
        # First poll: awaiting_approval, second poll: completed
        mock_daemon.get_plan.side_effect = [
            _make_plan_response(status="running", action_status="awaiting_approval"),
            _make_plan_response(status="completed", action_status="completed"),
        ]

        agent = _make_agent(mock_daemon, mock_provider, approval_mode="auto")

        tc = ToolCall(id="c1", name="computer_control__read_screen", arguments={})
        mock_provider.create_message = AsyncMock(side_effect=[
            LLMTurn(text="", tool_calls=[tc], is_done=False, raw_response=None),
            LLMTurn(text="Done", tool_calls=[], is_done=True, raw_response=None),
        ])

        result = await agent.run("test")
        assert result.success is True

        # Verify approve_action was called.
        mock_daemon.approve_action.assert_called_once()
        call_kwargs = mock_daemon.approve_action.call_args
        assert call_kwargs[1]["decision"] == "approve" or call_kwargs[0][2] == "approve"

    @pytest.mark.asyncio
    async def test_no_approval_when_immediate_complete(
        self, mock_daemon: AsyncMock, mock_provider: MagicMock
    ) -> None:
        """No approval call when plan completes immediately."""
        mock_daemon.get_plan.return_value = _make_plan_response()

        agent = _make_agent(mock_daemon, mock_provider)

        tc = ToolCall(id="c1", name="computer_control__read_screen", arguments={})
        mock_provider.create_message = AsyncMock(side_effect=[
            LLMTurn(text="", tool_calls=[tc], is_done=False, raw_response=None),
            LLMTurn(text="Done", tool_calls=[], is_done=True, raw_response=None),
        ])

        await agent.run("test")
        mock_daemon.approve_action.assert_not_called()


@pytest.mark.unit
class TestApprovalRejectMode:
    @pytest.mark.asyncio
    async def test_always_reject(
        self, mock_daemon: AsyncMock, mock_provider: MagicMock
    ) -> None:
        """Agent rejects when approval_mode='always_reject'."""
        mock_daemon.get_plan.side_effect = [
            _make_plan_response(status="running", action_status="awaiting_approval"),
            _make_plan_response(status="failed", action_status="failed"),
        ]

        agent = _make_agent(mock_daemon, mock_provider, approval_mode="always_reject")

        tc = ToolCall(id="c1", name="computer_control__read_screen", arguments={})
        mock_provider.create_message = AsyncMock(side_effect=[
            LLMTurn(text="", tool_calls=[tc], is_done=False, raw_response=None),
            LLMTurn(text="Failed", tool_calls=[], is_done=True, raw_response=None),
        ])

        await agent.run("test")

        mock_daemon.approve_action.assert_called_once()
        # Check decision is reject.
        args, kwargs = mock_daemon.approve_action.call_args
        assert kwargs.get("decision") == "reject"


@pytest.mark.unit
class TestApprovalCallbackMode:
    @pytest.mark.asyncio
    async def test_callback_called(
        self, mock_daemon: AsyncMock, mock_provider: MagicMock
    ) -> None:
        """Callback is invoked when approval_mode='callback'."""
        mock_daemon.get_plan.side_effect = [
            _make_plan_response(status="running", action_status="awaiting_approval"),
            _make_plan_response(status="completed"),
        ]

        callback = AsyncMock(return_value={"decision": "approve", "reason": "Looks safe"})
        agent = _make_agent(
            mock_daemon, mock_provider,
            approval_mode="callback",
            approval_callback=callback,
        )

        tc = ToolCall(id="c1", name="computer_control__read_screen", arguments={})
        mock_provider.create_message = AsyncMock(side_effect=[
            LLMTurn(text="", tool_calls=[tc], is_done=False, raw_response=None),
            LLMTurn(text="Done", tool_calls=[], is_done=True, raw_response=None),
        ])

        await agent.run("test")

        callback.assert_called_once()
        mock_daemon.approve_action.assert_called_once()

    @pytest.mark.asyncio
    async def test_callback_modify_decision(
        self, mock_daemon: AsyncMock, mock_provider: MagicMock
    ) -> None:
        """Callback can return 'reject' decision."""
        mock_daemon.get_plan.side_effect = [
            _make_plan_response(status="running", action_status="awaiting_approval"),
            _make_plan_response(status="failed"),
        ]

        callback = AsyncMock(return_value={"decision": "reject", "reason": "Not safe"})
        agent = _make_agent(
            mock_daemon, mock_provider,
            approval_mode="callback",
            approval_callback=callback,
        )

        tc = ToolCall(id="c1", name="computer_control__read_screen", arguments={})
        mock_provider.create_message = AsyncMock(side_effect=[
            LLMTurn(text="", tool_calls=[tc], is_done=False, raw_response=None),
            LLMTurn(text="Rejected", tool_calls=[], is_done=True, raw_response=None),
        ])

        await agent.run("test")

        _, kwargs = mock_daemon.approve_action.call_args
        assert kwargs["decision"] == "reject"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestApprovalErrorHandling:
    @pytest.mark.asyncio
    async def test_daemon_error_returns_error_dict(
        self, mock_daemon: AsyncMock, mock_provider: MagicMock
    ) -> None:
        """Daemon submission error returns error dict."""
        mock_daemon.submit_plan.side_effect = Exception("Connection refused")

        agent = _make_agent(mock_daemon, mock_provider)

        tc = ToolCall(id="c1", name="computer_control__read_screen", arguments={})
        mock_provider.create_message = AsyncMock(side_effect=[
            LLMTurn(text="", tool_calls=[tc], is_done=False, raw_response=None),
            LLMTurn(text="Error", tool_calls=[], is_done=True, raw_response=None),
        ])

        result = await agent.run("test")
        # Should still succeed (agent loop continues with error result)
        assert result.success is True
        assert result.steps[0].tool_output["error"]

    @pytest.mark.asyncio
    async def test_invalid_tool_name(
        self, mock_daemon: AsyncMock, mock_provider: MagicMock
    ) -> None:
        """Invalid tool name returns error dict."""
        agent = _make_agent(mock_daemon, mock_provider)

        tc = ToolCall(id="c1", name="invalid_no_separator", arguments={})
        mock_provider.create_message = AsyncMock(side_effect=[
            LLMTurn(text="", tool_calls=[tc], is_done=False, raw_response=None),
            LLMTurn(text="Done", tool_calls=[], is_done=True, raw_response=None),
        ])

        result = await agent.run("test")
        assert result.steps[0].tool_output["error"]

    @pytest.mark.asyncio
    async def test_plan_failed_status(
        self, mock_daemon: AsyncMock, mock_provider: MagicMock
    ) -> None:
        """Plan returning failed status is handled."""
        mock_daemon.get_plan.return_value = {
            "status": "failed",
            "actions": [{
                "action_id": "action",
                "status": "failed",
                "result": None,
                "error": "Permission denied",
            }],
        }

        agent = _make_agent(mock_daemon, mock_provider)

        tc = ToolCall(id="c1", name="computer_control__read_screen", arguments={})
        mock_provider.create_message = AsyncMock(side_effect=[
            LLMTurn(text="", tool_calls=[tc], is_done=False, raw_response=None),
            LLMTurn(text="Failed", tool_calls=[], is_done=True, raw_response=None),
        ])

        result = await agent.run("test")
        assert "error" in result.steps[0].tool_output


# ---------------------------------------------------------------------------
# Constructor tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAgentConstructor:
    def test_legacy_anthropic_api_key(self) -> None:
        """anthropic_api_key creates AnthropicProvider."""
        with patch("langchain_llmos.providers.anthropic_provider.anthropic") as mock_mod:
            mock_mod.AsyncAnthropic.return_value = MagicMock()
            with patch("langchain_llmos.agent.AsyncLLMOSClient"):
                from langchain_llmos.agent import ComputerUseAgent
                a = ComputerUseAgent(anthropic_api_key="test-key")
                assert a._provider.supports_vision is True

    def test_string_provider_openai(self) -> None:
        """provider='openai' creates OpenAICompatibleProvider."""
        with patch("langchain_llmos.providers.openai_provider.openai") as mock_mod:
            mock_mod.AsyncOpenAI.return_value = MagicMock()
            with patch("langchain_llmos.agent.AsyncLLMOSClient"):
                from langchain_llmos.agent import ComputerUseAgent
                a = ComputerUseAgent(provider="openai", api_key="test-key")
                assert a._provider is not None

    def test_string_provider_ollama(self) -> None:
        """provider='ollama' creates OpenAICompatibleProvider with vision=False."""
        with patch("langchain_llmos.providers.openai_provider.openai") as mock_mod:
            mock_mod.AsyncOpenAI.return_value = MagicMock()
            with patch("langchain_llmos.agent.AsyncLLMOSClient"):
                from langchain_llmos.agent import ComputerUseAgent
                a = ComputerUseAgent(provider="ollama")
                assert a._provider.supports_vision is False

    def test_prebuilt_provider_instance(self) -> None:
        """Passing an AgentLLMProvider instance directly."""
        mock_prov = MagicMock()
        mock_prov.supports_vision = True
        with patch("langchain_llmos.agent.AsyncLLMOSClient"):
            from langchain_llmos.agent import ComputerUseAgent
            a = ComputerUseAgent(provider=mock_prov)
            assert a._provider is mock_prov
