"""Tests for reference validations — result refs, variable refs, action params,
approval refs, and brain provider validation."""

import logging

import pytest

from llmos_bridge.apps.compiler import AppCompiler, CompilationError


FAKE_MODULE_INFO = {
    "filesystem": {
        "actions": [
            {"name": "read_file", "description": "Read file", "params": {
                "path": {"type": "string", "required": True, "description": "File path"},
                "encoding": {"type": "string", "required": False, "description": "Encoding", "default": "utf-8"},
            }},
            {"name": "write_file", "description": "Write file", "params": {
                "path": {"type": "string", "required": True, "description": "File path"},
                "content": {"type": "string", "required": True, "description": "Content"},
            }},
        ],
    },
    "os_exec": {
        "actions": [
            {"name": "run_command", "description": "Run cmd", "params": {
                "command": {"type": "string", "required": True, "description": "Command"},
                "working_directory": {"type": "string", "required": False, "description": "CWD"},
            }},
        ],
    },
}


@pytest.fixture
def compiler():
    return AppCompiler()


@pytest.fixture
def strict_compiler():
    return AppCompiler(module_info=FAKE_MODULE_INFO)


# ── Result Reference Tests ──────────────────────────────────────────


class TestResultRefs:
    """Test that {{result.step_id}} references are validated."""

    def test_valid_result_ref(self, compiler, caplog):
        yaml_text = """
app:
  name: test-valid-ref
agent:
  id: coder
flow:
  - id: fetch
    action: filesystem.read_file
    params: { path: "/tmp/test" }
  - id: process
    agent: coder
    input: "Process: {{result.fetch}}"
"""
        with caplog.at_level(logging.WARNING):
            compiler.compile_string(yaml_text)
        ref_warnings = [r for r in caplog.records if "unknown step ID" in r.message]
        assert len(ref_warnings) == 0

    def test_unknown_result_ref_warned(self, compiler, caplog):
        yaml_text = """
app:
  name: test-bad-ref
agent:
  id: coder
flow:
  - id: step1
    agent: coder
    input: "Data: {{result.nonexistent_step}}"
"""
        with caplog.at_level(logging.WARNING):
            compiler.compile_string(yaml_text)
        ref_warnings = [r for r in caplog.records if "unknown step ID" in r.message]
        assert len(ref_warnings) == 1
        assert "nonexistent_step" in ref_warnings[0].message

    def test_result_ref_to_nested_step(self, compiler, caplog):
        yaml_text = """
app:
  name: test-nested-ref
agent:
  id: coder
flow:
  - id: outer
    sequence:
      - id: inner
        action: os_exec.run_command
        params: { command: "echo hi" }
  - id: use_it
    agent: coder
    input: "{{result.inner}}"
"""
        with caplog.at_level(logging.WARNING):
            compiler.compile_string(yaml_text)
        ref_warnings = [r for r in caplog.records if "unknown step ID" in r.message]
        assert len(ref_warnings) == 0


# ── Variable Reference Tests ────────────────────────────────────────


