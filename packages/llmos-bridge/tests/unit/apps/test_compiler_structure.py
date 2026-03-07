"""Tests for structural/organizational compiler validations — step polymorphism,
multi-agent structure, flow completeness, and macro structure."""

import logging

import pytest

from llmos_bridge.apps.compiler import AppCompiler, CompilationError


@pytest.fixture
def compiler():
    return AppCompiler()


# ── Step Polymorphism Tests ─────────────────────────────────────────


class TestStepPolymorphism:
    """Test that each flow step has exactly one primary type."""

    def test_single_type_ok(self, compiler):
        yaml_text = """
app:
  name: test-single
flow:
  - id: step1
    action: os_exec.run_command
    params: { command: "echo hi" }
"""
        app_def = compiler.compile_string(yaml_text)
        assert len(app_def.flow) == 1

    def test_action_and_agent_rejected(self, compiler):
        yaml_text = """
app:
  name: test-conflict
agent:
  id: coder
flow:
  - id: step1
    action: os_exec.run_command
    agent: coder
    input: "Do something"
    params: {}
"""
        with pytest.raises(CompilationError, match="multiple types.*action.*agent"):
            compiler.compile_string(yaml_text)

    def test_action_and_branch_rejected(self, compiler):
        yaml_text = """
app:
  name: test-conflict2
flow:
  - id: step1
    action: os_exec.run_command
    params: {}
    branch:
      "on": "{{result.check}}"
      cases:
        "ok":
          - id: ok_step
            action: os_exec.run_command
            params: {}
"""
        with pytest.raises(CompilationError, match="multiple types.*action.*branch"):
            compiler.compile_string(yaml_text)

    def test_goto_coexists_with_try(self, compiler):
        """goto is a modifier, not a primary type — should coexist."""
        yaml_text = """
app:
  name: test-goto-try
agent:
  id: coder
flow:
  - id: step1
    try:
      - action: os_exec.run_command
        params: {}
    catch:
      - error: "*"
        then: fail
    goto: step2
  - id: step2
    agent: coder
    input: "Done"
"""
        app_def = compiler.compile_string(yaml_text)
        assert app_def.flow[0].goto == "step2"

    def test_goto_coexists_with_emit(self, compiler):
        """goto + emit is valid — emit, then jump."""
        yaml_text = """
app:
  name: test-goto-emit
agent:
  id: coder
flow:
  - id: step1
    emit:
      topic: "llmos.custom"
      data: { status: "done" }
    goto: step2
  - id: step2
    agent: coder
    input: "Continue"
"""
        app_def = compiler.compile_string(yaml_text)
        assert app_def.flow[0].goto == "step2"

    def test_nested_conflict_detected(self, compiler):
        yaml_text = """
app:
  name: test-nested-conflict
agent:
  id: coder
flow:
  - id: outer
    sequence:
      - id: inner
        action: os_exec.run_command
        agent: coder
        input: "Both"
        params: {}
"""
        with pytest.raises(CompilationError, match="multiple types"):
            compiler.compile_string(yaml_text)


# ── Multi-Agent Structure Tests ─────────────────────────────────────


class TestMultiAgentStructure:
    """Test multi-agent structural requirements."""

    def test_multi_agent_with_flow_ok(self, compiler):
        yaml_text = """
app:
  name: test-multi-ok
agents:
  - id: planner
    role: coordinator
  - id: worker
    role: specialist
flow:
  - id: step1
    agent: planner
    input: "Plan"
  - id: step2
    agent: worker
    input: "Execute"
"""
        app_def = compiler.compile_string(yaml_text)
        assert app_def.is_multi_agent()

    def test_multi_agent_without_flow_warns(self, compiler, caplog):
        yaml_text = """
app:
  name: test-multi-no-flow
agents:
  - id: planner
    role: coordinator
  - id: worker
    role: specialist
"""
        with caplog.at_level(logging.WARNING):
            app_def = compiler.compile_string(yaml_text)
        assert "no 'flow:' block" in caplog.text
        assert app_def.is_multi_agent()

    def test_multi_agent_missing_id(self, compiler):
        yaml_text = """
app:
  name: test-no-id
agents:
  - role: coordinator
  - id: worker
    role: specialist
flow:
  - id: step1
    agent: worker
    input: "Work"
"""
        with pytest.raises(CompilationError, match="must have an 'id' field"):
            compiler.compile_string(yaml_text)

    def test_single_agent_no_flow_ok(self, compiler):
        """Single agent apps don't need a flow."""
        yaml_text = """
app:
  name: test-single
agent:
  id: coder
  tools:
    - module: filesystem
"""
        app_def = compiler.compile_string(yaml_text)
        assert not app_def.is_multi_agent()


# ── Flow Step Completeness Tests ────────────────────────────────────


