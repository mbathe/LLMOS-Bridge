"""LLM provider abstraction for the ComputerUseAgent.

Exports
-------
- :class:`AgentLLMProvider`         — Abstract base class
- :class:`AnthropicProvider`        — Anthropic Claude (requires ``anthropic``)
- :class:`OpenAICompatibleProvider` — OpenAI / Ollama / Mistral (requires ``openai``)
- :func:`build_agent_provider`      — Factory from provider name string
"""

from __future__ import annotations

import os
from typing import Any

from langchain_llmos.providers.base import (
    AgentLLMProvider,
    LLMTurn,
    ToolCall,
    ToolDefinition,
    ToolResult,
)

__all__ = [
    "AgentLLMProvider",
    "AnthropicProvider",
    "OpenAICompatibleProvider",
    "LLMTurn",
    "ToolCall",
    "ToolDefinition",
    "ToolResult",
    "build_agent_provider",
]


# Lazy imports to avoid hard dependency on any SDK.
def AnthropicProvider(*args: Any, **kwargs: Any) -> AgentLLMProvider:  # noqa: N802
    from langchain_llmos.providers.anthropic_provider import (
        AnthropicProvider as _Cls,
    )

    return _Cls(*args, **kwargs)


def OpenAICompatibleProvider(*args: Any, **kwargs: Any) -> AgentLLMProvider:  # noqa: N802
    from langchain_llmos.providers.openai_provider import (
        OpenAICompatibleProvider as _Cls,
    )

    return _Cls(*args, **kwargs)


# ---------------------------------------------------------------------------
# Provider defaults
# ---------------------------------------------------------------------------

_PROVIDER_DEFAULTS: dict[str, dict[str, Any]] = {
    "anthropic": {
        "model": "claude-sonnet-4-20250514",
        "env_key": "ANTHROPIC_API_KEY",
        "vision": True,
    },
    "openai": {
        "model": "gpt-4o",
        "base_url": "https://api.openai.com/v1",
        "env_key": "OPENAI_API_KEY",
        "vision": True,
    },
    "ollama": {
        "model": "llama3.2",
        "base_url": "http://localhost:11434/v1",
        "env_key": None,
        "vision": False,
    },
    "mistral": {
        "model": "mistral-large-latest",
        "base_url": "https://api.mistral.ai/v1",
        "env_key": "MISTRAL_API_KEY",
        "vision": False,
    },
}


def build_agent_provider(
    provider: str = "anthropic",
    *,
    api_key: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    vision: bool | None = None,
) -> AgentLLMProvider:
    """Build an :class:`AgentLLMProvider` from a provider name string.

    Args:
        provider:  ``"anthropic"``, ``"openai"``, ``"ollama"``, ``"mistral"``.
        api_key:   API key.  Falls back to env var per provider.
        model:     Override model name.
        base_url:  Override base URL (OpenAI-compatible providers).
        vision:    Override vision support flag.

    Returns:
        Configured provider instance.

    Raises:
        ValueError: Unknown provider name.
    """
    provider = provider.lower()
    if provider not in _PROVIDER_DEFAULTS:
        raise ValueError(
            f"Unknown provider '{provider}'. "
            f"Supported: {', '.join(sorted(_PROVIDER_DEFAULTS))}"
        )

    defaults = _PROVIDER_DEFAULTS[provider]

    resolved_key = api_key
    if resolved_key is None and defaults.get("env_key"):
        resolved_key = os.environ.get(defaults["env_key"])

    resolved_model = model or defaults["model"]
    resolved_vision = vision if vision is not None else defaults.get("vision", True)

    if provider == "anthropic":
        return AnthropicProvider(
            api_key=resolved_key,
            model=resolved_model,
        )

    # OpenAI-compatible (openai, ollama, mistral).
    resolved_url = base_url or defaults.get("base_url", "https://api.openai.com/v1")
    return OpenAICompatibleProvider(
        api_key=resolved_key,
        model=resolved_model,
        base_url=resolved_url,
        vision=resolved_vision,
    )
