"""Security layer — LLM provider implementations.

Factory function ``build_provider()`` creates the appropriate LLMClient
based on the IntentVerifierConfig.

Supported providers:
  - ``null``      — NullLLMClient (always approves, zero overhead)
  - ``openai``    — OpenAI / Azure OpenAI
  - ``anthropic`` — Anthropic Claude
  - ``ollama``    — Local Ollama instance
  - ``custom``    — Load any LLMClient subclass by fully-qualified class path
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

from llmos_bridge.security.llm_client import LLMClient, NullLLMClient
from llmos_bridge.security.providers.anthropic import AnthropicLLMClient
from llmos_bridge.security.providers.base import BaseHTTPLLMClient
from llmos_bridge.security.providers.ollama import OllamaLLMClient
from llmos_bridge.security.providers.openai import OpenAILLMClient

if TYPE_CHECKING:
    from llmos_bridge.config import IntentVerifierConfig

__all__ = [
    "AnthropicLLMClient",
    "BaseHTTPLLMClient",
    "OllamaLLMClient",
    "OpenAILLMClient",
    "build_provider",
]


def build_provider(cfg: IntentVerifierConfig) -> LLMClient:
    """Build an LLMClient from the intent verifier configuration.

    Raises:
        ValueError: If the provider is unknown or custom_provider_class is invalid.
        ImportError: If the custom class cannot be loaded.
    """
    provider = cfg.provider

    if provider == "null":
        return NullLLMClient()

    kwargs: dict[str, Any] = {
        "api_key": cfg.api_key or "",
        "api_base_url": cfg.api_base_url or "",
        "model": cfg.model,
        "timeout": cfg.timeout_seconds,
        "max_retries": cfg.max_retries,
    }

    if provider == "openai":
        return OpenAILLMClient(**kwargs)
    if provider == "anthropic":
        return AnthropicLLMClient(**kwargs)
    if provider == "ollama":
        return OllamaLLMClient(**kwargs)
    if provider == "custom":
        return _load_custom_provider(cfg.custom_provider_class, kwargs)

    msg = f"Unknown intent verifier provider: {provider!r}"
    raise ValueError(msg)


def _load_custom_provider(
    class_path: str | None,
    kwargs: dict[str, Any],
) -> LLMClient:
    """Dynamically load a custom LLMClient class by its fully-qualified path.

    Example class_path: ``"myapp.security.MyLLMClient"``
    """
    if not class_path:
        msg = "provider='custom' requires 'custom_provider_class' to be set"
        raise ValueError(msg)

    module_path, _, class_name = class_path.rpartition(".")
    if not module_path:
        msg = f"Invalid custom_provider_class: {class_path!r} (must be 'module.ClassName')"
        raise ValueError(msg)

    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        msg = f"Cannot import module {module_path!r} for custom provider: {exc}"
        raise ImportError(msg) from exc

    cls = getattr(module, class_name, None)
    if cls is None:
        msg = f"Class {class_name!r} not found in module {module_path!r}"
        raise ImportError(msg)

    if not (isinstance(cls, type) and issubclass(cls, LLMClient)):
        msg = f"{class_path!r} is not a subclass of LLMClient"
        raise TypeError(msg)

    # Pass kwargs that the custom class accepts.
    return cls(**kwargs)
