"""Tests for the declarative Provider Registry and Spec models.

Covers:
- ProviderSpec / ModelSpec / ModelCapabilities (Pydantic validation)
- ProviderRegistry (register, list, build, YAML loading)
- Backward compatibility (build_agent_provider still works)
- Builtin specs match legacy _PROVIDER_DEFAULTS
"""

from __future__ import annotations

import os
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from langchain_llmos.providers.spec import (
    ModelCapabilities,
    ModelPricing,
    ModelSpec,
    ProviderSpec,
)
from langchain_llmos.providers.registry import (
    ProviderLoadError,
    ProviderNotFoundError,
    ProviderRegistry,
)


# ═══════════════════════════════════════════════════════════════════════
# TestModelCapabilities
# ═══════════════════════════════════════════════════════════════════════


class TestModelCapabilities:
    def test_defaults(self):
        caps = ModelCapabilities()
        assert caps.vision is False
        assert caps.tool_use is True
        assert caps.streaming is True
        assert caps.audio_input is False
        assert caps.video_input is False
        assert caps.json_mode is False

    def test_override(self):
        caps = ModelCapabilities(vision=True, audio_input=True)
        assert caps.vision is True
        assert caps.audio_input is True

    def test_from_dict(self):
        caps = ModelCapabilities.model_validate({"vision": True, "tool_use": False})
        assert caps.vision is True
        assert caps.tool_use is False


# ═══════════════════════════════════════════════════════════════════════
# TestModelSpec
# ═══════════════════════════════════════════════════════════════════════


class TestModelSpec:
    def test_minimal(self):
        spec = ModelSpec(model_id="gpt-4o")
        assert spec.model_id == "gpt-4o"
        assert spec.max_input_tokens == 128_000
        assert spec.max_output_tokens == 4096
        assert spec.capabilities.vision is False
        assert "image/png" in spec.supported_media_types

    def test_full(self):
        spec = ModelSpec(
            model_id="claude-opus-4-20250514",
            display_name="Claude Opus 4",
            max_input_tokens=200_000,
            max_output_tokens=32_768,
            capabilities=ModelCapabilities(vision=True, audio_input=True),
            supported_media_types=["image/png", "application/pdf"],
            pricing=ModelPricing(input_per_1m_tokens=15.0, output_per_1m_tokens=75.0),
        )
        assert spec.display_name == "Claude Opus 4"
        assert spec.capabilities.audio_input is True
        assert spec.pricing.output_per_1m_tokens == 75.0

    def test_from_dict(self):
        spec = ModelSpec.model_validate({
            "model_id": "gemini-2.5-flash",
            "max_output_tokens": 65536,
            "capabilities": {"vision": True, "audio_input": True},
        })
        assert spec.capabilities.vision is True
        assert spec.max_output_tokens == 65536


# ═══════════════════════════════════════════════════════════════════════
# TestProviderSpec
# ═══════════════════════════════════════════════════════════════════════


class TestProviderSpec:
    def test_minimal(self):
        spec = ProviderSpec(
            provider_id="test",
            api_style="openai_compat",
            default_model="test-model",
        )
        assert spec.provider_id == "test"
        assert spec.auth_method == "bearer"
        assert spec.timeout_seconds == 60

    def test_get_model_found(self):
        spec = ProviderSpec(
            provider_id="test",
            api_style="anthropic",
            default_model="m1",
            models={
                "m1": ModelSpec(model_id="m1", capabilities=ModelCapabilities(vision=True)),
                "m2": ModelSpec(model_id="m2"),
            },
        )
        model = spec.get_model("m1")
        assert model is not None
        assert model.capabilities.vision is True

    def test_get_model_default(self):
        spec = ProviderSpec(
            provider_id="test",
            api_style="anthropic",
            default_model="m1",
            models={"m1": ModelSpec(model_id="m1")},
        )
        model = spec.get_model()
        assert model is not None
        assert model.model_id == "m1"

    def test_get_model_not_found(self):
        spec = ProviderSpec(
            provider_id="test",
            api_style="anthropic",
            default_model="m1",
        )
        assert spec.get_model("nonexistent") is None

    def test_supports_vision(self):
        spec = ProviderSpec(
            provider_id="test",
            api_style="anthropic",
            default_model="m1",
            models={
                "m1": ModelSpec(model_id="m1", capabilities=ModelCapabilities(vision=True)),
                "m2": ModelSpec(model_id="m2", capabilities=ModelCapabilities(vision=False)),
            },
        )
        assert spec.supports_vision("m1") is True
        assert spec.supports_vision("m2") is False
        assert spec.supports_vision() is True  # default = m1

    def test_extra_field(self):
        spec = ProviderSpec(
            provider_id="test",
            api_style="custom",
            default_model="m",
            extra={"custom_option": 42},
        )
        assert spec.extra["custom_option"] == 42


