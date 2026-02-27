"""Security layer — Ollama (local) LLM provider.

Uses raw ``httpx`` to call Ollama's ``/api/chat`` endpoint.
No authentication required — Ollama runs locally.

Usage::

    client = OllamaLLMClient(
        model="llama3.2",
        api_base_url="http://localhost:11434",
    )
    response = await client.chat([LLMMessage(role="user", content="hello")])
"""

from __future__ import annotations

from typing import Any

from llmos_bridge.security.llm_client import LLMMessage, LLMResponse
from llmos_bridge.security.providers.base import BaseHTTPLLMClient

_DEFAULT_BASE_URL = "http://localhost:11434"


class OllamaLLMClient(BaseHTTPLLMClient):
    """Ollama local model chat completion client."""

    def __init__(
        self,
        *,
        api_key: str = "",
        api_base_url: str = "",
        model: str = "llama3.2",
        timeout: float = 60.0,
        max_retries: int = 1,
    ) -> None:
        super().__init__(
            api_key=api_key,
            api_base_url=api_base_url or _DEFAULT_BASE_URL,
            model=model,
            timeout=timeout,
            max_retries=max_retries,
        )

    def _build_headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json"}

    def _build_request_body(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float,
        max_tokens: int,
    ) -> tuple[str, dict[str, Any]]:
        url = f"{self._api_base_url}/api/chat"
        body: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        return url, body

    def _parse_api_response(self, data: dict[str, Any]) -> LLMResponse:
        message = data.get("message", {})
        content = message.get("content", "")

        return LLMResponse(
            content=content,
            model=data.get("model", self._model),
            prompt_tokens=data.get("prompt_eval_count", 0),
            completion_tokens=data.get("eval_count", 0),
        )
