"""Unit tests -- OpenAILLMClient (OpenAI / Azure OpenAI provider).

Tests cover:
  - Successful chat completion with mocked HTTP response
  - Correct URL construction (default and custom base URL)
  - Correct authentication headers (Bearer token, Content-Type)
  - Response parsing (content, tokens, model extraction)
  - Request body structure (model, messages, temperature, max_tokens)
  - close() releases resources
  - Missing API key produces no Authorization header
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from llmos_bridge.security.llm_client import LLMMessage, LLMResponse
from llmos_bridge.security.providers.openai import OpenAILLMClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_messages() -> list[LLMMessage]:
    return [
        LLMMessage(role="system", content="You are helpful."),
        LLMMessage(role="user", content="Hello!"),
    ]


def _openai_response(
    *,
    content: str = "Hi there!",
    model: str = "gpt-4o-mini",
    prompt_tokens: int = 15,
    completion_tokens: int = 8,
) -> dict[str, Any]:
    """Build a realistic OpenAI chat completion response body."""
    return {
        "id": "chatcmpl-abc123",
        "object": "chat.completion",
        "created": 1700000000,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content,
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def _make_httpx_response(
    status_code: int,
    json_body: dict[str, Any],
    *,
    url: str = "https://api.openai.com/v1/chat/completions",
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
        """Default base URL is https://api.openai.com/v1."""
        client = OpenAILLMClient(api_key="sk-test")
        assert client._api_base_url == "https://api.openai.com/v1"

    @pytest.mark.unit
    def test_custom_base_url(self) -> None:
        """Custom base URL is used when provided."""
        client = OpenAILLMClient(api_key="sk-test", api_base_url="https://my-proxy.com/v1")
        assert client._api_base_url == "https://my-proxy.com/v1"

    @pytest.mark.unit
    def test_trailing_slash_stripped(self) -> None:
        """Trailing slash on base URL is stripped."""
        client = OpenAILLMClient(api_key="sk-test", api_base_url="https://my-proxy.com/v1/")
        assert client._api_base_url == "https://my-proxy.com/v1"

    @pytest.mark.unit
    def test_build_request_body_url(self) -> None:
        """_build_request_body() produces the correct URL."""
        client = OpenAILLMClient(api_key="sk-test")
        url, _ = client._build_request_body(
            _make_messages(), temperature=0.0, max_tokens=1024
        )
        assert url == "https://api.openai.com/v1/chat/completions"


# ===================================================================
# Headers
# ===================================================================


class TestHeaders:
    """Verify authentication and content-type headers."""

    @pytest.mark.unit
    def test_headers_with_api_key(self) -> None:
        """Headers include Bearer auth when api_key is set."""
        client = OpenAILLMClient(api_key="sk-test-key-123")
        headers = client._build_headers()

        assert headers["Content-Type"] == "application/json"
        assert headers["Authorization"] == "Bearer sk-test-key-123"

    @pytest.mark.unit
    def test_headers_without_api_key(self) -> None:
        """No Authorization header when api_key is empty."""
        client = OpenAILLMClient(api_key="")
        headers = client._build_headers()

        assert headers["Content-Type"] == "application/json"
        assert "Authorization" not in headers


# ===================================================================
# Request body
# ===================================================================


class TestRequestBody:
    """Verify the request body structure."""

    @pytest.mark.unit
    def test_request_body_structure(self) -> None:
        """Request body has model, messages, temperature, max_tokens."""
        client = OpenAILLMClient(api_key="sk-test", model="gpt-4o")
        messages = _make_messages()
        _, body = client._build_request_body(messages, temperature=0.5, max_tokens=512)

        assert body["model"] == "gpt-4o"
        assert body["temperature"] == 0.5
        assert body["max_tokens"] == 512
        assert len(body["messages"]) == 2
        assert body["messages"][0] == {"role": "system", "content": "You are helpful."}
        assert body["messages"][1] == {"role": "user", "content": "Hello!"}

    @pytest.mark.unit
    def test_default_model(self) -> None:
        """Default model is gpt-4o-mini."""
        client = OpenAILLMClient(api_key="sk-test")
        assert client._model == "gpt-4o-mini"


