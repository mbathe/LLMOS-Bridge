"""Unit tests -- OllamaLLMClient (local Ollama provider).

Tests cover:
  - Successful chat completion with mocked HTTP response
  - Correct URL construction (default localhost:11434 and custom base URL)
  - Headers (Content-Type only, no auth)
  - Request body structure (stream=False, options with temperature/num_predict)
  - Response parsing (message.content, prompt_eval_count, eval_count)
  - Default model and timeout
  - close() releases resources
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from llmos_bridge.security.llm_client import LLMMessage, LLMResponse
from llmos_bridge.security.providers.ollama import OllamaLLMClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_messages() -> list[LLMMessage]:
    return [
        LLMMessage(role="system", content="Be brief."),
        LLMMessage(role="user", content="Summarise this."),
    ]


def _ollama_response(
    *,
    content: str = "Here is a summary.",
    model: str = "llama3.2",
    prompt_eval_count: int = 18,
    eval_count: int = 10,
) -> dict[str, Any]:
    """Build a realistic Ollama /api/chat response body."""
    return {
        "model": model,
        "created_at": "2024-01-01T00:00:00Z",
        "message": {
            "role": "assistant",
            "content": content,
        },
        "done": True,
        "total_duration": 1234567890,
        "load_duration": 12345678,
        "prompt_eval_count": prompt_eval_count,
        "prompt_eval_duration": 98765432,
        "eval_count": eval_count,
        "eval_duration": 987654321,
    }


def _make_httpx_response(
    status_code: int,
    json_body: dict[str, Any],
    *,
    url: str = "http://localhost:11434/api/chat",
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
        """Default base URL is http://localhost:11434."""
        client = OllamaLLMClient()
        assert client._api_base_url == "http://localhost:11434"

    @pytest.mark.unit
    def test_custom_base_url(self) -> None:
        """Custom base URL is used when provided."""
        client = OllamaLLMClient(api_base_url="http://192.168.1.100:11434")
        assert client._api_base_url == "http://192.168.1.100:11434"

    @pytest.mark.unit
    def test_build_request_body_url(self) -> None:
        """_build_request_body() produces {base_url}/api/chat."""
        client = OllamaLLMClient()
        url, _ = client._build_request_body(
            _make_messages(), temperature=0.0, max_tokens=1024
        )
        assert url == "http://localhost:11434/api/chat"

    @pytest.mark.unit
    def test_custom_url_produces_correct_endpoint(self) -> None:
        """Custom base URL produces correct /api/chat endpoint."""
        client = OllamaLLMClient(api_base_url="http://gpu-server:11434")
        url, _ = client._build_request_body(
            _make_messages(), temperature=0.0, max_tokens=1024
        )
        assert url == "http://gpu-server:11434/api/chat"


# ===================================================================
# Headers
# ===================================================================


class TestHeaders:
    """Verify Ollama headers (no auth, Content-Type only)."""

    @pytest.mark.unit
    def test_headers_content_type_only(self) -> None:
        """Headers only include Content-Type -- no auth for local Ollama."""
        client = OllamaLLMClient()
        headers = client._build_headers()

        assert headers == {"Content-Type": "application/json"}

    @pytest.mark.unit
    def test_headers_ignore_api_key(self) -> None:
        """Even if api_key is set, headers do not include it."""
        client = OllamaLLMClient(api_key="should-be-ignored")
        headers = client._build_headers()

        assert "Authorization" not in headers
        assert "x-api-key" not in headers
        assert headers == {"Content-Type": "application/json"}


# ===================================================================
# Request body
# ===================================================================


class TestRequestBody:
    """Verify the Ollama-specific request body structure."""

    @pytest.mark.unit
    def test_request_body_structure(self) -> None:
        """Body has model, messages, stream=False, and options."""
        client = OllamaLLMClient(model="mistral")
        messages = _make_messages()
        _, body = client._build_request_body(messages, temperature=0.7, max_tokens=256)

        assert body["model"] == "mistral"
        assert body["stream"] is False
        assert body["options"]["temperature"] == 0.7
        assert body["options"]["num_predict"] == 256
        assert len(body["messages"]) == 2
        # Ollama passes system messages in the messages array (unlike Anthropic)
        assert body["messages"][0] == {"role": "system", "content": "Be brief."}
        assert body["messages"][1] == {"role": "user", "content": "Summarise this."}

    @pytest.mark.unit
    def test_default_model(self) -> None:
        """Default model is llama3.2."""
        client = OllamaLLMClient()
        assert client._model == "llama3.2"

    @pytest.mark.unit
    def test_default_timeout(self) -> None:
        """Default timeout for Ollama is 60s (higher than other providers)."""
        client = OllamaLLMClient()
        assert client._timeout == 60.0

    @pytest.mark.unit
    def test_default_max_retries(self) -> None:
        """Default max_retries for Ollama is 1 (lower than other providers)."""
        client = OllamaLLMClient()
        assert client._max_retries == 1


