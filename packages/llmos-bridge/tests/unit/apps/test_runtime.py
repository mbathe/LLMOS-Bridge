"""Tests for AppRuntime — top-level lifecycle manager."""

import pytest
from pathlib import Path

from llmos_bridge.apps.agent_runtime import AgentRunResult, LLMProvider
from llmos_bridge.apps.compiler import CompilationError
from llmos_bridge.apps.models import AppDefinition, BrainConfig
from llmos_bridge.apps.runtime import AppRuntime, AppRuntimeError


# ─── Mock LLM ────────────────────────────────────────────────────────


class MockLLM(LLMProvider):
    def __init__(self, text="Task complete."):
        self._text = text

    async def chat(self, *, system, messages, tools, max_tokens=4096, **kwargs):
        return {"text": self._text, "tool_calls": [], "done": True}

    async def close(self):
        pass


def mock_llm_factory(brain_config):
    return MockLLM()


# ─── Fixtures ─────────────────────────────────────────────────────────


MINIMAL_YAML = """\
app:
  name: test-app
  version: "1.0"
agent:
  brain:
    provider: test
    model: test-model
  system_prompt: "You are a test assistant."
"""

MULTI_AGENT_YAML = """\
app:
  name: multi-app
  version: "1.0"
agents:
  - id: coordinator
    brain:
      provider: test
      model: test-model
    system_prompt: "You coordinate."
  - id: worker
    brain:
      provider: test
      model: test-model
    system_prompt: "You work."
"""

NO_AGENT_YAML = """\
app:
  name: broken-app
  version: "1.0"
agent: null
agents: null
"""


@pytest.fixture
def runtime():
    return AppRuntime(llm_provider_factory=mock_llm_factory)


# ─── Tests ────────────────────────────────────────────────────────────


class TestLoadString:
    def test_load_minimal(self, runtime):
        app_def = runtime.load_string(MINIMAL_YAML)
        assert app_def.app.name == "test-app"
        assert app_def.agent is not None

    def test_load_multi_agent(self, runtime):
        app_def = runtime.load_string(MULTI_AGENT_YAML)
        assert app_def.agents is not None
        assert len(app_def.agents.agents) == 2

    def test_load_invalid_yaml(self, runtime):
        with pytest.raises(CompilationError):
            runtime.load_string("{{invalid yaml")


class TestValidate:
    def test_valid(self, runtime, tmp_path):
        f = tmp_path / "test.app.yaml"
        f.write_text(MINIMAL_YAML)
        errors = runtime.validate(f)
        assert errors == []

    def test_invalid(self, runtime, tmp_path):
        f = tmp_path / "bad.app.yaml"
        f.write_text("app:\n  name: 123\n  version: true\n")
        errors = runtime.validate(f)
        assert len(errors) > 0

    def test_missing_file(self, runtime):
        errors = runtime.validate("/nonexistent/path.yaml")
        assert len(errors) > 0


class TestLoadFile:
    def test_load_file(self, runtime, tmp_path):
        f = tmp_path / "my.app.yaml"
        f.write_text(MINIMAL_YAML)
        app_def = runtime.load(f)
        assert app_def.app.name == "test-app"

    def test_file_not_found(self, runtime):
        with pytest.raises(CompilationError, match="not found"):
            runtime.load("/nonexistent/file.yaml")


class TestRun:
    @pytest.mark.asyncio
    async def test_basic_run(self, runtime):
        app_def = runtime.load_string(MINIMAL_YAML)
        result = await runtime.run(app_def, "Hello")
        assert isinstance(result, AgentRunResult)
        assert result.success is True
        assert result.output == "Task complete."

    @pytest.mark.asyncio
    async def test_run_with_variables(self, runtime):
        app_def = runtime.load_string(MINIMAL_YAML)
        result = await runtime.run(app_def, "Hello", variables={"workspace": "/custom/path"})
        assert result.success is True

    @pytest.mark.asyncio
    async def test_run_multi_agent_uses_first(self, runtime):
        app_def = runtime.load_string(MULTI_AGENT_YAML)
        result = await runtime.run(app_def, "Coordinate")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_run_no_agent_raises(self, runtime):
        """When both agent and agents are None."""
        app_def = runtime.load_string(MINIMAL_YAML)
        app_def.agent = None
        app_def.agents = None
        with pytest.raises(AppRuntimeError, match="No agent"):
            await runtime.run(app_def, "Hello")


