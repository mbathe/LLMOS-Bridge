"""Unit tests -- AnthropicLLMClient (Anthropic Claude provider).

Tests cover:
  - Successful chat completion with mocked HTTP response
  - Correct URL construction (default and custom base URL)
  - Correct authentication headers (x-api-key, anthropic-version, Content-Type)
  - System message extracted as top-level body field (not in messages array)
  - Response parsing (content blocks, input_tokens/output_tokens, model)
  - Request body structure
  - close() releases resources
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from llmos_bridge.security.llm_client import LLMMessage, LLMResponse
from llmos_bridge.security.providers.anthropic import AnthropicLLMClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_messages_with_system() -> list[LLMMessage]:
    return [
        LLMMessage(role="system", content="You are a security analyst."),
        LLMMessage(role="user", content="Analyse this plan."),
    ]


def _make_messages_no_system() -> list[LLMMessage]:
    return [
        LLMMessage(role="user", content="Hello!"),
    ]


def _anthropic_response(
    *,
    content: str = "The plan looks safe.",
    model: str = "claude-sonnet-4-20250514",
    input_tokens: int = 25,
    output_tokens: int = 12,
) -> dict[str, Any]:
    """Build a realistic Anthropic Messages API response body."""
    return {
        "id": "msg_abc123",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [
            {
                "type": "text",
                "text": content,
            }
        ],
        "stop_reason": "end_turn",
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        },
    }


def _make_httpx_response(
    status_code: int,
    json_body: dict[str, Any],
    *,
    url: str = "https://api.anthropic.com/v1/messages",
) -> httpx.Response:
    return httpx.Response(
        status_code=status_code,
        json=json_body,
        request=httpx.Request("POST", url),
    )


# ===================================================================
# URL construction
# ===================================================================


class TestURLConstruction:
    """Verify endpoint URL is built correctly."""

    @pytest.mark.unit
    def test_default_base_url(self) -> None:
        """Default base URL is https://api.anthropic.com/v1."""
        client = AnthropicLLMClient(api_key="sk-ant-test")
        assert client._api_base_url == "https://api.anthropic.com/v1"

    @pytest.mark.unit
    def test_custom_base_url(self) -> None:
        """Custom base URL is used when provided."""
        client = AnthropicLLMClient(
            api_key="sk-ant-test",
            api_base_url="https://my-proxy.com/v1",
        )
        assert client._api_base_url == "https://my-proxy.com/v1"

    @pytest.mark.unit
    def test_build_request_body_url(self) -> None:
        """_build_request_body() produces {base_url}/messages."""
        client = AnthropicLLMClient(api_key="sk-ant-test")
        url, _ = client._build_request_body(
            _make_messages_with_system(), temperature=0.0, max_tokens=1024
        )
        assert url == "https://api.anthropic.com/v1/messages"


# ===================================================================
# Headers
# ===================================================================


class TestHeaders:
    """Verify authentication and API version headers."""

    @pytest.mark.unit
    def test_headers_with_api_key(self) -> None:
        """Headers include x-api-key and anthropic-version when api_key is set."""
        client = AnthropicLLMClient(api_key="sk-ant-secret-key")
        headers = client._build_headers()

        assert headers["Content-Type"] == "application/json"
        assert headers["x-api-key"] == "sk-ant-secret-key"
        assert headers["anthropic-version"] == "2023-06-01"

    @pytest.mark.unit
    def test_headers_without_api_key(self) -> None:
        """No x-api-key header when api_key is empty."""
        client = AnthropicLLMClient(api_key="")
        headers = client._build_headers()

        assert headers["Content-Type"] == "application/json"
        assert headers["anthropic-version"] == "2023-06-01"
        assert "x-api-key" not in headers


# ===================================================================
# Request body (system message handling)
# ===================================================================


class TestRequestBody:
    """Verify system message is extracted to top-level field."""

    @pytest.mark.unit
    def test_system_message_extracted(self) -> None:
        """System message is a top-level 'system' field, not in messages array."""
        client = AnthropicLLMClient(api_key="sk-ant-test", model="claude-sonnet-4-20250514")
        messages = _make_messages_with_system()
        _, body = client._build_request_body(messages, temperature=0.3, max_tokens=256)

        # System message is sent as block-based format with cache_control.
        assert body["system"] == [
            {
                "type": "text",
                "text": "You are a security analyst.",
                "cache_control": {"type": "ephemeral"},
            }
        ]
        assert body["model"] == "claude-sonnet-4-20250514"
        assert body["temperature"] == 0.3
        assert body["max_tokens"] == 256
        # Only user message in messages array (system extracted)
        assert len(body["messages"]) == 1
        assert body["messages"][0] == {"role": "user", "content": "Analyse this plan."}

    @pytest.mark.unit
    def test_no_system_message(self) -> None:
        """When no system message, 'system' key is absent from body."""
        client = AnthropicLLMClient(api_key="sk-ant-test")
        messages = _make_messages_no_system()
        _, body = client._build_request_body(messages, temperature=0.0, max_tokens=1024)

        assert "system" not in body
        assert len(body["messages"]) == 1
        assert body["messages"][0] == {"role": "user", "content": "Hello!"}

    @pytest.mark.unit
    def test_multi_turn_conversation(self) -> None:
        """Multi-turn conversation with system, user, assistant, user."""
        client = AnthropicLLMClient(api_key="sk-ant-test")
        messages = [
            LLMMessage(role="system", content="Be concise."),
            LLMMessage(role="user", content="First question"),
            LLMMessage(role="assistant", content="First answer"),
            LLMMessage(role="user", content="Follow-up"),
        ]
        _, body = client._build_request_body(messages, temperature=0.0, max_tokens=1024)

        assert body["system"] == [
            {"type": "text", "text": "Be concise.", "cache_control": {"type": "ephemeral"}}
        ]
        assert len(body["messages"]) == 3
        assert body["messages"][0]["role"] == "user"
        assert body["messages"][1]["role"] == "assistant"
        assert body["messages"][2]["role"] == "user"

    @pytest.mark.unit
    def test_default_model(self) -> None:
        """Default model is claude-sonnet-4-20250514."""
        client = AnthropicLLMClient(api_key="sk-ant-test")
        assert client._model == "claude-sonnet-4-20250514"