# ═══════════════════════════════════════════════════════════════════════
# TestProviderRegistry
# ═══════════════════════════════════════════════════════════════════════


class TestProviderRegistry:
    def _make_spec(self, pid: str = "test", **overrides) -> ProviderSpec:
        defaults = {
            "provider_id": pid,
            "api_style": "openai_compat",
            "default_model": "test-model",
        }
        defaults.update(overrides)
        return ProviderSpec(**defaults)

    def test_register_and_list(self):
        reg = ProviderRegistry()
        reg.register(self._make_spec("a"))
        reg.register(self._make_spec("b"))
        assert reg.list_providers() == ["a", "b"]

    def test_has(self):
        reg = ProviderRegistry()
        reg.register(self._make_spec("a"))
        assert reg.has("a") is True
        assert reg.has("z") is False

    def test_get_spec(self):
        reg = ProviderRegistry()
        spec = self._make_spec("myp")
        reg.register(spec)
        assert reg.get_spec("myp") is spec

    def test_get_spec_not_found(self):
        reg = ProviderRegistry()
        with pytest.raises(ProviderNotFoundError):
            reg.get_spec("nonexistent")

    def test_list_specs(self):
        reg = ProviderRegistry()
        reg.register(self._make_spec("b"))
        reg.register(self._make_spec("a"))
        specs = reg.list_specs()
        assert [s.provider_id for s in specs] == ["a", "b"]

    def test_register_overwrites(self):
        reg = ProviderRegistry()
        reg.register(self._make_spec("a", default_model="v1"))
        reg.register(self._make_spec("a", default_model="v2"))
        assert reg.get_spec("a").default_model == "v2"

    def test_build_openai_compat(self):
        reg = ProviderRegistry()
        reg.register(self._make_spec(
            "myoai",
            api_style="openai_compat",
            base_url="http://localhost:1234/v1",
        ))
        with patch("langchain_llmos.providers.openai_provider.OpenAICompatibleProvider") as mock_cls:
            mock_cls.return_value = MagicMock()
            provider = reg.build("myoai", api_key="test-key")
            mock_cls.assert_called_once_with(
                api_key="test-key",
                model="test-model",
                base_url="http://localhost:1234/v1",
                vision=False,
            )

    def test_build_anthropic(self):
        reg = ProviderRegistry()
        reg.register(self._make_spec(
            "anth",
            api_style="anthropic",
            default_model="claude-sonnet-4-20250514",
            models={"claude-sonnet-4-20250514": ModelSpec(
                model_id="claude-sonnet-4-20250514",
                capabilities=ModelCapabilities(vision=True),
            )},
        ))
        with patch("langchain_llmos.providers.anthropic_provider.AnthropicProvider") as mock_cls:
            mock_cls.return_value = MagicMock()
            reg.build("anth", api_key="sk-test")
            mock_cls.assert_called_once_with(api_key="sk-test", model="claude-sonnet-4-20250514")

    def test_build_env_key_fallback(self):
        reg = ProviderRegistry()
        reg.register(self._make_spec("ep", env_key="MY_TEST_KEY"))
        with patch("langchain_llmos.providers.openai_provider.OpenAICompatibleProvider") as mock_cls:
            mock_cls.return_value = MagicMock()
            with patch.dict(os.environ, {"MY_TEST_KEY": "env-secret"}):
                reg.build("ep")
                mock_cls.assert_called_once()
                call_kwargs = mock_cls.call_args[1]
                assert call_kwargs["api_key"] == "env-secret"

    def test_build_not_found(self):
        reg = ProviderRegistry()
        with pytest.raises(ProviderNotFoundError):
            reg.build("nonexistent")

    def test_register_class_override(self):
        reg = ProviderRegistry()
        reg.register(self._make_spec("custom_p", api_style="openai_compat"))
        mock_cls = MagicMock()
        reg.register_class("custom_p", mock_cls)
        reg.build("custom_p", api_key="k")
        mock_cls.assert_called_once()

    def test_build_vision_auto_detect(self):
        reg = ProviderRegistry()
        reg.register(self._make_spec(
            "vp",
            models={"test-model": ModelSpec(
                model_id="test-model",
                capabilities=ModelCapabilities(vision=True),
            )},
        ))
        with patch("langchain_llmos.providers.openai_provider.OpenAICompatibleProvider") as mock_cls:
            mock_cls.return_value = MagicMock()
            reg.build("vp", api_key="k")
            call_kwargs = mock_cls.call_args[1]
            assert call_kwargs["vision"] is True

    def test_build_vision_override(self):
        reg = ProviderRegistry()
        reg.register(self._make_spec(
            "vp2",
            models={"test-model": ModelSpec(
                model_id="test-model",
                capabilities=ModelCapabilities(vision=True),
            )},
        ))
        with patch("langchain_llmos.providers.openai_provider.OpenAICompatibleProvider") as mock_cls:
            mock_cls.return_value = MagicMock()
            reg.build("vp2", api_key="k", vision=False)
            call_kwargs = mock_cls.call_args[1]
            assert call_kwargs["vision"] is False


