"""Unit tests — Provider abstraction layer."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from langchain_llmos.providers.base import (
    AgentLLMProvider,
    LLMTurn,
    ToolCall,
    ToolDefinition,
    ToolResult,
)


# ---------------------------------------------------------------------------
# Data type tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDataTypes:
    def test_tool_definition(self) -> None:
        td = ToolDefinition(
            name="fs__read_file",
            description="Read a file",
            parameters_schema={"type": "object", "properties": {"path": {"type": "string"}}},
        )
        assert td.name == "fs__read_file"
        assert "path" in td.parameters_schema["properties"]

    def test_tool_call(self) -> None:
        tc = ToolCall(id="tc_1", name="fs__read_file", arguments={"path": "/tmp"})
        assert tc.id == "tc_1"
        assert tc.arguments["path"] == "/tmp"

    def test_llm_turn_done(self) -> None:
        turn = LLMTurn(text="Done!", tool_calls=[], is_done=True, raw_response=None)
        assert turn.is_done is True
        assert turn.text == "Done!"

    def test_llm_turn_with_tools(self) -> None:
        tc = ToolCall(id="tc_1", name="test", arguments={})
        turn = LLMTurn(text=None, tool_calls=[tc], is_done=False, raw_response=None)
        assert not turn.is_done
        assert len(turn.tool_calls) == 1

    def test_tool_result_basic(self) -> None:
        tr = ToolResult(tool_call_id="tc_1", text='{"ok": true}')
        assert tr.image_b64 is None
        assert not tr.is_error

    def test_tool_result_with_image(self) -> None:
        tr = ToolResult(
            tool_call_id="tc_1",
            text='{"elements": []}',
            image_b64="iVBORw0KGgo_FAKE",
        )
        assert tr.image_b64 == "iVBORw0KGgo_FAKE"
        assert tr.image_media_type == "image/png"


# ---------------------------------------------------------------------------
# Anthropic provider tests
# ---------------------------------------------------------------------------


@dataclass
class _AnthTextBlock:
    type: str = "text"
    text: str = ""


@dataclass
class _AnthToolUseBlock:
    type: str = "tool_use"
    id: str = "tu_1"
    name: str = ""
    input: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.input is None:
            self.input = {}


@dataclass
class _AnthResponse:
    content: list[Any] = None  # type: ignore[assignment]
    stop_reason: str = "end_turn"

    def __post_init__(self) -> None:
        if self.content is None:
            self.content = [_AnthTextBlock(text="Done!")]


@pytest.mark.unit
class TestAnthropicProvider:
    @pytest.fixture
    def mock_anthropic_client(self) -> AsyncMock:
        client = AsyncMock()
        client.messages.create = AsyncMock(return_value=_AnthResponse())
        return client

    @pytest.fixture
    def provider(self, mock_anthropic_client: AsyncMock) -> Any:
        with patch("langchain_llmos.providers.anthropic_provider.anthropic") as mock_mod:
            mock_mod.AsyncAnthropic.return_value = mock_anthropic_client
            from langchain_llmos.providers.anthropic_provider import AnthropicProvider
            p = AnthropicProvider(api_key="test", model="test-model")
        p._client = mock_anthropic_client
        return p

    def test_format_tool_definitions(self, provider: Any) -> None:
        tools = [ToolDefinition("test", "A test tool", {"type": "object", "properties": {}})]
        result = provider.format_tool_definitions(tools)
        assert len(result) == 1
        assert result[0]["name"] == "test"
        assert "input_schema" in result[0]

    def test_build_user_message(self, provider: Any) -> None:
        msgs = provider.build_user_message("Hello")
        assert len(msgs) == 1
        assert msgs[0]["role"] == "user"
        assert msgs[0]["content"] == "Hello"

    def test_build_assistant_message(self, provider: Any) -> None:
        turn = LLMTurn(
            text="Hi",
            tool_calls=[],
            is_done=True,
            raw_response=_AnthResponse(),
        )
        msg = provider.build_assistant_message(turn)
        assert msg["role"] == "assistant"

    def test_build_tool_results_text_only(self, provider: Any) -> None:
        results = [ToolResult(tool_call_id="tu_1", text='{"ok": true}')]
        msgs = provider.build_tool_results_message(results)
        assert len(msgs) == 1  # Single user message
        assert msgs[0]["role"] == "user"
        blocks = msgs[0]["content"]
        assert blocks[0]["type"] == "tool_result"
        assert blocks[0]["tool_use_id"] == "tu_1"

    def test_build_tool_results_with_image(self, provider: Any) -> None:
        results = [ToolResult(
            tool_call_id="tu_1",
            text='{"elements": []}',
            image_b64="iVBORw0KGgo_FAKE",
        )]
        msgs = provider.build_tool_results_message(results)
        content = msgs[0]["content"][0]["content"]
        assert content[0]["type"] == "image"
        assert content[0]["source"]["data"] == "iVBORw0KGgo_FAKE"
        assert content[1]["type"] == "text"

    @pytest.mark.asyncio
    async def test_create_message_end_turn(self, provider: Any) -> None:
        tools = [ToolDefinition("test", "desc", {"type": "object", "properties": {}})]
        turn = await provider.create_message(
            system="You are a test.",
            messages=[{"role": "user", "content": "Hi"}],
            tools=tools,
        )
        assert turn.is_done is True
        assert turn.text == "Done!"
        assert len(turn.tool_calls) == 0

    @pytest.mark.asyncio
    async def test_create_message_tool_use(self, provider: Any, mock_anthropic_client: AsyncMock) -> None:
        mock_anthropic_client.messages.create.return_value = _AnthResponse(
            content=[
                _AnthTextBlock(text="Let me check."),
                _AnthToolUseBlock(id="tu_1", name="fs__read", input={"path": "/"}),
            ],
            stop_reason="tool_use",
        )
        tools = [ToolDefinition("fs__read", "desc", {"type": "object", "properties": {}})]
        turn = await provider.create_message(
            system="test", messages=[], tools=tools,
        )
        assert not turn.is_done
        assert len(turn.tool_calls) == 1
        assert turn.tool_calls[0].name == "fs__read"
        assert turn.tool_calls[0].id == "tu_1"

    def test_supports_vision(self, provider: Any) -> None:
        assert provider.supports_vision is True


# ---------------------------------------------------------------------------
# OpenAI-compatible provider tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestOpenAICompatibleProvider:
    @pytest.fixture
    def mock_openai_client(self) -> AsyncMock:
        client = AsyncMock()
        # Default: simple text response
        choice = MagicMock()
        choice.finish_reason = "stop"
        choice.message.content = "Done!"
        choice.message.tool_calls = None
        response = MagicMock()
        response.choices = [choice]
        client.chat.completions.create = AsyncMock(return_value=response)
        return client

    @pytest.fixture
    def provider(self, mock_openai_client: AsyncMock) -> Any:
        with patch("langchain_llmos.providers.openai_provider.openai") as mock_mod:
            mock_mod.AsyncOpenAI.return_value = mock_openai_client
            mock_mod.NOT_GIVEN = None
            from langchain_llmos.providers.openai_provider import OpenAICompatibleProvider
            p = OpenAICompatibleProvider(api_key="test", model="gpt-4o")
        p._client = mock_openai_client
        return p

    def test_format_tool_definitions(self, provider: Any) -> None:
        tools = [ToolDefinition("test", "A test", {"type": "object", "properties": {}})]
        result = provider.format_tool_definitions(tools)
        assert len(result) == 1
        assert result[0]["type"] == "function"
        assert result[0]["function"]["name"] == "test"
        assert "parameters" in result[0]["function"]

    def test_build_user_message(self, provider: Any) -> None:
        msgs = provider.build_user_message("Hello")
        assert msgs[0]["role"] == "user"

    def test_build_assistant_message_text_only(self, provider: Any) -> None:
        turn = LLMTurn(text="Hi", tool_calls=[], is_done=True, raw_response=None)
        msg = provider.build_assistant_message(turn)
        assert msg["role"] == "assistant"
        assert msg["content"] == "Hi"
        assert "tool_calls" not in msg

    def test_build_assistant_message_with_tools(self, provider: Any) -> None:
        tc = ToolCall(id="call_1", name="test", arguments={"x": 1})
        turn = LLMTurn(text="", tool_calls=[tc], is_done=False, raw_response=None)
        msg = provider.build_assistant_message(turn)
        assert "tool_calls" in msg
        assert msg["tool_calls"][0]["id"] == "call_1"
        assert msg["tool_calls"][0]["function"]["name"] == "test"

    def test_build_tool_results_text_only(self, provider: Any) -> None:
        results = [ToolResult(tool_call_id="call_1", text='{"ok": true}')]
        msgs = provider.build_tool_results_message(results)
        assert len(msgs) == 1
        assert msgs[0]["role"] == "tool"
        assert msgs[0]["tool_call_id"] == "call_1"

    def test_build_tool_results_with_image(self, provider: Any) -> None:
        provider._vision = True
        results = [ToolResult(
            tool_call_id="call_1",
            text='{"elements": []}',
            image_b64="iVBORw0KGgo_FAKE",
        )]
        msgs = provider.build_tool_results_message(results)
        # Should have tool result + user message with image
        assert len(msgs) == 2
        assert msgs[0]["role"] == "tool"
        assert msgs[1]["role"] == "user"
        img_block = msgs[1]["content"][1]
        assert img_block["type"] == "image_url"

    def test_build_tool_results_no_vision(self, provider: Any) -> None:
        provider._vision = False
        results = [ToolResult(
            tool_call_id="call_1",
            text='{"elements": []}',
            image_b64="iVBORw0KGgo_FAKE",
        )]
        msgs = provider.build_tool_results_message(results)
        assert len(msgs) == 1  # No image user message
        assert msgs[0]["role"] == "tool"

    @pytest.mark.asyncio
    async def test_create_message_end_turn(self, provider: Any) -> None:
        tools = [ToolDefinition("test", "desc", {"type": "object", "properties": {}})]
        turn = await provider.create_message(
            system="test", messages=[], tools=tools,
        )
        assert turn.is_done is True
        assert turn.text == "Done!"

    @pytest.mark.asyncio
    async def test_create_message_tool_calls(
        self, provider: Any, mock_openai_client: AsyncMock
    ) -> None:
        tc = MagicMock()
        tc.id = "call_1"
        tc.function.name = "fs__read"
        tc.function.arguments = '{"path": "/"}'

        choice = MagicMock()
        choice.finish_reason = "tool_calls"
        choice.message.content = ""
        choice.message.tool_calls = [tc]
        response = MagicMock()
        response.choices = [choice]
        mock_openai_client.chat.completions.create.return_value = response

        tools = [ToolDefinition("fs__read", "desc", {"type": "object", "properties": {}})]
        turn = await provider.create_message(
            system="test", messages=[], tools=tools,
        )
        assert not turn.is_done
        assert len(turn.tool_calls) == 1
        assert turn.tool_calls[0].name == "fs__read"
        assert turn.tool_calls[0].arguments == {"path": "/"}

    def test_supports_vision_configurable(self) -> None:
        with patch("langchain_llmos.providers.openai_provider.openai") as mock_mod:
            mock_mod.AsyncOpenAI.return_value = MagicMock()
            from langchain_llmos.providers.openai_provider import OpenAICompatibleProvider
            p1 = OpenAICompatibleProvider(api_key="k", vision=True)
            assert p1.supports_vision is True
            p2 = OpenAICompatibleProvider(api_key="k", vision=False)
            assert p2.supports_vision is False


# ---------------------------------------------------------------------------
# Factory tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildAgentProvider:
    def test_unknown_provider_raises(self) -> None:
        from langchain_llmos.providers import build_agent_provider
        with pytest.raises(ValueError, match="Unknown provider"):
            build_agent_provider("unknown_provider")

    def test_anthropic_factory(self) -> None:
        with patch("langchain_llmos.providers.anthropic_provider.anthropic") as mock_mod:
            mock_mod.AsyncAnthropic.return_value = MagicMock()
            from langchain_llmos.providers import build_agent_provider
            p = build_agent_provider("anthropic", api_key="test")
            assert p.supports_vision is True

    def test_openai_factory(self) -> None:
        with patch("langchain_llmos.providers.openai_provider.openai") as mock_mod:
            mock_mod.AsyncOpenAI.return_value = MagicMock()
            from langchain_llmos.providers import build_agent_provider
            p = build_agent_provider("openai", api_key="test")
            assert p.supports_vision is True

    def test_ollama_factory(self) -> None:
        with patch("langchain_llmos.providers.openai_provider.openai") as mock_mod:
            mock_mod.AsyncOpenAI.return_value = MagicMock()
            from langchain_llmos.providers import build_agent_provider
            p = build_agent_provider("ollama")
            assert p.supports_vision is False

    def test_mistral_factory(self) -> None:
        with patch("langchain_llmos.providers.openai_provider.openai") as mock_mod:
            mock_mod.AsyncOpenAI.return_value = MagicMock()
            from langchain_llmos.providers import build_agent_provider
            p = build_agent_provider("mistral", api_key="test")
            assert p.supports_vision is False
