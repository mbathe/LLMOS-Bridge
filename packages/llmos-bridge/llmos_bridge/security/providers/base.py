"""Security layer — Base HTTP LLM client with shared retry logic.

All concrete LLM providers (OpenAI, Anthropic, Ollama) inherit from
``BaseHTTPLLMClient`` to get:

  - Shared ``httpx.AsyncClient`` lifecycle (constructor + ``close()``)
  - Exponential backoff retry for transient HTTP errors (429, 5xx)
  - Response timing measurement
  - Consistent error wrapping

Subclasses only need to implement ``_build_request()`` and ``_parse_response()``.
"""

from __future__ import annotations

import asyncio
import time
from abc import abstractmethod
from typing import Any

import httpx

from llmos_bridge.logging import get_logger
from llmos_bridge.security.llm_client import LLMClient, LLMMessage, LLMResponse

log = get_logger(__name__)

# HTTP status codes worth retrying on.
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class BaseHTTPLLMClient(LLMClient):
    """Abstract HTTP-based LLM client with retry logic.

    Args:
        api_key:      API key for authentication (provider-specific).
        api_base_url: Base URL for the API (e.g. ``https://api.openai.com/v1``).
        model:        Model ID to use (e.g. ``gpt-4o-mini``).
        timeout:      HTTP timeout in seconds.
        max_retries:  Maximum retry attempts on transient errors (0 = no retry).
    """

    def __init__(
        self,
        *,
        api_key: str = "",
        api_base_url: str = "",
        model: str = "",
        timeout: float = 30.0,
        max_retries: int = 2,
    ) -> None:
        self._api_key = api_key
        self._api_base_url = api_base_url.rstrip("/")
        self._model = model
        self._timeout = timeout
        self._max_retries = max_retries
        self._http: httpx.AsyncClient | None = None

    def _get_http(self) -> httpx.AsyncClient:
        """Lazily create the async HTTP client."""
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                timeout=self._timeout,
                headers=self._build_headers(),
            )
        return self._http

    @abstractmethod
    def _build_headers(self) -> dict[str, str]:
        """Return HTTP headers for authentication (provider-specific)."""
        ...

    @abstractmethod
    def _build_request_body(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float,
        max_tokens: int,
    ) -> tuple[str, dict[str, Any]]:
        """Return (url, json_body) for the chat completion request.

        The URL should be the full endpoint URL (base_url + path).
        """
        ...

    @abstractmethod
    def _parse_api_response(self, data: dict[str, Any]) -> LLMResponse:
        """Parse the provider-specific JSON response into LLMResponse."""
        ...

    async def chat(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int = 2048,
        timeout: float = 30.0,
    ) -> LLMResponse:
        """Send a chat completion request with retry logic."""
        url, body = self._build_request_body(
            messages, temperature=temperature, max_tokens=max_tokens
        )
        http = self._get_http()

        start = time.time()
        last_exc: Exception | None = None

        for attempt in range(self._max_retries + 1):
            try:
                resp = await http.post(url, json=body, timeout=timeout)

                if resp.status_code in _RETRYABLE_STATUS_CODES and attempt < self._max_retries:
                    delay = _backoff_delay(attempt)
                    log.warning(
                        "llm_provider_retry",
                        status=resp.status_code,
                        attempt=attempt + 1,
                        delay=delay,
                    )
                    await asyncio.sleep(delay)
                    continue

                resp.raise_for_status()
                data = resp.json()
                result = self._parse_api_response(data)
                result.latency_ms = round((time.time() - start) * 1000, 1)
                result.raw = data
                return result

            except httpx.HTTPStatusError:
                raise
            except Exception as exc:
                last_exc = exc
                if attempt < self._max_retries:
                    delay = _backoff_delay(attempt)
                    log.warning(
                        "llm_provider_retry_error",
                        error=str(exc),
                        attempt=attempt + 1,
                        delay=delay,
                    )
                    await asyncio.sleep(delay)
                    continue

        raise last_exc or RuntimeError("LLM request failed after retries")

    async def close(self) -> None:
        if self._http is not None and not self._http.is_closed:
            await self._http.aclose()
            self._http = None


def _backoff_delay(attempt: int) -> float:
    """Exponential backoff: 1s, 2s, 4s, ..."""
    return min(2**attempt, 30.0)
