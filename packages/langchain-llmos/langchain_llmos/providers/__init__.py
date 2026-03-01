"""LLM provider abstraction for the ComputerUseAgent.

Exports
-------
- :class:`AgentLLMProvider`         — Abstract base class
- :class:`AnthropicProvider`        — Anthropic Claude (requires ``anthropic``)
- :class:`OpenAICompatibleProvider` — OpenAI / Ollama / Mistral (requires ``openai``)
- :class:`GeminiProvider`           — Google Gemini (requires ``google-generativeai``)
- :class:`ProviderRegistry`         — Declarative registry of all providers
- :class:`ProviderSpec`             — Pydantic spec describing a provider
- :class:`ModelSpec`                — Pydantic spec describing a model
- :func:`build_agent_provider`      — Factory from provider name string
"""

from __future__ import annotations

from typing import Any

from langchain_llmos.providers.base import (
    AgentLLMProvider,
    LLMTurn,
    ToolCall,
    ToolDefinition,
    ToolResult,
)
from langchain_llmos.providers.registry import (
    ProviderLoadError,
    ProviderNotFoundError,
    ProviderRegistry,
)
from langchain_llmos.providers.spec import (
    ModelCapabilities,
    ModelPricing,
    ModelSpec,
    ProviderSpec,
)

__all__ = [
    # Base types
    "AgentLLMProvider",
    "LLMTurn",
    "ToolCall",
    "ToolDefinition",
    "ToolResult",
    # Registry
    "ProviderRegistry",
    "ProviderSpec",
    "ModelSpec",
    "ModelCapabilities",
    "ModelPricing",
    "ProviderNotFoundError",
    "ProviderLoadError",
    # Lazy provider constructors
    "AnthropicProvider",
    "OpenAICompatibleProvider",
    "GeminiProvider",
    # Factory
    "build_agent_provider",
    "get_registry",
]


# ---------------------------------------------------------------------------
# Lazy imports — SDKs are NOT loaded until the provider is instantiated
# ---------------------------------------------------------------------------


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


def GeminiProvider(*args: Any, **kwargs: Any) -> AgentLLMProvider:  # noqa: N802
    from langchain_llmos.providers.gemini_provider import (
        GeminiProvider as _Cls,
    )

    return _Cls(*args, **kwargs)


# ---------------------------------------------------------------------------
# Default registry singleton — loaded lazily on first access
# ---------------------------------------------------------------------------

_default_registry: ProviderRegistry | None = None


def get_registry() -> ProviderRegistry:
    """Return the default :class:`ProviderRegistry` (singleton).

    On first call it loads ``builtins.yaml`` shipped with the package.
    """
    global _default_registry
    if _default_registry is None:
        _default_registry = ProviderRegistry()
        _default_registry.load_builtins()
    return _default_registry


# ---------------------------------------------------------------------------
# Backward-compatible factory  (delegates to the registry)
# ---------------------------------------------------------------------------

# Legacy dict kept for tests that import it directly.
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

    This function delegates to :meth:`ProviderRegistry.build` using the
    default registry (which contains all built-in providers).

    Args:
        provider:  ``"anthropic"``, ``"openai"``, ``"ollama"``, ``"mistral"``,
                   ``"gemini"`` — or any provider registered in the registry.
        api_key:   API key.  Falls back to env var per provider.
        model:     Override model name.
        base_url:  Override base URL (OpenAI-compatible providers).
        vision:    Override vision support flag.

    Returns:
        Configured provider instance.

    Raises:
        ProviderNotFoundError: Unknown provider name.
    """
    registry = get_registry()
    provider_lower = provider.lower()

    try:
        return registry.build(
            provider_lower,
            api_key=api_key,
            model=model,
            base_url=base_url,
            vision=vision,
        )
    except ProviderNotFoundError:
        # Backward compat: if registry doesn't know the provider,
        # try the legacy dict (in case someone imported _PROVIDER_DEFAULTS
        # and mutated it).
        if provider_lower in _PROVIDER_DEFAULTS:
            import os

            defaults = _PROVIDER_DEFAULTS[provider_lower]
            resolved_key = api_key
            if resolved_key is None and defaults.get("env_key"):
                resolved_key = os.environ.get(defaults["env_key"])
            resolved_model = model or defaults["model"]
            resolved_vision = vision if vision is not None else defaults.get("vision", True)

            if provider_lower == "anthropic":
                return AnthropicProvider(api_key=resolved_key, model=resolved_model)

            resolved_url = base_url or defaults.get("base_url", "https://api.openai.com/v1")
            return OpenAICompatibleProvider(
                api_key=resolved_key,
                model=resolved_model,
                base_url=resolved_url,
                vision=resolved_vision,
            )

        raise ValueError(
            f"Unknown provider '{provider}'. "
            f"Registered: {', '.join(registry.list_providers())}"
        ) from None