class TestFlowCompleteness:
    """Test flow step completeness checks."""

    def test_agent_step_with_input_ok(self, compiler):
        yaml_text = """
app:
  name: test-agent-ok
agent:
  id: coder
flow:
  - id: step1
    agent: coder
    input: "Hello"
"""
        app_def = compiler.compile_string(yaml_text)
        assert app_def.flow[0].input == "Hello"

    def test_agent_step_without_input_rejected(self, compiler):
        yaml_text = """
app:
  name: test-agent-no-input
agent:
  id: coder
flow:
  - id: step1
    agent: coder
"""
        with pytest.raises(CompilationError, match="has no 'input:' field"):
            compiler.compile_string(yaml_text)

    def test_branch_with_cases_ok(self, compiler):
        yaml_text = """
app:
  name: test-branch-ok
agent:
  id: coder
flow:
  - id: step1
    branch:
      "on": "{{result.check}}"
      cases:
        "ok":
          - id: ok_step
            agent: coder
            input: "OK"
"""
        app_def = compiler.compile_string(yaml_text)
        assert "ok" in app_def.flow[0].branch.cases

    def test_branch_with_only_default_ok(self, compiler):
        yaml_text = """
app:
  name: test-branch-default
agent:
  id: coder
flow:
  - id: step1
    branch:
      "on": "{{result.check}}"
      default:
        - id: fallback
          agent: coder
          input: "Fallback"
"""
        app_def = compiler.compile_string(yaml_text)
        assert app_def.flow[0].branch.default is not None

    def test_branch_empty_rejected(self, compiler):
        yaml_text = """
app:
  name: test-branch-empty
flow:
  - id: step1
    branch:
      "on": "{{result.check}}"
"""
        with pytest.raises(CompilationError, match="no 'cases:' and no 'default:'"):
            compiler.compile_string(yaml_text)

    def test_parallel_empty_rejected(self, compiler):
        yaml_text = """
app:
  name: test-parallel-empty
flow:
  - id: step1
    parallel:
      steps: []
"""
        with pytest.raises(CompilationError, match="'parallel:' with no steps"):
            compiler.compile_string(yaml_text)

    def test_parallel_with_steps_ok(self, compiler):
        yaml_text = """
app:
  name: test-parallel-ok
agent:
  id: coder
flow:
  - id: step1
    parallel:
      steps:
        - id: a
          agent: coder
          input: "A"
        - id: b
          agent: coder
          input: "B"
"""
        app_def = compiler.compile_string(yaml_text)
        assert len(app_def.flow[0].parallel.steps) == 2

    def test_race_needs_two_steps(self, compiler):
        yaml_text = """
app:
  name: test-race-one
agent:
  id: coder
flow:
  - id: step1
    race:
      steps:
        - id: only
          agent: coder
          input: "Solo"
"""
        with pytest.raises(CompilationError, match="fewer than 2 steps"):
            compiler.compile_string(yaml_text)


# ── Macro Structure Tests ──────────────────────────────────────────


class TestMacroStructure:
    """Test macro structural validation."""

    def test_macro_with_body_ok(self, compiler):
        yaml_text = """
app:
  name: test-macro-ok
agent:
  id: coder
macros:
  - name: my_macro
    body:
      - id: step1
        agent: coder
        input: "Hello"
"""
        app_def = compiler.compile_string(yaml_text)
        assert len(app_def.macros) == 1

    def test_macro_empty_body_rejected(self, compiler):
        yaml_text = """
app:
  name: test-macro-empty
macros:
  - name: empty_macro
    body: []
"""
        with pytest.raises(CompilationError, match="empty body"):
            compiler.compile_string(yaml_text)

    def test_macro_unused_param_warned(self, compiler, caplog):
        yaml_text = """
app:
  name: test-macro-unused
agent:
  id: coder
macros:
  - name: my_macro
    params:
      used_param: { type: string }
      unused_param: { type: string }
    body:
      - id: step1
        agent: coder
        input: "Value is {{macro.used_param}}"
"""
        with caplog.at_level(logging.WARNING):
            compiler.compile_string(yaml_text)
        macro_warns = [r for r in caplog.records if "declares param" in r.message]
        warned_params = [r.message for r in macro_warns]
        assert any("unused_param" in w for w in warned_params)
        assert not any("'used_param'" in w for w in warned_params)

    def test_macro_all_params_used_no_warning(self, compiler, caplog):
        yaml_text = """
app:
  name: test-macro-all-used
agent:
  id: coder
macros:
  - name: my_macro
    params:
      path: { type: string }
      max_lines: { type: integer, default: 200 }
    body:
      - id: read
        action: filesystem.read_file
        params: { path: "{{macro.path}}", lines: "{{macro.max_lines}}" }
"""
        with caplog.at_level(logging.WARNING):
            compiler.compile_string(yaml_text)
        macro_warnings = [r for r in caplog.records if "declares param" in r.message]
        assert len(macro_warnings) == 0
