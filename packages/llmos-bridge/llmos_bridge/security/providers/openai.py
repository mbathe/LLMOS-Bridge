"""Security layer — OpenAI / Azure OpenAI LLM provider.

Uses raw ``httpx`` (no ``openai`` SDK dependency) to call the
``/chat/completions`` endpoint.

Usage::

    client = OpenAILLMClient(
        api_key="sk-...",
        model="gpt-4o-mini",
    )
    response = await client.chat([LLMMessage(role="user", content="hello")])
"""

from __future__ import annotations

from typing import Any

from llmos_bridge.security.llm_client import LLMMessage, LLMResponse
from llmos_bridge.security.providers.base import BaseHTTPLLMClient

_DEFAULT_BASE_URL = "https://api.openai.com/v1"


class OpenAILLMClient(BaseHTTPLLMClient):
    """OpenAI-compatible chat completion client."""

    def __init__(
        self,
        *,
        api_key: str = "",
        api_base_url: str = "",
        model: str = "gpt-4o-mini",
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
        }
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _build_request_body(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float,
        max_tokens: int,
    ) -> tuple[str, dict[str, Any]]:
        url = f"{self._api_base_url}/chat/completions"
        body: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        return url, body

    def _parse_api_response(self, data: dict[str, Any]) -> LLMResponse:
        choices = data.get("choices", [])
        content = ""
        if choices:
            content = choices[0].get("message", {}).get("content", "")

        usage = data.get("usage", {})
        return LLMResponse(
            content=content,
            model=data.get("model", self._model),
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
        )
