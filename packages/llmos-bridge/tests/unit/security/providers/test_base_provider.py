"""Unit tests -- BaseHTTPLLMClient retry logic and build_provider() factory.

Tests cover:
  - _backoff_delay() returns correct exponential values (capped at 30s)
  - Retry on 429 (rate limit) status
  - Retry on 500 (server error) status
  - No retry on 400 (client error) status -- raises immediately
  - Max retries exceeded raises the underlying exception
  - close() releases the HTTP client
  - build_provider() factory for all provider types
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from llmos_bridge.security.llm_client import LLMClient, LLMMessage, LLMResponse, NullLLMClient
from llmos_bridge.security.providers.base import BaseHTTPLLMClient, _backoff_delay
from llmos_bridge.security.providers.openai import OpenAILLMClient
from llmos_bridge.security.providers.anthropic import AnthropicLLMClient
from llmos_bridge.security.providers.ollama import OllamaLLMClient
from llmos_bridge.security.providers import build_provider


# ---------------------------------------------------------------------------
# Concrete subclass for testing the abstract base
# ---------------------------------------------------------------------------


class StubHTTPLLMClient(BaseHTTPLLMClient):
    """Minimal concrete subclass to test BaseHTTPLLMClient retry logic."""

    def _build_headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json"}

    def _build_request_body(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float,
        max_tokens: int,
    ) -> tuple[str, dict[str, Any]]:
        return "https://test.example.com/chat", {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }

    def _parse_api_response(self, data: dict[str, Any]) -> LLMResponse:
        return LLMResponse(
            content=data.get("text", ""),
            model=data.get("model", self._model),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_messages() -> list[LLMMessage]:
    return [LLMMessage(role="user", content="hello")]


def _make_httpx_response(
    status_code: int,
    json_body: dict[str, Any] | None = None,
    *,
    url: str = "https://test.example.com/chat",
) -> httpx.Response:
    """Create a real httpx.Response with the given status and body."""
    resp = httpx.Response(
        status_code=status_code,
        json=json_body or {},
        request=httpx.Request("POST", url),
    )
    return resp


# ===================================================================
# _backoff_delay tests
# ===================================================================


class TestBackoffDelay:
    """Verify exponential backoff calculation."""

    @pytest.mark.unit
    def test_attempt_0(self) -> None:
        """First attempt: 2^0 = 1 second."""
        assert _backoff_delay(0) == 1.0

    @pytest.mark.unit
    def test_attempt_1(self) -> None:
        """Second attempt: 2^1 = 2 seconds."""
        assert _backoff_delay(1) == 2.0

    @pytest.mark.unit
    def test_attempt_2(self) -> None:
        """Third attempt: 2^2 = 4 seconds."""
        assert _backoff_delay(2) == 4.0

    @pytest.mark.unit
    def test_capped_at_30(self) -> None:
        """Very high attempt number is capped at 30 seconds."""
        assert _backoff_delay(10) == 30.0
        assert _backoff_delay(100) == 30.0

    @pytest.mark.unit
    def test_progressive(self) -> None:
        """Delays are strictly increasing up to the cap."""
        delays = [_backoff_delay(i) for i in range(6)]
        # 1, 2, 4, 8, 16, 30
        assert delays == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0]


# ===================================================================
# Retry logic tests
# ===================================================================


class TestRetryLogic:
    """Test BaseHTTPLLMClient.chat() retry behaviour."""

    @pytest.mark.unit
    async def test_retry_on_429(self) -> None:
        """429 status triggers a retry; second attempt succeeds."""
        client = StubHTTPLLMClient(model="test-model", max_retries=2)

        response_429 = _make_httpx_response(429, {"error": "rate limited"})
        response_200 = _make_httpx_response(200, {"text": "hello", "model": "test-model"})

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.is_closed = False
        mock_http.post = AsyncMock(side_effect=[response_429, response_200])
        client._http = mock_http

        with patch("llmos_bridge.security.providers.base.asyncio.sleep", new_callable=AsyncMock):
            result = await client.chat(_make_messages())

        assert result.content == "hello"
        assert mock_http.post.call_count == 2

    @pytest.mark.unit
    async def test_retry_on_500(self) -> None:
        """500 status triggers a retry; second attempt succeeds."""
        client = StubHTTPLLMClient(model="test-model", max_retries=2)

        response_500 = _make_httpx_response(500, {"error": "internal error"})
        response_200 = _make_httpx_response(200, {"text": "ok", "model": "test-model"})

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.is_closed = False
        mock_http.post = AsyncMock(side_effect=[response_500, response_200])
        client._http = mock_http

        with patch("llmos_bridge.security.providers.base.asyncio.sleep", new_callable=AsyncMock):
            result = await client.chat(_make_messages())

        assert result.content == "ok"
        assert mock_http.post.call_count == 2

    @pytest.mark.unit
    async def test_retry_on_502(self) -> None:
        """502 status triggers a retry."""
        client = StubHTTPLLMClient(model="test-model", max_retries=1)

        response_502 = _make_httpx_response(502, {"error": "bad gateway"})
        response_200 = _make_httpx_response(200, {"text": "recovered", "model": "test-model"})

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.is_closed = False
        mock_http.post = AsyncMock(side_effect=[response_502, response_200])
        client._http = mock_http

        with patch("llmos_bridge.security.providers.base.asyncio.sleep", new_callable=AsyncMock):
            result = await client.chat(_make_messages())

        assert result.content == "recovered"
        assert mock_http.post.call_count == 2

    @pytest.mark.unit
    async def test_no_retry_on_400(self) -> None:
        """400 status (client error) raises HTTPStatusError immediately."""
        client = StubHTTPLLMClient(model="test-model", max_retries=3)

        response_400 = _make_httpx_response(400, {"error": "bad request"})

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.is_closed = False
        mock_http.post = AsyncMock(return_value=response_400)
        client._http = mock_http

        with pytest.raises(httpx.HTTPStatusError):
            await client.chat(_make_messages())

        # Only one call -- no retries for 400
        assert mock_http.post.call_count == 1

    @pytest.mark.unit
    async def test_no_retry_on_401(self) -> None:
        """401 status (unauthorized) raises immediately without retry."""
        client = StubHTTPLLMClient(model="test-model", max_retries=3)

        response_401 = _make_httpx_response(401, {"error": "unauthorized"})

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.is_closed = False
        mock_http.post = AsyncMock(return_value=response_401)
        client._http = mock_http

        with pytest.raises(httpx.HTTPStatusError):
            await client.chat(_make_messages())

        assert mock_http.post.call_count == 1

    @pytest.mark.unit
    async def test_max_retries_exceeded_retryable_status(self) -> None:
        """When all retries exhausted on a retryable status, raise HTTPStatusError."""
        client = StubHTTPLLMClient(model="test-model", max_retries=2)

        # 3 consecutive 429 responses (attempt 0, 1, 2) -- all retries exhausted
        response_429 = _make_httpx_response(429, {"error": "rate limited"})

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.is_closed = False
        mock_http.post = AsyncMock(return_value=response_429)
        client._http = mock_http

        with patch("llmos_bridge.security.providers.base.asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(httpx.HTTPStatusError):
                await client.chat(_make_messages())

        # 3 calls: initial + 2 retries
        assert mock_http.post.call_count == 3

    @pytest.mark.unit
    async def test_max_retries_exceeded_connection_error(self) -> None:
        """Connection error on all attempts raises the underlying exception."""
        client = StubHTTPLLMClient(model="test-model", max_retries=1)

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.is_closed = False
        mock_http.post = AsyncMock(side_effect=ConnectionError("connection refused"))
        client._http = mock_http

        with patch("llmos_bridge.security.providers.base.asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(ConnectionError, match="connection refused"):
                await client.chat(_make_messages())

        # 2 calls: initial + 1 retry
        assert mock_http.post.call_count == 2

    @pytest.mark.unit
    async def test_no_retries_when_max_retries_zero(self) -> None:
        """max_retries=0 means no retries at all."""
        client = StubHTTPLLMClient(model="test-model", max_retries=0)

        response_500 = _make_httpx_response(500, {"error": "server error"})

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.is_closed = False
        mock_http.post = AsyncMock(return_value=response_500)
        client._http = mock_http

        with pytest.raises(httpx.HTTPStatusError):
            await client.chat(_make_messages())

        assert mock_http.post.call_count == 1


# ===================================================================
# Lifecycle tests
# ===================================================================


class TestLifecycle:
    """HTTP client lifecycle management."""

    @pytest.mark.unit
    async def test_close_releases_http_client(self) -> None:
        """close() calls aclose() on the underlying httpx.AsyncClient."""
        client = StubHTTPLLMClient(model="test-model")

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.is_closed = False
        client._http = mock_http

        await client.close()

        mock_http.aclose.assert_awaited_once()
        assert client._http is None

    @pytest.mark.unit
    async def test_close_noop_when_no_client(self) -> None:
        """close() does nothing when no HTTP client was created."""
        client = StubHTTPLLMClient(model="test-model")
        assert client._http is None

        # Should not raise
        await client.close()
        assert client._http is None

    @pytest.mark.unit
    async def test_close_noop_when_already_closed(self) -> None:
        """close() does nothing when the HTTP client is already closed."""
        client = StubHTTPLLMClient(model="test-model")

        mock_http = MagicMock(spec=httpx.AsyncClient)
        mock_http.is_closed = True
        client._http = mock_http

        await client.close()

        mock_http.aclose.assert_not_called()

    @pytest.mark.unit
    def test_lazy_http_creation(self) -> None:
        """_get_http() lazily creates the httpx.AsyncClient."""
        client = StubHTTPLLMClient(model="test-model")
        assert client._http is None

        http = client._get_http()
        assert http is not None
        assert isinstance(http, httpx.AsyncClient)
        assert client._http is http

    @pytest.mark.unit
    async def test_successful_chat_sets_latency(self) -> None:
        """Successful chat() sets latency_ms on the result."""
        client = StubHTTPLLMClient(model="test-model", max_retries=0)

        response_200 = _make_httpx_response(200, {"text": "hi", "model": "test-model"})

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.is_closed = False
        mock_http.post = AsyncMock(return_value=response_200)
        client._http = mock_http

        result = await client.chat(_make_messages())

        assert result.latency_ms >= 0
        assert result.raw == {"text": "hi", "model": "test-model"}


# ===================================================================
# build_provider() factory tests
# ===================================================================


def _make_config(**overrides: Any) -> MagicMock:
    """Build a mock IntentVerifierConfig with sensible defaults."""
    cfg = MagicMock()
    cfg.enabled = True
    cfg.provider = overrides.get("provider", "null")
    cfg.api_key = overrides.get("api_key", "test-key")
    cfg.api_base_url = overrides.get("api_base_url", "")
    cfg.model = overrides.get("model", "gpt-4o-mini")
    cfg.timeout_seconds = overrides.get("timeout_seconds", 30.0)
    cfg.max_retries = overrides.get("max_retries", 2)
    cfg.custom_provider_class = overrides.get("custom_provider_class", None)
    return cfg


class TestBuildProvider:
    """build_provider() factory function tests."""

    @pytest.mark.unit
    def test_null_provider(self) -> None:
        """provider='null' returns NullLLMClient."""
        cfg = _make_config(provider="null")
        client = build_provider(cfg)
        assert isinstance(client, NullLLMClient)

    @pytest.mark.unit
    def test_openai_provider(self) -> None:
        """provider='openai' returns OpenAILLMClient."""
        cfg = _make_config(provider="openai", api_key="sk-test")
        client = build_provider(cfg)
        assert isinstance(client, OpenAILLMClient)
        assert client._api_key == "sk-test"
        assert client._model == "gpt-4o-mini"

    @pytest.mark.unit
    def test_anthropic_provider(self) -> None:
        """provider='anthropic' returns AnthropicLLMClient."""
        cfg = _make_config(provider="anthropic", api_key="sk-ant-test", model="claude-sonnet-4-20250514")
        client = build_provider(cfg)
        assert isinstance(client, AnthropicLLMClient)
        assert client._api_key == "sk-ant-test"
        assert client._model == "claude-sonnet-4-20250514"

    @pytest.mark.unit
    def test_ollama_provider(self) -> None:
        """provider='ollama' returns OllamaLLMClient."""
        cfg = _make_config(provider="ollama", model="llama3.2")
        client = build_provider(cfg)
        assert isinstance(client, OllamaLLMClient)
        assert client._model == "llama3.2"

    @pytest.mark.unit
    def test_custom_provider_without_class_raises(self) -> None:
        """provider='custom' without custom_provider_class raises ValueError."""
        cfg = _make_config(provider="custom", custom_provider_class=None)
        with pytest.raises(ValueError, match="custom_provider_class"):
            build_provider(cfg)

    @pytest.mark.unit
    def test_custom_provider_empty_string_raises(self) -> None:
        """provider='custom' with empty custom_provider_class raises ValueError."""
        cfg = _make_config(provider="custom", custom_provider_class="")
        with pytest.raises(ValueError, match="custom_provider_class"):
            build_provider(cfg)

    @pytest.mark.unit
    def test_unknown_provider_raises(self) -> None:
        """Unknown provider name raises ValueError."""
        cfg = _make_config(provider="deepseek")
        with pytest.raises(ValueError, match="Unknown intent verifier provider"):
            build_provider(cfg)

    @pytest.mark.unit
    def test_openai_with_custom_base_url(self) -> None:
        """OpenAI provider respects custom api_base_url."""
        cfg = _make_config(
            provider="openai",
            api_base_url="https://my-proxy.example.com/v1",
        )
        client = build_provider(cfg)
        assert isinstance(client, OpenAILLMClient)
        assert client._api_base_url == "https://my-proxy.example.com/v1"

    @pytest.mark.unit
    def test_ollama_with_custom_base_url(self) -> None:
        """Ollama provider respects custom api_base_url."""
        cfg = _make_config(
            provider="ollama",
            api_base_url="http://192.168.1.100:11434",
        )
        client = build_provider(cfg)
        assert isinstance(client, OllamaLLMClient)
        assert client._api_base_url == "http://192.168.1.100:11434"

    @pytest.mark.unit
    def test_null_api_key_becomes_empty_string(self) -> None:
        """api_key=None is normalised to empty string for HTTP providers."""
        cfg = _make_config(provider="openai", api_key=None)
        client = build_provider(cfg)
        assert isinstance(client, OpenAILLMClient)
        assert client._api_key == ""

    @pytest.mark.unit
    def test_custom_provider_bad_class_path_raises(self) -> None:
        """provider='custom' with a class path that has no dot raises ValueError."""
        cfg = _make_config(provider="custom", custom_provider_class="NoDotsHere")
        with pytest.raises(ValueError, match="Invalid custom_provider_class"):
            build_provider(cfg)

    @pytest.mark.unit
    def test_custom_provider_nonexistent_module_raises(self) -> None:
        """provider='custom' with non-existent module raises ImportError."""
        cfg = _make_config(
            provider="custom",
            custom_provider_class="nonexistent_module_xyz.MyClient",
        )
        with pytest.raises(ImportError, match="Cannot import module"):
            build_provider(cfg)
