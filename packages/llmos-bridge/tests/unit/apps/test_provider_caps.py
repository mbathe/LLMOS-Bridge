"""Tests for the provider capability registry and param filtering.

Validates:
1. filter_params_for_provider strips unsupported params
2. filter_params_for_provider resolves mutual exclusion
3. Unknown providers pass through all params
4. Compiler rejects mutually exclusive brain params
5. Compiler handles fallback provider inheritance
6. Runtime filters params before LLM call
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

from llmos_bridge.apps.providers import (
    PROVIDER_CAPS,
    filter_params_for_provider,
)


# ─── filter_params_for_provider ─────────────────────────────────


class TestFilterParamsForProvider:
    def test_anthropic_strips_unsupported_param(self):
        """Anthropic does not support frequency_penalty."""
        result = filter_params_for_provider("anthropic", {
            "temperature": 0.5,
            "frequency_penalty": 0.3,
        })
        assert result == {"temperature": 0.5}

    def test_anthropic_mutual_exclusion_keeps_first(self):
        """When both temperature and top_p are set, keep temperature (first)."""
        result = filter_params_for_provider("anthropic", {
            "temperature": 0.5,
            "top_p": 0.9,
        })
        assert result == {"temperature": 0.5}
        assert "top_p" not in result

    def test_anthropic_single_param_passes(self):
        """A single param should pass through fine."""
        result = filter_params_for_provider("anthropic", {"top_p": 0.9})
        assert result == {"top_p": 0.9}

    def test_openai_allows_both_temperature_and_top_p(self):
        """OpenAI allows temperature + top_p together."""
        result = filter_params_for_provider("openai", {
            "temperature": 0.5,
            "top_p": 0.9,
        })
        assert result == {"temperature": 0.5, "top_p": 0.9}

    def test_openai_allows_frequency_penalty(self):
        result = filter_params_for_provider("openai", {
            "frequency_penalty": 0.3,
            "presence_penalty": 0.5,
        })
        assert result == {"frequency_penalty": 0.3, "presence_penalty": 0.5}

    def test_unknown_provider_passes_all(self):
        """Unknown providers should not have params stripped."""
        params = {"temperature": 0.5, "top_p": 0.9, "custom_param": True}
        result = filter_params_for_provider("my_custom_provider", params)
        assert result == params

    def test_empty_params(self):
        result = filter_params_for_provider("anthropic", {})
        assert result == {}

    def test_bedrock_mutual_exclusion(self):
        """Bedrock (Anthropic backend) also has temperature/top_p exclusion."""
        result = filter_params_for_provider("bedrock", {
            "temperature": 0.7,
            "top_p": 0.95,
        })
        assert "top_p" not in result
        assert result["temperature"] == 0.7


# ─── PROVIDER_CAPS registry ─────────────────────────────────────


class TestProviderCaps:
    def test_all_known_providers_have_max_tokens(self):
        """Every known provider must support max_tokens."""
        for name, caps in PROVIDER_CAPS.items():
            assert "max_tokens" in caps.supported_params, f"{name} missing max_tokens"

    def test_anthropic_has_mutual_exclusion(self):
        caps = PROVIDER_CAPS["anthropic"]
        assert len(caps.mutually_exclusive) > 0
        assert frozenset({"temperature", "top_p"}) in caps.mutually_exclusive

    def test_openai_has_no_mutual_exclusion(self):
        caps = PROVIDER_CAPS["openai"]
        assert len(caps.mutually_exclusive) == 0


# ─── Compiler validation ─────────────────────────────────────────


class TestCompilerBrainParams:
    def test_mutual_exclusion_caught(self):
        """Compiler should reject temperature + top_p for Anthropic."""
        from llmos_bridge.apps.compiler import AppCompiler, CompilationError

        yaml_text = """
app:
  name: test
  version: "1.0"
agent:
  role: specialist
  brain:
    provider: anthropic
    model: claude-sonnet-4-6
    temperature: 0.5
    top_p: 0.9
  system_prompt: test
"""
        compiler = AppCompiler()
        with pytest.raises(CompilationError) as exc_info:
            compiler.compile_string(yaml_text, source="test.yaml")
        msg = str(exc_info.value)
        assert "temperature" in msg and "top_p" in msg

    def test_single_temperature_passes(self):
        """Only temperature (no top_p) should compile fine."""
        from llmos_bridge.apps.compiler import AppCompiler

        yaml_text = """
app:
  name: test
  version: "1.0"
agent:
  role: specialist
  brain:
    provider: anthropic
    model: claude-sonnet-4-6
    temperature: 0.5
  system_prompt: test
