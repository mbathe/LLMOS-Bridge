"""Security layer — Anthropic Claude LLM provider.

Uses raw ``httpx`` (no ``anthropic`` SDK dependency) to call the
``/messages`` endpoint.

Anthropic's API uses a different message format:
  - The ``system`` parameter is a top-level field (not a message)
  - Only ``user`` and ``assistant`` messages go in the ``messages`` array
  - Responses use ``content[0].text`` instead of ``choices[0].message.content``

Usage::

    client = AnthropicLLMClient(
        api_key="sk-ant-...",
        model="claude-sonnet-4-20250514",
    )
    response = await client.chat([
        LLMMessage(role="system", content="You are a security analyst."),
        LLMMessage(role="user", content="Analyse this plan."),
    ])
"""

from __future__ import annotations

from typing import Any

from llmos_bridge.security.llm_client import LLMMessage, LLMResponse
from llmos_bridge.security.providers.base import BaseHTTPLLMClient

_DEFAULT_BASE_URL = "https://api.anthropic.com/v1"
_ANTHROPIC_VERSION = "2023-06-01"


class AnthropicLLMClient(BaseHTTPLLMClient):
    """Anthropic Claude chat completion client."""

    def __init__(
        self,
        *,
        api_key: str = "",
        api_base_url: str = "",
        model: str = "claude-sonnet-4-20250514",
        timeout: float = 30.0,
        max_retries: int = 2,
    ) -> None:
        super().__init__(
            api_key=api_key,
            api_base_url=api_base_url or _DEFAULT_BASE_URL,
            model=model,
            timeout=timeout,
            max_retries=max_retries,
        )

    def _build_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "anthropic-version": _ANTHROPIC_VERSION,
        }
        if self._api_key:
            headers["x-api-key"] = self._api_key
        return headers

    def _build_request_body(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float,
        max_tokens: int,
    ) -> tuple[str, dict[str, Any]]:
        url = f"{self._api_base_url}/messages"

        # Anthropic: system message is a top-level field, not in messages array.
        system_text = ""
        chat_messages: list[dict[str, str]] = []
        for m in messages:
            if m.role == "system":
                system_text = m.content
            else:
                chat_messages.append({"role": m.role, "content": m.content})

        body: dict[str, Any] = {
            "model": self._model,
            "messages": chat_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if system_text:
            # Use block-based system format with explicit cache breakpoint.
            # Anthropic caches the system prompt server-side for 5 minutes,
            # reducing latency by up to 85% and input cost by 90% on
            # subsequent calls with the same prefix.
            body["system"] = [
                {
                    "type": "text",
                    "text": system_text,
                    "cache_control": {"type": "ephemeral"},
                }
            ]

        return url, body

    def _parse_api_response(self, data: dict[str, Any]) -> LLMResponse:
        content_blocks = data.get("content", [])
        content = ""
        if content_blocks:
            content = content_blocks[0].get("text", "")

        usage = data.get("usage", {})
        return LLMResponse(
            content=content,
            model=data.get("model", self._model),
            prompt_tokens=usage.get("input_tokens", 0),
            completion_tokens=usage.get("output_tokens", 0),
            cache_creation_input_tokens=usage.get("cache_creation_input_tokens", 0),
            cache_read_input_tokens=usage.get("cache_read_input_tokens", 0),
        )