class TestVariableRefs:
    """Test that {{variable}} references are validated against defined variables."""

    def test_defined_variable_ok(self, compiler, caplog):
        yaml_text = """
app:
  name: test-var-ok
variables:
  workspace: "/tmp"
agent:
  system_prompt: "Workspace: {{workspace}}"
"""
        with caplog.at_level(logging.WARNING):
            compiler.compile_string(yaml_text)
        var_warnings = [r for r in caplog.records
                        if "expression_warning" in r.message and "unknown variable" in r.message]
        assert len(var_warnings) == 0

    def test_undefined_variable_warned(self, compiler, caplog):
        yaml_text = """
app:
  name: test-var-bad
agent:
  system_prompt: "Dir: {{my_undefined_var}}"
"""
        with caplog.at_level(logging.WARNING):
            compiler.compile_string(yaml_text)
        var_warnings = [r for r in caplog.records
                        if "expression_warning" in r.message and "unknown variable" in r.message]
        assert len(var_warnings) == 1
        assert "my_undefined_var" in var_warnings[0].message

    def test_builtin_namespaces_ok(self, compiler, caplog):
        """Built-in namespaces like result, trigger, env should not warn."""
        yaml_text = """
app:
  name: test-builtins
agent:
  system_prompt: "{{result.step1}} {{trigger.input}} {{env.HOME}} {{memory.key}}"
"""
        with caplog.at_level(logging.WARNING):
            compiler.compile_string(yaml_text)
        var_warnings = [r for r in caplog.records
                        if "expression_warning" in r.message and "unknown variable" in r.message]
        assert len(var_warnings) == 0

    def test_iteration_var_ok(self, compiler, caplog):
        """Map iteration variable (item) should not warn."""
        yaml_text = """
app:
  name: test-iter
agent:
  id: coder
flow:
  - id: map_step
    map:
      over: "{{result.list}}"
      as: entry
      step:
        - id: process
          agent: coder
          input: "Process {{entry}}"
"""
        with caplog.at_level(logging.WARNING):
            compiler.compile_string(yaml_text)
        var_warnings = [r for r in caplog.records
                        if "expression_warning" in r.message and "unknown variable" in r.message]
        assert len(var_warnings) == 0


# ── Action Param Tests ──────────────────────────────────────────────


class TestActionParams:
    """Test flow step param validation against module action schemas."""

    def test_valid_params(self, strict_compiler):
        yaml_text = """
app:
  name: test-valid-params
flow:
  - id: step1
    action: filesystem.read_file
    params:
      path: "/tmp/test.txt"
"""
        app_def = strict_compiler.compile_string(yaml_text)
        assert app_def.flow[0].action == "filesystem.read_file"

    def test_unknown_param_rejected(self, strict_compiler):
        yaml_text = """
app:
  name: test-unknown-param
flow:
  - id: step1
    action: filesystem.read_file
    params:
      path: "/tmp/test.txt"
      nonexistent_param: "bad"
"""
        with pytest.raises(CompilationError, match="unknown param 'nonexistent_param'"):
            strict_compiler.compile_string(yaml_text)

    def test_missing_required_param_rejected(self, strict_compiler):
        yaml_text = """
app:
  name: test-missing-param
flow:
  - id: step1
    action: filesystem.write_file
    params:
      path: "/tmp/test.txt"
"""
        with pytest.raises(CompilationError, match="missing required param 'content'"):
            strict_compiler.compile_string(yaml_text)

    def test_optional_param_ok(self, strict_compiler):
        """Optional params can be omitted."""
        yaml_text = """
app:
  name: test-optional
flow:
  - id: step1
    action: filesystem.read_file
    params:
      path: "/tmp/test.txt"
"""
        app_def = strict_compiler.compile_string(yaml_text)
        assert app_def.flow[0].params["path"] == "/tmp/test.txt"

    def test_template_params_skip_required_check(self, strict_compiler):
        """When params contain templates, skip required check (resolved at runtime)."""
        yaml_text = """
app:
  name: test-template-params
flow:
  - id: step1
    action: filesystem.write_file
    params:
      path: "{{result.get_path}}"
      content: "{{result.get_content}}"
"""
        app_def = strict_compiler.compile_string(yaml_text)
        assert len(app_def.flow) == 1

    def test_no_module_info_skips_param_check(self, compiler):
        """Without module_info, param validation is skipped."""
        yaml_text = """
app:
  name: test-no-info
flow:
  - id: step1
    action: filesystem.read_file
    params:
      totally_fake_param: "works without module_info"
"""
        app_def = compiler.compile_string(yaml_text)
        assert len(app_def.flow) == 1

    def test_internal_params_ignored(self, strict_compiler):
        """Params starting with _ are internal and should not be validated."""
        yaml_text = """
app:
  name: test-internal
flow:
  - id: step1
    action: os_exec.run_command
    params:
      command: "echo hi"
      _stream: true
"""
        app_def = strict_compiler.compile_string(yaml_text)
        assert app_def.flow[0].params["_stream"] is True