class TestStream:
    @pytest.mark.asyncio
    async def test_stream_basic(self, runtime):
        app_def = runtime.load_string(MINIMAL_YAML)
        events = []
        async for event in runtime.stream(app_def, "Hello"):
            events.append(event)
        types = [e.type for e in events]
        assert "done" in types

    @pytest.mark.asyncio
    async def test_stream_no_agent_raises(self, runtime):
        app_def = runtime.load_string(MINIMAL_YAML)
        app_def.agent = None
        app_def.agents = None
        with pytest.raises(AppRuntimeError):
            async for _ in runtime.stream(app_def, "Hello"):
                pass


class TestStubProvider:
    @pytest.mark.asyncio
    async def test_stub_provider_used_when_no_factory(self):
        runtime = AppRuntime()
        app_def = runtime.load_string(MINIMAL_YAML)
        result = await runtime.run(app_def, "Hello")
        assert result.success is True
        assert "stub" in result.output.lower()


class TestModuleInfo:
    @pytest.mark.asyncio
    async def test_module_tools_resolved(self):
        module_info = {
            "filesystem": {
                "actions": [
                    {"name": "read_file", "description": "Read", "params": {"path": {"type": "string"}}},
                ],
            },
        }
        yaml_text = """\
app:
  name: tool-app
  version: "1.0"
agent:
  brain:
    provider: test
    model: test-model
  system_prompt: "You have tools."
  tools:
    - module: filesystem
      action: read_file
"""
        runtime = AppRuntime(module_info=module_info, llm_provider_factory=mock_llm_factory)
        app_def = runtime.load_string(yaml_text)
        result = await runtime.run(app_def, "Read something")
        assert result.success is True


class TestExpressionContext:
    @pytest.mark.asyncio
    async def test_app_context_injected(self):
        """The expression context gets app name/version."""
        recorded_system = []

        class CaptureLLM(LLMProvider):
            async def chat(self, *, system, messages, tools, max_tokens=4096, **kwargs):
                recorded_system.append(system)
                return {"text": "done", "tool_calls": [], "done": True}

            async def close(self):
                pass

        yaml_text = """\
app:
  name: ctx-test
  version: "2.0"
agent:
  brain:
    provider: test
    model: test-model
  system_prompt: "App: {{app.name}} v{{app.version}}"
"""
        runtime = AppRuntime(llm_provider_factory=lambda b: CaptureLLM())
        app_def = runtime.load_string(yaml_text)
        await runtime.run(app_def, "Hello")
        assert "ctx-test" in recorded_system[0]
        assert "2.0" in recorded_system[0]

    @pytest.mark.asyncio
    async def test_trigger_input_injected(self):
        recorded_system = []

        class CaptureLLM(LLMProvider):
            async def chat(self, *, system, messages, tools, max_tokens=4096, **kwargs):
                recorded_system.append(system)
                return {"text": "done", "tool_calls": [], "done": True}

            async def close(self):
                pass

        yaml_text = """\
app:
  name: trigger-test
  version: "1.0"
agent:
  brain:
    provider: test
    model: test-model
  system_prompt: "User said: {{trigger.input}}"
"""
        runtime = AppRuntime(llm_provider_factory=lambda b: CaptureLLM())
        app_def = runtime.load_string(yaml_text)
        await runtime.run(app_def, "Fix the bug")
        assert "Fix the bug" in recorded_system[0]
