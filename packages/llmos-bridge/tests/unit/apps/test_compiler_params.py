"""Tests for parameter-level compiler validations — flow action format,
trigger required fields, duration strings, cron syntax, and on_error values."""

import pytest

from llmos_bridge.apps.compiler import AppCompiler, CompilationError


@pytest.fixture
def compiler():
    return AppCompiler()


# ── Flow Action Format Tests ────────────────────────────────────────


class TestFlowActionFormat:
    """Test that flow step actions must be in 'module.action' format."""

    def test_valid_action_format(self, compiler):
        yaml_text = """
app:
  name: test-valid
flow:
  - id: step1
    action: filesystem.read_file
    params: { path: "/tmp/test" }
"""
        app_def = compiler.compile_string(yaml_text)
        assert app_def.flow[0].action == "filesystem.read_file"

    def test_invalid_action_no_dot(self, compiler):
        yaml_text = """
app:
  name: test-bad
flow:
  - id: step1
    action: read_file
    params: {}
"""
        with pytest.raises(CompilationError, match="must be in 'module.action' format"):
            compiler.compile_string(yaml_text)

    def test_template_action_skipped(self, compiler):
        yaml_text = """
app:
  name: test-template
flow:
  - id: step1
    action: "{{dynamic_action}}"
    params: {}
"""
        app_def = compiler.compile_string(yaml_text)
        assert app_def.flow[0].action == "{{dynamic_action}}"

    def test_nested_action_validated(self, compiler):
        yaml_text = """
app:
  name: test-nested
agent:
  id: coder
flow:
  - id: step1
    branch:
      "on": "{{result.check}}"
      cases:
        "ok":
          - id: ok_step
            action: bad_format
            params: {}
"""
        with pytest.raises(CompilationError, match="must be in 'module.action' format"):
            compiler.compile_string(yaml_text)

    def test_agent_step_no_action_ok(self, compiler):
        """Agent steps don't have action field — should pass."""
        yaml_text = """
app:
  name: test-agent-step
agent:
  id: coder
flow:
  - id: step1
    agent: coder
    input: "Hello"
"""
        app_def = compiler.compile_string(yaml_text)
        assert app_def.flow[0].agent == "coder"


# ── Trigger Required Fields Tests ───────────────────────────────────


class TestTriggerValidation:
    """Test trigger-type-specific required field validation."""

    def test_cli_trigger_no_required_fields(self, compiler):
        """CLI triggers have no extra required fields."""
        yaml_text = """
app:
  name: test-cli
triggers:
  - type: cli
    mode: conversation
"""
        app_def = compiler.compile_string(yaml_text)
        assert len(app_def.triggers) == 1

    def test_schedule_requires_cron_or_when(self, compiler):
        yaml_text = """
app:
  name: test-schedule
triggers:
  - type: schedule
"""
        with pytest.raises(CompilationError, match="schedule trigger requires 'cron' or 'when'"):
            compiler.compile_string(yaml_text)

    def test_schedule_with_cron_ok(self, compiler):
        yaml_text = """
app:
  name: test-schedule-ok
triggers:
  - type: schedule
    cron: "*/30 * * * *"
    input: "Run check"
"""
        app_def = compiler.compile_string(yaml_text)
        assert app_def.triggers[0].cron == "*/30 * * * *"

    def test_schedule_with_when_ok(self, compiler):
        yaml_text = """
app:
  name: test-schedule-when
triggers:
  - type: schedule
    when: "every 30 minutes"
    input: "Run check"
"""
        app_def = compiler.compile_string(yaml_text)
        assert app_def.triggers[0].when == "every 30 minutes"

    def test_http_requires_path(self, compiler):
        yaml_text = """
app:
  name: test-http
triggers:
  - type: http
"""
        with pytest.raises(CompilationError, match="http trigger requires 'path'"):
            compiler.compile_string(yaml_text)

    def test_http_with_path_ok(self, compiler):
        yaml_text = """
app:
  name: test-http-ok
triggers:
  - type: http
    path: /api/run
"""
        app_def = compiler.compile_string(yaml_text)
        assert app_def.triggers[0].path == "/api/run"

    def test_webhook_requires_path(self, compiler):
        yaml_text = """
app:
  name: test-webhook
triggers:
  - type: webhook
"""
        with pytest.raises(CompilationError, match="webhook trigger requires 'path'"):
            compiler.compile_string(yaml_text)

    def test_watch_requires_paths(self, compiler):
        yaml_text = """
app:
  name: test-watch
triggers:
  - type: watch
"""
        with pytest.raises(CompilationError, match="watch trigger requires 'paths'"):
            compiler.compile_string(yaml_text)

    def test_watch_with_paths_ok(self, compiler):
        yaml_text = """
app:
  name: test-watch-ok
triggers:
  - type: watch
    paths: ["src/"]
"""
        app_def = compiler.compile_string(yaml_text)
        assert app_def.triggers[0].paths == ["src/"]

    def test_event_requires_topic(self, compiler):
        yaml_text = """
app:
  name: test-event
triggers:
  - type: event
"""
        with pytest.raises(CompilationError, match="event trigger requires 'topic'"):
            compiler.compile_string(yaml_text)

    def test_event_with_topic_ok(self, compiler):
        yaml_text = """
app:
  name: test-event-ok
triggers:
  - type: event
    topic: llmos.plans
"""
        app_def = compiler.compile_string(yaml_text)
        assert app_def.triggers[0].topic == "llmos.plans"


# ── Cron Expression Validation Tests ────────────────────────────────


