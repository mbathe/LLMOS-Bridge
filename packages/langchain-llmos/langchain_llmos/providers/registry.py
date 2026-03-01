"""Provider registry — discover, configure and instantiate LLM providers.

Mirrors the ``ModuleRegistry`` pattern used in the daemon: declarative
specs are registered first, implementations are resolved lazily.
"""

from __future__ import annotations

import importlib
import os
from pathlib import Path
from typing import Any

from langchain_llmos.providers.base import AgentLLMProvider
from langchain_llmos.providers.spec import ModelSpec, ProviderSpec


class ProviderNotFoundError(KeyError):
    """Raised when a provider ID is not in the registry."""

    def __init__(self, provider_id: str, available: list[str] | None = None) -> None:
        avail = ", ".join(sorted(available or []))
        super().__init__(
            f"Unknown provider '{provider_id}'. "
            f"Registered: {avail or '(none)'}"
        )
        self.provider_id = provider_id


class ProviderLoadError(RuntimeError):
    """Raised when a provider cannot be instantiated."""

    def __init__(self, provider_id: str, reason: str) -> None:
        super().__init__(f"Cannot load provider '{provider_id}': {reason}")
        self.provider_id = provider_id


# ── routing map: api_style → (module_path, class_name) ──────────────────

_BUILTIN_STYLES: dict[str, tuple[str, str]] = {
    "anthropic": (
        "langchain_llmos.providers.anthropic_provider",
        "AnthropicProvider",
    ),
    "openai_compat": (
        "langchain_llmos.providers.openai_provider",
        "OpenAICompatibleProvider",
    ),
    "google_genai": (
        "langchain_llmos.providers.gemini_provider",
        "GeminiProvider",
    ),
}


