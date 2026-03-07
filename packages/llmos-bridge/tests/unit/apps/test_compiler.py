"""Tests for the AppCompiler — YAML parsing, validation, and compilation."""

import pytest

from llmos_bridge.apps.compiler import AppCompiler, CompilationError
from llmos_bridge.apps.models import AppDefinition, LoopType


@pytest.fixture
def compiler():
    return AppCompiler()


MINIMAL_APP = """
app:
  name: test-app
  version: "1.0.0"
"""

FULL_SINGLE_AGENT_APP = """
app:
  name: full-agent
  version: "2.0.0"
  description: "A fully configured agent"
  author: "test"
  tags: [ai, test]
  max_concurrent_runs: 3
  max_turns_per_run: 100
  timeout: "1800s"

agent:
  brain:
    provider: anthropic
    model: claude-sonnet-4-6
    temperature: 0.5
    max_tokens: 4096
  system_prompt: |
    You are a helpful assistant.
    Workspace: {{workspace}}
  tools:
    - module: filesystem
      actions: [read_file, write_file]
    - module: os_exec
      actions: [run_command]
    - builtin: ask_user
  loop:
    type: reactive
    max_turns: 50
    stop_conditions:
      - "{{agent.no_tool_calls}}"
    on_tool_error: show_to_agent

triggers:
  - id: cli
    type: cli
    prompt: "> "
    mode: conversation
    greeting: "Hello!"
"""

MULTI_AGENT_APP = """
app:
  name: multi-agent
  version: "1.0.0"

agents:
  - id: orchestrator
    role: coordinator
    brain:
      provider: anthropic
      model: claude-opus-4-6
    system_prompt: "You coordinate."
    tools:
      - builtin: delegate
  - id: coder
    role: specialist
    brain:
      provider: anthropic
      model: claude-sonnet-4-6
    system_prompt: "You write code."
    tools:
      - module: filesystem
"""

APP_WITH_FLOW = """
app:
  name: flow-app
  version: "1.0.0"

agent:
  brain:
    provider: anthropic
    model: claude-sonnet-4-6
  tools:
    - module: filesystem

flow:
  - id: step1
    action: filesystem.read_file
    params:
      path: "/tmp/test.txt"
  - id: step2
    agent: default
    input: "Analyze: {{result.step1.content}}"
"""

APP_WITH_MACROS = """
app:
  name: macro-app
  version: "1.0.0"

agent:
  brain:
    provider: anthropic
    model: claude-sonnet-4-6

macros:
  - name: greet
    params:
      who:
        type: string
    body:
      - id: greeting
        action: os_exec.run_command
        params:
          command: "echo hello"

flow:
  - id: step1
    use: greet
    with:
      who: world
"""

APP_WITH_CAPABILITIES = """
app:
  name: secure-app
  version: "1.0.0"

agent:
  brain:
    provider: anthropic
    model: claude-sonnet-4-6
  tools:
    - module: filesystem
    - module: os_exec

capabilities:
  grant:
    - module: filesystem
      actions: [read_file]
      constraints:
        paths: ["{{workspace}}/**"]
    - module: os_exec
      actions: [run_command]
      constraints:
        timeout: "120s"
        forbidden_commands:
          - "rm -rf /"
  deny:
    - module: os_exec
      action: run_command
      when: "{{params.command | matches('sudo.*')}}"
      reason: "No sudo"
  approval_required:
    - module: os_exec
      action: run_command
      when: "{{params.command | matches('git push')}}"
      message: "About to push"
      timeout: "300s"
  audit:
    level: full
    redact_secrets: true
"""

APP_WITH_MEMORY = """
app:
  name: memory-app
  version: "1.0.0"

agent:
  brain:
    provider: anthropic
    model: claude-sonnet-4-6

memory:
  working:
    backend: in_memory
  conversation:
    backend: sqlite
    path: "{{data_dir}}/conv.db"
    auto_summarize: true
  episodic:
    backend: chromadb
    path: "{{data_dir}}/episodes"
    auto_record: true
    auto_recall:
      on_start: true
      query: "{{trigger.input}}"
      limit: 5
  project:
    backend: file
    path: "{{workspace}}/.llmos/MEMORY.md"
    auto_inject: true
"""