# ═══════════════════════════════════════════════════════════════════════
# TestYAMLLoading
# ═══════════════════════════════════════════════════════════════════════


class TestYAMLLoading:
    def test_load_yaml_string(self):
        yaml_str = textwrap.dedent("""\
            providers:
              test_provider:
                display_name: "Test Provider"
                api_style: openai_compat
                base_url: "http://localhost:8000/v1"
                default_model: test-model
                models:
                  test-model:
                    max_input_tokens: 32000
                    max_output_tokens: 4096
                    capabilities:
                      vision: true
        """)
        reg = ProviderRegistry()
        reg.load_yaml_string(yaml_str)
        assert reg.has("test_provider")
        spec = reg.get_spec("test_provider")
        assert spec.display_name == "Test Provider"
        assert spec.base_url == "http://localhost:8000/v1"
        assert "test-model" in spec.models
        assert spec.models["test-model"].capabilities.vision is True

    def test_load_yaml_file(self, tmp_path: Path):
        yaml_file = tmp_path / "providers.yaml"
        yaml_file.write_text(textwrap.dedent("""\
            providers:
              file_provider:
                api_style: openai_compat
                default_model: my-model
                models:
                  my-model:
                    max_output_tokens: 8192
        """))
        reg = ProviderRegistry()
        reg.load_yaml(yaml_file)
        assert reg.has("file_provider")
        spec = reg.get_spec("file_provider")
        assert spec.models["my-model"].max_output_tokens == 8192

    def test_load_yaml_multiple_providers(self):
        yaml_str = textwrap.dedent("""\
            providers:
              p1:
                api_style: openai_compat
                default_model: m1
                models:
                  m1:
                    max_output_tokens: 1000
              p2:
                api_style: anthropic
                default_model: m2
                models:
                  m2:
                    max_output_tokens: 2000
        """)
        reg = ProviderRegistry()
        reg.load_yaml_string(yaml_str)
        assert reg.list_providers() == ["p1", "p2"]


# ═══════════════════════════════════════════════════════════════════════
# TestBuiltinSpecs
# ═══════════════════════════════════════════════════════════════════════


