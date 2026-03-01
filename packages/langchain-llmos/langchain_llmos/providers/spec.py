"""Declarative specification models for LLM providers.

These Pydantic v2 models describe a provider's capabilities, models,
and configuration *without* importing any SDK.  They can be serialised
to / from YAML so that adding a new provider requires zero Python code.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ModelCapabilities(BaseModel):
    """Boolean capability flags for a single model."""

    vision: bool = False
    tool_use: bool = True
    streaming: bool = True
    audio_input: bool = False
    video_input: bool = False
    json_mode: bool = False


class ModelPricing(BaseModel):
    """Optional cost information (USD per 1 M tokens)."""

    input_per_1m_tokens: float | None = None
    output_per_1m_tokens: float | None = None


class ModelSpec(BaseModel):
    """Describes a single model offered by a provider."""

    model_id: str = Field(..., description="Model identifier as the provider names it")
    display_name: str = ""
    max_input_tokens: int = 128_000
    max_output_tokens: int = 4096
    capabilities: ModelCapabilities = Field(default_factory=ModelCapabilities)
    supported_media_types: list[str] = Field(
        default_factory=lambda: ["image/png", "image/jpeg"],
        description="MIME types accepted as input (images, PDFs, audio …)",
    )
    pricing: ModelPricing | None = None


class ProviderSpec(BaseModel):
    """Declarative specification of an LLM provider.

    Defines *what* a provider can do and how to reach it, without
    importing any SDK.  Used by :class:`ProviderRegistry` to instantiate
    the correct :class:`AgentLLMProvider` subclass at runtime.

    ``api_style`` determines which concrete class is used:

    * ``"anthropic"``      → :class:`AnthropicProvider`
    * ``"openai_compat"``  → :class:`OpenAICompatibleProvider`
    * ``"google_genai"``   → :class:`GeminiProvider`
    * ``"custom"``         → dynamically imported from ``provider_class``
    """

    provider_id: str = Field(..., description="Unique key: 'anthropic', 'openai', 'gemini' …")
    display_name: str = ""
    api_style: Literal["anthropic", "openai_compat", "google_genai", "custom"] = "openai_compat"
    provider_class: str | None = Field(
        default=None,
        description="Fully-qualified class path for api_style='custom' or 'google_genai'",
    )
    base_url: str | None = None
    env_key: str | None = Field(
        default=None,
        description="Environment variable for the API key (e.g. 'ANTHROPIC_API_KEY')",
    )
    auth_method: Literal["bearer", "api_key_header", "none"] = "bearer"
    sdk_package: str | None = Field(
        default=None,
        description="pip package name (for error messages, e.g. 'anthropic')",
    )
    default_model: str = Field(..., description="Model used when caller does not specify one")
    models: dict[str, ModelSpec] = Field(default_factory=dict)
    rate_limit_rpm: int | None = None
    timeout_seconds: int = 60
    extra: dict[str, Any] = Field(
        default_factory=dict,
        description="Provider-specific options forwarded to the constructor",
    )

    # -- helpers ----------------------------------------------------------

    def get_model(self, model_id: str | None = None) -> ModelSpec | None:
        """Return the :class:`ModelSpec` for *model_id* (or the default model).

        Returns ``None`` if the model is not declared — callers should
        still allow unregistered model strings to be passed through.
        """
        mid = model_id or self.default_model
        return self.models.get(mid)

    def supports_vision(self, model_id: str | None = None) -> bool:
        """Quick check: does the (default) model support vision?"""
        spec = self.get_model(model_id)
        if spec is None:
            return False
        return spec.capabilities.vision