class TestMinimalApp:
    def test_compile_minimal(self, compiler):
        app_def = compiler.compile_string(MINIMAL_APP)
        assert app_def.app.name == "test-app"
        assert app_def.app.version == "1.0.0"
        # Default agent is created
        assert app_def.agent is not None

    def test_defaults(self, compiler):
        app_def = compiler.compile_string(MINIMAL_APP)
        assert app_def.app.max_concurrent_runs == 5
        assert app_def.app.max_turns_per_run == 200
        assert app_def.app.max_actions_per_turn == 50
        assert app_def.observability.streaming.enabled is True


class TestSingleAgentApp:
    def test_compile_full(self, compiler):
        app_def = compiler.compile_string(FULL_SINGLE_AGENT_APP)
        assert app_def.app.name == "full-agent"
        assert app_def.app.version == "2.0.0"
        assert app_def.app.description == "A fully configured agent"
        assert app_def.app.author == "test"
        assert app_def.app.tags == ["ai", "test"]
        assert app_def.app.max_concurrent_runs == 3

    def test_brain_config(self, compiler):
        app_def = compiler.compile_string(FULL_SINGLE_AGENT_APP)
        brain = app_def.agent.brain
        assert brain.provider == "anthropic"
        assert brain.model == "claude-sonnet-4-6"
        assert brain.temperature == 0.5
        assert brain.max_tokens == 4096

    def test_system_prompt(self, compiler):
        app_def = compiler.compile_string(FULL_SINGLE_AGENT_APP)
        assert "helpful assistant" in app_def.agent.system_prompt
        assert "{{workspace}}" in app_def.agent.system_prompt

    def test_tools(self, compiler):
        app_def = compiler.compile_string(FULL_SINGLE_AGENT_APP)
        tools = app_def.agent.tools
        assert len(tools) == 3
        assert tools[0].module == "filesystem"
        assert tools[0].actions == ["read_file", "write_file"]
        assert tools[1].module == "os_exec"
        assert tools[2].builtin == "ask_user"

    def test_loop_config(self, compiler):
        app_def = compiler.compile_string(FULL_SINGLE_AGENT_APP)
        loop = app_def.agent.loop
        assert loop.type == LoopType.reactive
        assert loop.max_turns == 50
        assert "{{agent.no_tool_calls}}" in loop.stop_conditions

    def test_triggers(self, compiler):
        app_def = compiler.compile_string(FULL_SINGLE_AGENT_APP)
        assert len(app_def.triggers) == 1
        trigger = app_def.triggers[0]
        assert trigger.id == "cli"
        assert trigger.type.value == "cli"
        assert trigger.greeting == "Hello!"

    def test_is_not_multi_agent(self, compiler):
        app_def = compiler.compile_string(FULL_SINGLE_AGENT_APP)
        assert app_def.is_multi_agent() is False

    def test_get_all_tools(self, compiler):
        app_def = compiler.compile_string(FULL_SINGLE_AGENT_APP)
        tools = app_def.get_all_tools()
        assert len(tools) == 3

    def test_get_all_module_ids(self, compiler):
        app_def = compiler.compile_string(FULL_SINGLE_AGENT_APP)
        modules = app_def.get_all_module_ids()
        assert "filesystem" in modules
        assert "os_exec" in modules