# ===================================================================
# Response parsing
# ===================================================================


class TestResponseParsing:
    """Verify _parse_api_response() extracts Anthropic-specific fields."""

    @pytest.mark.unit
    def test_parse_standard_response(self) -> None:
        """Parse a standard Anthropic response with content blocks and usage."""
        client = AnthropicLLMClient(api_key="sk-ant-test")
        data = _anthropic_response(
            content="This plan is approved.",
            model="claude-sonnet-4-20250514",
            input_tokens=30,
            output_tokens=15,
        )

        result = client._parse_api_response(data)

        assert result.content == "This plan is approved."
        assert result.model == "claude-sonnet-4-20250514"
        assert result.prompt_tokens == 30
        assert result.completion_tokens == 15

    @pytest.mark.unit
    def test_parse_empty_content_blocks(self) -> None:
        """Empty content array produces empty content string."""
        client = AnthropicLLMClient(api_key="sk-ant-test")
        data = {"content": [], "usage": {}, "model": "claude-sonnet-4-20250514"}

        result = client._parse_api_response(data)

        assert result.content == ""

    @pytest.mark.unit
    def test_parse_missing_usage(self) -> None:
        """Missing usage section defaults tokens to 0."""
        client = AnthropicLLMClient(api_key="sk-ant-test")
        data = {
            "content": [{"type": "text", "text": "response"}],
            "model": "claude-sonnet-4-20250514",
        }

        result = client._parse_api_response(data)

        assert result.content == "response"
        assert result.prompt_tokens == 0
        assert result.completion_tokens == 0

    @pytest.mark.unit
    def test_parse_missing_model_uses_default(self) -> None:
        """Missing model in response falls back to configured model."""
        client = AnthropicLLMClient(api_key="sk-ant-test", model="claude-opus-4-20250514")
        data = {"content": [{"type": "text", "text": "hi"}], "usage": {}}

        result = client._parse_api_response(data)

        assert result.model == "claude-opus-4-20250514"


# ===================================================================
# Full chat() integration (mocked HTTP)
# ===================================================================


class TestChat:
    """Test the full chat() path with mocked httpx."""

    @pytest.mark.unit
    async def test_successful_chat(self) -> None:
        """Successful chat() returns parsed LLMResponse with latency and raw."""
        client = AnthropicLLMClient(api_key="sk-ant-test", max_retries=0)

        resp_data = _anthropic_response(
            content="Plan analysis complete.",
            input_tokens=40,
            output_tokens=20,
        )
        mock_response = _make_httpx_response(200, resp_data)

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.is_closed = False
        mock_http.post = AsyncMock(return_value=mock_response)
        client._http = mock_http

        result = await client.chat(_make_messages_with_system())

        assert result.content == "Plan analysis complete."
        assert result.model == "claude-sonnet-4-20250514"
        assert result.prompt_tokens == 40
        assert result.completion_tokens == 20
        assert result.latency_ms >= 0
        assert result.raw == resp_data

    @pytest.mark.unit
    async def test_chat_posts_to_messages_endpoint(self) -> None:
        """chat() posts to {base_url}/messages."""
        client = AnthropicLLMClient(
            api_key="sk-ant-test",
            api_base_url="https://custom.api.com/v1",
            max_retries=0,
        )

        resp_data = _anthropic_response()
        mock_response = _make_httpx_response(
            200, resp_data, url="https://custom.api.com/v1/messages"
        )

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.is_closed = False
        mock_http.post = AsyncMock(return_value=mock_response)
        client._http = mock_http

        await client.chat(_make_messages_with_system())

        call_args = mock_http.post.call_args
        assert call_args[0][0] == "https://custom.api.com/v1/messages"

    @pytest.mark.unit
    async def test_chat_sends_system_in_body(self) -> None:
        """chat() sends system message as top-level body field."""
        client = AnthropicLLMClient(api_key="sk-ant-test", max_retries=0)

        resp_data = _anthropic_response()
        mock_response = _make_httpx_response(200, resp_data)

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.is_closed = False
        mock_http.post = AsyncMock(return_value=mock_response)
        client._http = mock_http

        await client.chat(_make_messages_with_system())

        call_kwargs = mock_http.post.call_args
        body = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert body["system"] == [
            {"type": "text", "text": "You are a security analyst.", "cache_control": {"type": "ephemeral"}}
        ]
        # Only user message in messages array
        assert all(m["role"] != "system" for m in body["messages"])

    @pytest.mark.unit
    async def test_close_releases_resources(self) -> None:
        """close() shuts down the httpx client."""
        client = AnthropicLLMClient(api_key="sk-ant-test")

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.is_closed = False
        client._http = mock_http

        await client.close()

        mock_http.aclose.assert_awaited_once()
        assert client._http is None