"""
        compiler = AppCompiler()
        result = compiler.compile_string(yaml_text, source="test.yaml")
        assert result is not None  # should compile without error

    def test_openai_both_params_pass(self):
        """OpenAI allows both temperature + top_p."""
        from llmos_bridge.apps.compiler import AppCompiler

        yaml_text = """
app:
  name: test
  version: "1.0"
agent:
  role: specialist
  brain:
    provider: openai
    model: gpt-4o
    temperature: 0.5
    top_p: 0.9
  system_prompt: test
"""
        compiler = AppCompiler()
        result = compiler.compile_string(yaml_text, source="test.yaml")
        assert result is not None

    def test_multi_agent_brain_params_validated(self):
        """Mutually exclusive params caught in multi-agent brains too."""
        from llmos_bridge.apps.compiler import AppCompiler, CompilationError

        yaml_text = """
app:
  name: test
  version: "1.0"
agents:
  coordinator: planner
  agents:
    - id: planner
      role: coordinator
      brain:
        provider: anthropic
        model: claude-sonnet-4-6
        temperature: 0.5
        top_p: 0.9
      system_prompt: plan things
    - id: worker
      role: specialist
      brain:
        provider: anthropic
        model: claude-haiku-4-5-20251001
      system_prompt: do things
"""
        compiler = AppCompiler()
        with pytest.raises(CompilationError) as exc_info:
            compiler.compile_string(yaml_text, source="test.yaml")
        msg = str(exc_info.value)
        assert "planner" in msg
        assert "temperature" in msg and "top_p" in msg


# ─── Runtime safety net ──────────────────────────────────────────


class TestRuntimeParamFiltering:
    @pytest.mark.asyncio
    async def test_runtime_filters_conflicting_params(self):
        """AgentRuntime should filter params via provider caps before calling LLM."""
        from llmos_bridge.apps.agent_runtime import AgentRuntime, LLMProvider
        from llmos_bridge.apps.models import AgentConfig, BrainConfig

        received_kwargs = {}

        async def capture_chat(**kwargs):
            received_kwargs.update(kwargs)
            return {"text": "Done", "tool_calls": [], "done": True}

        llm = AsyncMock(spec=LLMProvider)
        llm.chat = capture_chat
        llm.close = AsyncMock()

        brain = BrainConfig(
            provider="anthropic",
            model="claude-sonnet-4-6",
            temperature=0.5,
            top_p=0.9,
        )
        config = AgentConfig(role="specialist", brain=brain, system_prompt="test")

        agent = AgentRuntime(
            agent_config=config,
            llm=llm,
            tools=[],
            execute_tool=AsyncMock(),
        )
        await agent.run("test")

        # temperature should be kept, top_p should be filtered out
        assert received_kwargs.get("temperature") == 0.5
        assert "top_p" not in received_kwargs

    @pytest.mark.asyncio
    async def test_runtime_passes_single_param(self):
        """A single temperature should pass through fine."""
        from llmos_bridge.apps.agent_runtime import AgentRuntime, LLMProvider
        from llmos_bridge.apps.models import AgentConfig, BrainConfig

        received_kwargs = {}

        async def capture_chat(**kwargs):
            received_kwargs.update(kwargs)
            return {"text": "Done", "tool_calls": [], "done": True}

        llm = AsyncMock(spec=LLMProvider)
        llm.chat = capture_chat
        llm.close = AsyncMock()

        brain = BrainConfig(
            provider="anthropic",
            model="claude-sonnet-4-6",
            temperature=0.7,
        )
        config = AgentConfig(role="specialist", brain=brain, system_prompt="test")

        agent = AgentRuntime(
            agent_config=config,
            llm=llm,
            tools=[],
            execute_tool=AsyncMock(),
        )
        await agent.run("test")

        assert received_kwargs.get("temperature") == 0.7
        assert "top_p" not in received_kwargs


# ─── Fallback provider inheritance ───────────────────────────────


class TestFallbackProviderInheritance:
    def test_fallback_brain_provider_none_by_default(self):
        """FallbackBrain.provider should default to None (inherit from parent)."""
        from llmos_bridge.apps.models import FallbackBrain

        fb = FallbackBrain(model="claude-haiku-4-5-20251001")
        assert fb.provider is None

    def test_fallback_brain_explicit_provider_kept(self):
        from llmos_bridge.apps.models import FallbackBrain

        fb = FallbackBrain(provider="openai", model="gpt-4o")
        assert fb.provider == "openai"