class TestMultiAgentApp:
    def test_compile(self, compiler):
        app_def = compiler.compile_string(MULTI_AGENT_APP)
        assert app_def.agents is not None
        assert len(app_def.agents.agents) == 2
        assert app_def.agent is None

    def test_is_multi_agent(self, compiler):
        app_def = compiler.compile_string(MULTI_AGENT_APP)
        assert app_def.is_multi_agent() is True

    def test_agent_ids(self, compiler):
        app_def = compiler.compile_string(MULTI_AGENT_APP)
        ids = [a.id for a in app_def.agents.agents]
        assert "orchestrator" in ids
        assert "coder" in ids

    def test_agent_roles(self, compiler):
        app_def = compiler.compile_string(MULTI_AGENT_APP)
        orchestrator = app_def.get_agent("orchestrator")
        assert orchestrator is not None
        assert orchestrator.role.value == "coordinator"

    def test_get_agent_by_id(self, compiler):
        app_def = compiler.compile_string(MULTI_AGENT_APP)
        coder = app_def.get_agent("coder")
        assert coder is not None
        assert coder.brain.model == "claude-sonnet-4-6"

    def test_get_agent_not_found(self, compiler):
        app_def = compiler.compile_string(MULTI_AGENT_APP)
        assert app_def.get_agent("nonexistent") is None


class TestFlowApp:
    def test_compile_with_flow(self, compiler):
        app_def = compiler.compile_string(APP_WITH_FLOW)
        assert app_def.flow is not None
        assert len(app_def.flow) == 2

    def test_flow_step_types(self, compiler):
        app_def = compiler.compile_string(APP_WITH_FLOW)
        assert app_def.flow[0].action == "filesystem.read_file"
        assert app_def.flow[1].agent == "default"

    def test_flow_step_ids(self, compiler):
        app_def = compiler.compile_string(APP_WITH_FLOW)
        assert app_def.flow[0].id == "step1"
        assert app_def.flow[1].id == "step2"

    def test_flow_params(self, compiler):
        app_def = compiler.compile_string(APP_WITH_FLOW)
        assert app_def.flow[0].params["path"] == "/tmp/test.txt"

    def test_flow_templates(self, compiler):
        app_def = compiler.compile_string(APP_WITH_FLOW)
        assert "{{result.step1.content}}" in app_def.flow[1].input


class TestMacros:
    def test_compile_with_macros(self, compiler):
        app_def = compiler.compile_string(APP_WITH_MACROS)
        assert len(app_def.macros) == 1
        assert app_def.macros[0].name == "greet"

    def test_macro_expansion(self, compiler):
        app_def = compiler.compile_string(APP_WITH_MACROS)
        # After expansion, the flow should contain the macro body
        assert app_def.flow is not None
        assert len(app_def.flow) >= 1


class TestCapabilities:
    def test_grants(self, compiler):
        app_def = compiler.compile_string(APP_WITH_CAPABILITIES)
        grants = app_def.capabilities.grant
        assert len(grants) == 2
        assert grants[0].module == "filesystem"
        assert grants[0].actions == ["read_file"]

    def test_constraints(self, compiler):
        app_def = compiler.compile_string(APP_WITH_CAPABILITIES)
        os_grant = app_def.capabilities.grant[1]
        assert os_grant.constraints.timeout == "120s"
        assert "rm -rf /" in os_grant.constraints.forbidden_commands

    def test_denials(self, compiler):
        app_def = compiler.compile_string(APP_WITH_CAPABILITIES)
        denials = app_def.capabilities.deny
        assert len(denials) == 1
        assert denials[0].reason == "No sudo"

    def test_approval_rules(self, compiler):
        app_def = compiler.compile_string(APP_WITH_CAPABILITIES)
        rules = app_def.capabilities.approval_required
        assert len(rules) == 1
        assert "git push" in rules[0].when

    def test_audit(self, compiler):
        app_def = compiler.compile_string(APP_WITH_CAPABILITIES)
        assert app_def.capabilities.audit.level.value == "full"
        assert app_def.capabilities.audit.redact_secrets is True


class TestMemory:
    def test_memory_levels(self, compiler):
        app_def = compiler.compile_string(APP_WITH_MEMORY)
        mem = app_def.memory
        assert mem.working.backend.value == "in_memory"
        assert mem.conversation.backend.value == "sqlite"
        assert mem.episodic.backend.value == "chromadb"
        assert mem.project.backend.value == "file"

    def test_episodic_recall(self, compiler):
        app_def = compiler.compile_string(APP_WITH_MEMORY)
        recall = app_def.memory.episodic.auto_recall
        assert recall.on_start is True
        assert recall.limit == 5
        assert recall.query == "{{trigger.input}}"