# ===================================================================
# Response parsing
# ===================================================================


class TestResponseParsing:
    """Verify _parse_api_response() extracts fields correctly."""

    @pytest.mark.unit
    def test_parse_standard_response(self) -> None:
        """Parse a standard OpenAI response with content and usage."""
        client = OpenAILLMClient(api_key="sk-test")
        data = _openai_response(
            content="Hello world!",
            model="gpt-4o-mini",
            prompt_tokens=10,
            completion_tokens=5,
        )

        result = client._parse_api_response(data)

        assert result.content == "Hello world!"
        assert result.model == "gpt-4o-mini"
        assert result.prompt_tokens == 10
        assert result.completion_tokens == 5

    @pytest.mark.unit
    def test_parse_empty_choices(self) -> None:
        """Empty choices array produces empty content."""
        client = OpenAILLMClient(api_key="sk-test")
        data = {"choices": [], "usage": {}, "model": "gpt-4o-mini"}

        result = client._parse_api_response(data)

        assert result.content == ""
        assert result.model == "gpt-4o-mini"

    @pytest.mark.unit
    def test_parse_missing_usage(self) -> None:
        """Missing usage section defaults tokens to 0."""
        client = OpenAILLMClient(api_key="sk-test")
        data = {
            "choices": [{"message": {"content": "hi"}}],
            "model": "gpt-4o-mini",
        }

        result = client._parse_api_response(data)

        assert result.content == "hi"
        assert result.prompt_tokens == 0
        assert result.completion_tokens == 0

    @pytest.mark.unit
    def test_parse_missing_model_uses_default(self) -> None:
        """Missing model in response falls back to the configured model."""
        client = OpenAILLMClient(api_key="sk-test", model="my-custom-model")
        data = {"choices": [{"message": {"content": "yo"}}], "usage": {}}

        result = client._parse_api_response(data)

        assert result.model == "my-custom-model"


# ===================================================================
# Full chat() integration (mocked HTTP)
# ===================================================================


class TestChat:
    """Test the full chat() path with mocked httpx."""

    @pytest.mark.unit
    async def test_successful_chat(self) -> None:
        """Successful chat() returns parsed LLMResponse with latency."""
        client = OpenAILLMClient(api_key="sk-test", max_retries=0)

        resp_data = _openai_response(content="Test reply", prompt_tokens=20, completion_tokens=10)
        mock_response = _make_httpx_response(200, resp_data)

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.is_closed = False
        mock_http.post = AsyncMock(return_value=mock_response)
        client._http = mock_http

        result = await client.chat(_make_messages())

        assert result.content == "Test reply"
        assert result.model == "gpt-4o-mini"
        assert result.prompt_tokens == 20
        assert result.completion_tokens == 10
        assert result.latency_ms >= 0
        assert result.raw == resp_data

    @pytest.mark.unit
    async def test_chat_posts_to_correct_url(self) -> None:
        """chat() posts to {base_url}/chat/completions."""
        client = OpenAILLMClient(
            api_key="sk-test",
            api_base_url="https://custom.api.com/v1",
            max_retries=0,
        )

        resp_data = _openai_response()
        mock_response = _make_httpx_response(200, resp_data, url="https://custom.api.com/v1/chat/completions")

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.is_closed = False
        mock_http.post = AsyncMock(return_value=mock_response)
        client._http = mock_http

        await client.chat(_make_messages())

        call_args = mock_http.post.call_args
        assert call_args[0][0] == "https://custom.api.com/v1/chat/completions"

    @pytest.mark.unit
    async def test_close_releases_resources(self) -> None:
        """close() shuts down the httpx client."""
        client = OpenAILLMClient(api_key="sk-test")

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.is_closed = False
        client._http = mock_http

        await client.close()

        mock_http.aclose.assert_awaited_once()
        assert client._http is None