# ── Approval Rule Reference Tests ──────────────────────────────────


class TestApprovalRefs:
    """Test approval_required module/action validation."""

    def test_valid_approval_ref(self, strict_compiler):
        yaml_text = """
app:
  name: test-approval-ok
agent:
  tools:
    - module: os_exec
capabilities:
  approval_required:
    - module: os_exec
      action: run_command
      message: "Allow command?"
"""
        app_def = strict_compiler.compile_string(yaml_text)
        assert len(app_def.capabilities.approval_required) == 1

    def test_unknown_module_in_approval(self, strict_compiler):
        yaml_text = """
app:
  name: test-approval-bad-module
agent:
  tools:
    - module: filesystem
capabilities:
  approval_required:
    - module: nonexistent_module
      action: some_action
      message: "Allow?"
"""
        with pytest.raises(CompilationError, match="unknown module 'nonexistent_module'"):
            strict_compiler.compile_string(yaml_text)

    def test_unknown_action_in_approval(self, strict_compiler):
        yaml_text = """
app:
  name: test-approval-bad-action
agent:
  tools:
    - module: filesystem
capabilities:
  approval_required:
    - module: filesystem
      action: nonexistent_action
      message: "Allow?"
"""
        with pytest.raises(CompilationError, match="unknown action 'nonexistent_action'"):
            strict_compiler.compile_string(yaml_text)

    def test_no_module_info_skips_approval_check(self, compiler):
        yaml_text = """
app:
  name: test-no-info
capabilities:
  approval_required:
    - module: fake_module
      action: fake_action
"""
        app_def = compiler.compile_string(yaml_text)
        assert len(app_def.capabilities.approval_required) == 1


# ── Brain Provider Tests ────────────────────────────────────────────


class TestBrainProvider:
    """Test that brain.provider is validated."""

    def test_valid_providers(self, compiler):
        for provider in ("anthropic", "openai", "ollama", "bedrock", "vertex", "azure", "local"):
            yaml_text = f"""
app:
  name: test-{provider}
agent:
  brain:
    provider: {provider}
    model: some-model
"""
            app_def = compiler.compile_string(yaml_text)
            assert app_def.agent.brain.provider == provider

    def test_unknown_provider_warned(self, compiler, caplog):
        yaml_text = """
app:
  name: test-bad-provider
agent:
  brain:
    provider: nonexistent_ai_company
    model: some-model
"""
        with caplog.at_level(logging.WARNING):
            compiler.compile_string(yaml_text)
        assert "nonexistent_ai_company" in caplog.text

    def test_template_provider_skipped(self, compiler):
        yaml_text = """
app:
  name: test-template-provider
agent:
  brain:
    provider: "{{env.LLM_PROVIDER}}"
    model: some-model
"""
        app_def = compiler.compile_string(yaml_text)
        assert app_def.agent.brain.provider == "{{env.LLM_PROVIDER}}"

    def test_multi_agent_provider_validated(self, compiler, caplog):
        yaml_text = """
app:
  name: test-multi-provider
agents:
  - id: planner
    brain:
      provider: fake_provider
      model: x
    role: coordinator
  - id: worker
    brain:
      provider: anthropic
      model: y
    role: specialist
flow:
  - id: step1
    agent: planner
    input: "Plan"
"""
        with caplog.at_level(logging.WARNING):
            compiler.compile_string(yaml_text)
        assert "fake_provider" in caplog.text

    def test_no_brain_ok(self, compiler):
        """Agent without brain uses defaults — should pass."""
        yaml_text = """
app:
  name: test-no-brain
agent:
  tools:
    - module: filesystem
"""
        app_def = compiler.compile_string(yaml_text)
        assert app_def.agent is not None