class TestValidationErrors:
    def test_invalid_yaml(self, compiler):
        with pytest.raises(CompilationError, match="YAML parse error"):
            compiler.compile_string("{{invalid yaml: [")

    def test_not_a_mapping(self, compiler):
        with pytest.raises(CompilationError, match="Expected a YAML mapping"):
            compiler.compile_string("- list item")

    def test_missing_app_name(self, compiler):
        with pytest.raises(CompilationError, match="validation failed"):
            compiler.compile_string("app:\n  version: '1.0.0'")

    def test_both_agent_and_agents(self, compiler):
        yaml_text = """
app:
  name: bad
agent:
  brain:
    model: test
agents:
  - id: a1
    brain:
      model: test
"""
        with pytest.raises(CompilationError, match="Cannot define both"):
            compiler.compile_string(yaml_text)

    def test_duplicate_agent_ids(self, compiler):
        yaml_text = """
app:
  name: bad
agents:
  - id: same
    brain:
      model: test
  - id: same
    brain:
      model: test
"""
        with pytest.raises(CompilationError, match="Agent IDs must be unique"):
            compiler.compile_string(yaml_text)

    def test_duplicate_flow_step_ids(self, compiler):
        yaml_text = """
app:
  name: bad
flow:
  - id: dup
    action: test.do
  - id: dup
    action: test.do2
"""
        with pytest.raises(CompilationError, match="Duplicate flow step ID"):
            compiler.compile_string(yaml_text)

    def test_tool_both_module_and_builtin(self, compiler):
        yaml_text = """
app:
  name: bad
agent:
  tools:
    - module: filesystem
      builtin: ask_user
"""
        with pytest.raises(CompilationError, match="both 'module' and 'builtin'"):
            compiler.compile_string(yaml_text)

    def test_file_not_found(self, compiler):
        with pytest.raises(CompilationError, match="File not found"):
            compiler.compile_file("/nonexistent/path.yaml")

    def test_wrong_extension(self, compiler, tmp_path):
        f = tmp_path / "test.json"
        f.write_text("{}")
        with pytest.raises(CompilationError, match="Expected .yaml"):
            compiler.compile_file(f)


class TestTriggerDeepValidation:
    """Tests for step 18: deep trigger validation."""

    def test_duplicate_http_paths(self, compiler):
        yaml_text = """
app:
  name: bad
triggers:
  - type: http
    path: /api
    method: POST
  - type: http
    path: /api
    method: POST
"""
        with pytest.raises(CompilationError, match="duplicate HTTP path"):
            compiler.compile_string(yaml_text)

    def test_http_path_must_start_with_slash(self, compiler):
        yaml_text = """
app:
  name: bad
triggers:
  - type: http
    path: api/run
"""
        with pytest.raises(CompilationError, match="must start with '/'"):
            compiler.compile_string(yaml_text)

    def test_transform_mismatched_brackets(self, compiler):
        yaml_text = """
app:
  name: bad
triggers:
  - type: schedule
    cron: "0 9 * * *"
    transform: "Task: {{input"
"""
        with pytest.raises(CompilationError, match="mismatched template brackets"):
            compiler.compile_string(yaml_text)

    def test_valid_http_trigger_passes(self, compiler):
        yaml_text = """
app:
  name: good
triggers:
  - type: http
    path: /api/run
    method: POST
  - type: http
    path: /api/review
    method: POST
"""
        app_def = compiler.compile_string(yaml_text)
        assert len(app_def.triggers) == 2

    def test_valid_schedule_trigger_passes(self, compiler):
        yaml_text = """
app:
  name: good
triggers:
  - type: schedule
    cron: "0 9 * * 1-5"
    transform: "Daily report: {{input}}"
"""
        app_def = compiler.compile_string(yaml_text)
        assert len(app_def.triggers) == 1