# ===================================================================
# Response parsing
# ===================================================================


class TestResponseParsing:
    """Verify _parse_api_response() extracts Ollama-specific fields."""

    @pytest.mark.unit
    def test_parse_standard_response(self) -> None:
        """Parse a standard Ollama response with message and eval counts."""
        client = OllamaLLMClient()
        data = _ollama_response(
            content="Analysis complete.",
            model="llama3.2",
            prompt_eval_count=22,
            eval_count=14,
        )

        result = client._parse_api_response(data)

        assert result.content == "Analysis complete."
        assert result.model == "llama3.2"
        assert result.prompt_tokens == 22
        assert result.completion_tokens == 14

    @pytest.mark.unit
    def test_parse_empty_message(self) -> None:
        """Empty message object produces empty content string."""
        client = OllamaLLMClient()
        data = {"message": {}, "model": "llama3.2"}

        result = client._parse_api_response(data)

        assert result.content == ""

    @pytest.mark.unit
    def test_parse_missing_message(self) -> None:
        """Missing message key produces empty content."""
        client = OllamaLLMClient()
        data = {"model": "llama3.2"}

        result = client._parse_api_response(data)

        assert result.content == ""

    @pytest.mark.unit
    def test_parse_missing_eval_counts(self) -> None:
        """Missing eval count fields default to 0."""
        client = OllamaLLMClient()
        data = {
            "message": {"content": "hello"},
            "model": "llama3.2",
        }

        result = client._parse_api_response(data)

        assert result.prompt_tokens == 0
        assert result.completion_tokens == 0

    @pytest.mark.unit
    def test_parse_missing_model_uses_default(self) -> None:
        """Missing model in response falls back to configured model."""
        client = OllamaLLMClient(model="codellama")
        data = {"message": {"content": "code"}}

        result = client._parse_api_response(data)

        assert result.model == "codellama"


# ===================================================================
# Full chat() integration (mocked HTTP)
# ===================================================================


class TestChat:
    """Test the full chat() path with mocked httpx."""

    @pytest.mark.unit
    async def test_successful_chat(self) -> None:
        """Successful chat() returns parsed LLMResponse with latency."""
        client = OllamaLLMClient(max_retries=0)

        resp_data = _ollama_response(
            content="Local model response.",
            prompt_eval_count=30,
            eval_count=18,
        )
        mock_response = _make_httpx_response(200, resp_data)

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.is_closed = False
        mock_http.post = AsyncMock(return_value=mock_response)
        client._http = mock_http

        result = await client.chat(_make_messages())

        assert result.content == "Local model response."
        assert result.model == "llama3.2"
        assert result.prompt_tokens == 30
        assert result.completion_tokens == 18
        assert result.latency_ms >= 0
        assert result.raw == resp_data

    @pytest.mark.unit
    async def test_chat_posts_to_api_chat_endpoint(self) -> None:
        """chat() posts to {base_url}/api/chat."""
        client = OllamaLLMClient(
            api_base_url="http://gpu-server:11434",
            max_retries=0,
        )

        resp_data = _ollama_response()
        mock_response = _make_httpx_response(
            200, resp_data, url="http://gpu-server:11434/api/chat"
        )

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.is_closed = False
        mock_http.post = AsyncMock(return_value=mock_response)
        client._http = mock_http

        await client.chat(_make_messages())

        call_args = mock_http.post.call_args
        assert call_args[0][0] == "http://gpu-server:11434/api/chat"

    @pytest.mark.unit
    async def test_chat_sends_stream_false(self) -> None:
        """chat() sends stream=False in the request body."""
        client = OllamaLLMClient(max_retries=0)

        resp_data = _ollama_response()
        mock_response = _make_httpx_response(200, resp_data)

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.is_closed = False
        mock_http.post = AsyncMock(return_value=mock_response)
        client._http = mock_http

        await client.chat(_make_messages())

        call_kwargs = mock_http.post.call_args
        body = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert body["stream"] is False

    @pytest.mark.unit
    async def test_close_releases_resources(self) -> None:
        """close() shuts down the httpx client."""
        client = OllamaLLMClient()

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.is_closed = False
        client._http = mock_http

        await client.close()

        mock_http.aclose.assert_awaited_once()
        assert client._http is None