class ProviderRegistry:
    """Registry of LLM provider specifications and implementations.

    Usage::

        registry = ProviderRegistry()
        registry.load_builtins()                       # ships with 5 providers
        registry.load_yaml(Path("my_providers.yaml"))  # add custom ones

        provider = registry.build("anthropic", api_key="sk-…")
    """

    def __init__(self) -> None:
        self._specs: dict[str, ProviderSpec] = {}
        self._class_overrides: dict[str, type[AgentLLMProvider]] = {}

    # ── registration ────────────────────────────────────────────────────

    def register(self, spec: ProviderSpec) -> None:
        """Register (or overwrite) a provider spec."""
        self._specs[spec.provider_id] = spec

    def register_class(
        self,
        provider_id: str,
        cls: type[AgentLLMProvider],
    ) -> None:
        """Override the implementation class for *provider_id*.

        Useful for tests or when the default routing by ``api_style``
        is not sufficient.
        """
        self._class_overrides[provider_id] = cls

    # ── YAML loading ────────────────────────────────────────────────────

    def load_yaml(self, path: str | Path) -> None:
        """Load provider specs from a YAML file.

        Expected format::

            providers:
              anthropic:
                display_name: "Anthropic Claude"
                api_style: anthropic
                ...
        """
        import yaml  # lazy — optional dep

        raw = Path(path).read_text(encoding="utf-8")
        data = yaml.safe_load(raw)
        if not isinstance(data, dict):
            return

        providers = data.get("providers", data)
        for pid, spec_dict in providers.items():
            if not isinstance(spec_dict, dict):
                continue
            # Normalise models: if values are plain dicts, inject model_id key
            models_raw = spec_dict.get("models", {})
            models_parsed: dict[str, Any] = {}
            for mid, mspec in models_raw.items():
                if isinstance(mspec, dict):
                    mspec.setdefault("model_id", mid)
                    models_parsed[mid] = ModelSpec.model_validate(mspec)
                else:
                    models_parsed[mid] = ModelSpec(model_id=mid)
            spec_dict["models"] = models_parsed
            spec_dict.setdefault("provider_id", pid)
            self.register(ProviderSpec.model_validate(spec_dict))

    def load_yaml_string(self, yaml_text: str) -> None:
        """Load provider specs from a YAML string (handy for tests)."""
        import yaml

        data = yaml.safe_load(yaml_text)
        if not isinstance(data, dict):
            return

        providers = data.get("providers", data)
        for pid, spec_dict in providers.items():
            if not isinstance(spec_dict, dict):
                continue
            models_raw = spec_dict.get("models", {})
            models_parsed: dict[str, Any] = {}
            for mid, mspec in models_raw.items():
                if isinstance(mspec, dict):
                    mspec.setdefault("model_id", mid)
                    models_parsed[mid] = ModelSpec.model_validate(mspec)
                else:
                    models_parsed[mid] = ModelSpec(model_id=mid)
            spec_dict["models"] = models_parsed
            spec_dict.setdefault("provider_id", pid)
            self.register(ProviderSpec.model_validate(spec_dict))

    def load_builtins(self) -> None:
        """Load the built-in providers shipped with the package."""
        builtins_path = Path(__file__).parent / "builtins.yaml"
        if builtins_path.exists():
            self.load_yaml(builtins_path)

    # ── queries ─────────────────────────────────────────────────────────

    def get_spec(self, provider_id: str) -> ProviderSpec:
        """Return the spec for *provider_id*, or raise."""
        if provider_id not in self._specs:
            raise ProviderNotFoundError(provider_id, list(self._specs))
        return self._specs[provider_id]

    def list_providers(self) -> list[str]:
        """Return sorted list of registered provider IDs."""
        return sorted(self._specs)

    def list_specs(self) -> list[ProviderSpec]:
        """Return all registered specs."""
        return [self._specs[k] for k in sorted(self._specs)]

    def has(self, provider_id: str) -> bool:
        """Check whether *provider_id* is registered."""
        return provider_id in self._specs

    # ── build ───────────────────────────────────────────────────────────

    def build(
        self,
        provider_id: str,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        vision: bool | None = None,
        **extra: Any,
    ) -> AgentLLMProvider:
        """Instantiate the provider from its spec.

        Resolves the API key from the environment if not given explicitly,
        picks the default model from the spec, and delegates to the
        correct implementation class based on ``api_style``.
        """
        spec = self.get_spec(provider_id)

        # Resolve API key
        resolved_key = api_key
        if resolved_key is None and spec.env_key:
            resolved_key = os.environ.get(spec.env_key)

        resolved_model = model or spec.default_model

        # Resolve vision flag
        if vision is None:
            vision = spec.supports_vision(resolved_model)

        # Resolve implementation class
        cls = self._resolve_class(provider_id, spec)

        # Build kwargs based on api_style
        if spec.api_style == "anthropic":
            return cls(api_key=resolved_key, model=resolved_model)

        if spec.api_style == "openai_compat":
            resolved_url = base_url or spec.base_url or "https://api.openai.com/v1"
            return cls(
                api_key=resolved_key,
                model=resolved_model,
                base_url=resolved_url,
                vision=vision,
            )

        # google_genai / custom — pass everything through
        kwargs: dict[str, Any] = {
            "api_key": resolved_key,
            "model": resolved_model,
            **extra,
        }
        if base_url is not None or spec.base_url is not None:
            kwargs["base_url"] = base_url or spec.base_url
        return cls(**kwargs)

    # ── internal ────────────────────────────────────────────────────────

    def _resolve_class(
        self,
        provider_id: str,
        spec: ProviderSpec,
    ) -> type[AgentLLMProvider]:
        """Resolve the Python class to instantiate — lazily imported."""

        # Explicit override?
        if provider_id in self._class_overrides:
            return self._class_overrides[provider_id]

        # Route by api_style
        if spec.api_style in _BUILTIN_STYLES:
            mod_path, cls_name = _BUILTIN_STYLES[spec.api_style]
            try:
                mod = importlib.import_module(mod_path)
            except ImportError as exc:
                pkg = spec.sdk_package or mod_path.rsplit(".", 1)[0]
                raise ProviderLoadError(
                    provider_id,
                    f"Missing SDK package. Install: pip install {pkg}",
                ) from exc
            return getattr(mod, cls_name)  # type: ignore[return-value]

        # Custom class from provider_class path
        if spec.api_style == "custom" and spec.provider_class:
            return self._import_class(spec.provider_class, provider_id)

        # google_genai with provider_class
        if spec.provider_class:
            return self._import_class(spec.provider_class, provider_id)

        raise ProviderLoadError(
            provider_id,
            f"Cannot resolve implementation for api_style='{spec.api_style}'. "
            f"Set 'provider_class' to a fully-qualified class path.",
        )

    @staticmethod
    def _import_class(
        fqn: str,
        provider_id: str,
    ) -> type[AgentLLMProvider]:
        """Dynamically import ``module.path.ClassName``."""
        parts = fqn.rsplit(".", 1)
        if len(parts) != 2:
            raise ProviderLoadError(
                provider_id,
                f"Invalid provider_class '{fqn}': expected 'module.ClassName'",
            )
        mod_path, cls_name = parts
        try:
            mod = importlib.import_module(mod_path)
        except ImportError as exc:
            raise ProviderLoadError(
                provider_id,
                f"Cannot import module '{mod_path}': {exc}",
            ) from exc
        if not hasattr(mod, cls_name):
            raise ProviderLoadError(
                provider_id,
                f"Module '{mod_path}' has no class '{cls_name}'",
            )
        return getattr(mod, cls_name)  # type: ignore[return-value]
