"""Security layer — LLM client protocol for intent verification.

Defines the abstract interface that any LLM provider must implement to
serve as the backend for IntentVerifier.  This keeps the security layer
completely vendor-neutral.

Implementations:
  - NullLLMClient    — no-op (returns empty approval), used when disabled
  - OpenAILLMClient  — OpenAI/Azure (Phase 2, separate file)
  - AnthropicLLMClient — Anthropic Claude (Phase 2, separate file)
  - OllamaLLMClient  — Local Ollama (Phase 2, separate file)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class LLMMessage:
    """A single message in a chat-style LLM conversation."""

    role: str  # "system", "user", "assistant"
    content: str


@dataclass
class LLMResponse:
    """Structured response from an LLM call."""

    content: str
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)
    # Anthropic prompt caching metrics (0 for other providers).
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


class LLMClient(ABC):
    """Abstract LLM client for security verification.

    All implementations must be async-safe and should not raise on
    transient errors — they should return an LLMResponse with
    content indicating the error so the caller can decide how to
    handle it.
    """

    @abstractmethod
    async def chat(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int = 2048,
        timeout: float = 30.0,
    ) -> LLMResponse:
        """Send a chat completion request and return the response."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Release any held resources (HTTP connections, etc.)."""
        ...


class NullLLMClient(LLMClient):
    """No-op client used when intent verification is disabled.

    Always returns an "approved" response so the pipeline continues.
    """

    async def chat(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int = 2048,
        timeout: float = 30.0,
    ) -> LLMResponse:
        return LLMResponse(
            content='{"verdict": "approve", "risk_level": "low", "reasoning": "Verification disabled."}',
            model="null",
        )

    async def close(self) -> None:
        pass