class TestBuiltinSpecs:
    @pytest.fixture
    def builtins(self):
        reg = ProviderRegistry()
        reg.load_builtins()
        return reg

    def test_builtins_loaded(self, builtins: ProviderRegistry):
        providers = builtins.list_providers()
        assert "anthropic" in providers
        assert "openai" in providers
        assert "ollama" in providers
        assert "mistral" in providers
        assert "gemini" in providers

    def test_anthropic_spec(self, builtins: ProviderRegistry):
        spec = builtins.get_spec("anthropic")
        assert spec.api_style == "anthropic"
        assert spec.default_model == "claude-sonnet-4-20250514"
        assert spec.env_key == "ANTHROPIC_API_KEY"
        assert "claude-sonnet-4-20250514" in spec.models
        assert spec.models["claude-sonnet-4-20250514"].capabilities.vision is True

    def test_openai_spec(self, builtins: ProviderRegistry):
        spec = builtins.get_spec("openai")
        assert spec.api_style == "openai_compat"
        assert spec.default_model == "gpt-4o"
        assert spec.base_url == "https://api.openai.com/v1"

    def test_ollama_spec(self, builtins: ProviderRegistry):
        spec = builtins.get_spec("ollama")
        assert spec.base_url == "http://localhost:11434/v1"
        assert spec.env_key is None

    def test_gemini_spec(self, builtins: ProviderRegistry):
        spec = builtins.get_spec("gemini")
        assert spec.api_style == "google_genai"
        assert spec.default_model == "gemini-2.5-flash"
        assert "gemini-2.5-flash" in spec.models
        flash = spec.models["gemini-2.5-flash"]
        assert flash.capabilities.vision is True
        assert flash.capabilities.audio_input is True
        assert flash.max_input_tokens == 1_048_576


# ═══════════════════════════════════════════════════════════════════════
# TestBackwardCompat
# ═══════════════════════════════════════════════════════════════════════


class TestBackwardCompat:
    """Ensure build_agent_provider() works exactly as before."""

    def test_anthropic_via_factory(self):
        with patch("langchain_llmos.providers.anthropic_provider.AnthropicProvider") as mock_cls:
            mock_cls.return_value = MagicMock()
            from langchain_llmos.providers import build_agent_provider

            build_agent_provider("anthropic", api_key="sk-test")
            mock_cls.assert_called_once_with(api_key="sk-test", model="claude-sonnet-4-20250514")

    def test_openai_via_factory(self):
        with patch("langchain_llmos.providers.openai_provider.OpenAICompatibleProvider") as mock_cls:
            mock_cls.return_value = MagicMock()
            from langchain_llmos.providers import build_agent_provider

            build_agent_provider("openai", api_key="sk-test")
            mock_cls.assert_called_once()
            kwargs = mock_cls.call_args[1]
            assert kwargs["model"] == "gpt-4o"
            assert kwargs["base_url"] == "https://api.openai.com/v1"

    def test_ollama_via_factory(self):
        with patch("langchain_llmos.providers.openai_provider.OpenAICompatibleProvider") as mock_cls:
            mock_cls.return_value = MagicMock()
            from langchain_llmos.providers import build_agent_provider

            build_agent_provider("ollama")
            mock_cls.assert_called_once()
            kwargs = mock_cls.call_args[1]
            assert kwargs["model"] == "llama3.2"
            assert kwargs["base_url"] == "http://localhost:11434/v1"

    def test_unknown_provider_raises(self):
        from langchain_llmos.providers import build_agent_provider

        with pytest.raises((ValueError, ProviderNotFoundError)):
            build_agent_provider("nonexistent_provider")

    def test_gemini_via_factory(self):
        with patch("langchain_llmos.providers.gemini_provider.GeminiProvider") as mock_cls:
            mock_cls.return_value = MagicMock()
            from langchain_llmos.providers import build_agent_provider

            build_agent_provider("gemini", api_key="test-key")
            mock_cls.assert_called_once()
            kwargs = mock_cls.call_args[1]
            assert kwargs["api_key"] == "test-key"
            assert kwargs["model"] == "gemini-2.5-flash"