class TestCronValidation:
    """Test cron expression field count validation."""

    def test_valid_5_field_cron(self, compiler):
        yaml_text = """
app:
  name: test-cron5
triggers:
  - type: schedule
    cron: "0 9 * * 1-5"
    input: "weekday check"
"""
        app_def = compiler.compile_string(yaml_text)
        assert app_def.triggers[0].cron == "0 9 * * 1-5"

    def test_valid_6_field_cron(self, compiler):
        yaml_text = """
app:
  name: test-cron6
triggers:
  - type: schedule
    cron: "0 0 9 * * 1-5"
    input: "weekday check"
"""
        app_def = compiler.compile_string(yaml_text)
        assert app_def.triggers[0].cron == "0 0 9 * * 1-5"

    def test_invalid_cron_too_few_fields(self, compiler):
        yaml_text = """
app:
  name: test-cron-bad
triggers:
  - type: schedule
    cron: "* *"
    input: "bad"
"""
        with pytest.raises(CompilationError, match="cron expression.*must have 5 or 6 fields"):
            compiler.compile_string(yaml_text)

    def test_invalid_cron_too_many_fields(self, compiler):
        yaml_text = """
app:
  name: test-cron-bad2
triggers:
  - type: schedule
    cron: "0 0 0 * * * *"
    input: "bad"
"""
        with pytest.raises(CompilationError, match="cron expression.*must have 5 or 6 fields"):
            compiler.compile_string(yaml_text)


# ── Duration String Validation Tests ────────────────────────────────


class TestDurationValidation:
    """Test that duration/timeout strings are validated."""

    def test_valid_durations(self, compiler):
        yaml_text = """
app:
  name: test-dur
flow:
  - id: step1
    action: os_exec.run_command
    params: { command: "echo hi" }
    timeout: "30s"
"""
        app_def = compiler.compile_string(yaml_text)
        assert app_def.flow[0].timeout == "30s"

    def test_various_units(self, compiler):
        for dur in ("100ms", "5s", "10m", "2h", "1d"):
            yaml_text = f"""
app:
  name: test-{dur}
flow:
  - id: step1
    action: os_exec.run_command
    params: {{}}
    timeout: "{dur}"
"""
            app_def = compiler.compile_string(yaml_text)
            assert app_def.flow[0].timeout == dur

    def test_invalid_duration_format(self, compiler):
        yaml_text = """
app:
  name: test-bad-dur
flow:
  - id: step1
    action: os_exec.run_command
    params: {}
    timeout: "thirty seconds"
"""
        with pytest.raises(CompilationError, match="Invalid duration 'thirty seconds'"):
            compiler.compile_string(yaml_text)

    def test_invalid_duration_no_unit(self, compiler):
        yaml_text = """
app:
  name: test-no-unit
flow:
  - id: step1
    action: os_exec.run_command
    params: {}
    timeout: "30"
"""
        with pytest.raises(CompilationError, match="Invalid duration '30'"):
            compiler.compile_string(yaml_text)

    def test_template_duration_skipped(self, compiler):
        yaml_text = """
app:
  name: test-tmpl-dur
flow:
  - id: step1
    action: os_exec.run_command
    params: {}
    timeout: "{{shell_timeout}}"
"""
        app_def = compiler.compile_string(yaml_text)
        assert app_def.flow[0].timeout == "{{shell_timeout}}"

    def test_trigger_debounce_validated(self, compiler):
        yaml_text = """
app:
  name: test-debounce
triggers:
  - type: watch
    paths: ["src/"]
    debounce: "not_a_duration"
"""
        with pytest.raises(CompilationError, match="Invalid duration 'not_a_duration'"):
            compiler.compile_string(yaml_text)

    def test_empty_timeout_ok(self, compiler):
        """Empty timeout string should be ignored."""
        yaml_text = """
app:
  name: test-empty-timeout
flow:
  - id: step1
    action: os_exec.run_command
    params: {}
"""
        app_def = compiler.compile_string(yaml_text)
        assert app_def.flow[0].timeout == ""


# ── on_error Validation Tests ───────────────────────────────────────


class TestOnErrorValidation:
    """Test that on_error values are valid enum values."""

    def test_valid_on_error_values(self, compiler):
        for value in ("fail", "skip", "continue", "rollback"):
            yaml_text = f"""
app:
  name: test-{value}
flow:
  - id: step1
    action: os_exec.run_command
    params: {{}}
    on_error: {value}
"""
            app_def = compiler.compile_string(yaml_text)
            assert app_def.flow[0].on_error == value

    def test_invalid_on_error(self, compiler):
        yaml_text = """
app:
  name: test-bad-err
flow:
  - id: step1
    action: os_exec.run_command
    params: {}
    on_error: explode
"""
        with pytest.raises(CompilationError, match="invalid on_error value 'explode'"):
            compiler.compile_string(yaml_text)

    def test_empty_on_error_ok(self, compiler):
        yaml_text = """
app:
  name: test-no-err
flow:
  - id: step1
    action: os_exec.run_command
    params: {}
"""
        app_def = compiler.compile_string(yaml_text)
        assert app_def.flow[0].on_error == ""

    def test_template_on_error_skipped(self, compiler):
        yaml_text = """
app:
  name: test-tmpl-err
flow:
  - id: step1
    action: os_exec.run_command
    params: {}
    on_error: "{{error_strategy}}"
"""
        app_def = compiler.compile_string(yaml_text)
        assert app_def.flow[0].on_error == "{{error_strategy}}"

    def test_nested_on_error_validated(self, compiler):
        yaml_text = """
app:
  name: test-nested-err
agent:
  id: coder
flow:
  - id: step1
    sequence:
      - id: inner
        action: os_exec.run_command
        params: {}
        on_error: bogus_value
"""
        with pytest.raises(CompilationError, match="invalid on_error value 'bogus_value'"):
            compiler.compile_string(yaml_text)
