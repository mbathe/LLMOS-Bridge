"""Tests for deep integrity validations — capabilities grant/deny action existence,
flow action existence, module_config validation, and security profile consistency."""

import logging

import pytest

from llmos_bridge.apps.compiler import AppCompiler, CompilationError


FAKE_MODULE_INFO = {
    "filesystem": {
        "actions": [
            {"name": "read_file", "description": "Read file", "params": {
                "path": {"type": "string", "required": True, "description": "File path"},
            }},
            {"name": "write_file", "description": "Write file", "params": {
                "path": {"type": "string", "required": True, "description": "File path"},
                "content": {"type": "string", "required": True, "description": "Content"},
            }},
            {"name": "list_directory", "description": "List dir", "params": {}},
        ],
    },
    "os_exec": {
        "actions": [
            {"name": "run_command", "description": "Run cmd", "params": {
                "command": {"type": "string", "required": True, "description": "Command"},
            }},
        ],
    },
    "database": {
        "actions": [
            {"name": "query", "description": "Run query", "params": {
                "sql": {"type": "string", "required": True, "description": "SQL"},
            }},
        ],
    },
    "memory": {
        "actions": [
            {"name": "store", "description": "Store value", "params": {}},
            {"name": "retrieve", "description": "Retrieve value", "params": {}},
        ],
    },
}


@pytest.fixture
def strict_compiler():
    return AppCompiler(module_info=FAKE_MODULE_INFO)


@pytest.fixture
def compiler():
    return AppCompiler()


# ── Grant Action Validation ────────────────────────────────────────


class TestGrantActionValidation:
    """Test that capabilities.grant actions are validated against module schemas."""

    def test_valid_grant_actions(self, strict_compiler):
        yaml_text = """
app:
  name: test-grant-ok
agent:
  tools:
    - module: filesystem
capabilities:
  grant:
    - module: filesystem
      actions: [read_file, write_file]
"""
        app_def = strict_compiler.compile_string(yaml_text)
        assert len(app_def.capabilities.grant) == 1

    def test_unknown_grant_action_rejected(self, strict_compiler):
        yaml_text = """
app:
  name: test-grant-bad-action
agent:
  tools:
    - module: filesystem
capabilities:
  grant:
    - module: filesystem
      actions: [read_file, nonexistent_action]
"""
        with pytest.raises(CompilationError, match="Unknown action 'nonexistent_action'.*capabilities.grant"):
            strict_compiler.compile_string(yaml_text)

    def test_grant_empty_actions_ok(self, strict_compiler):
        """Empty actions list means 'all actions' — should pass."""
        yaml_text = """
app:
  name: test-grant-all
agent:
  tools:
    - module: filesystem
capabilities:
  grant:
    - module: filesystem
"""
        app_def = strict_compiler.compile_string(yaml_text)
        assert len(app_def.capabilities.grant[0].actions) == 0

    def test_grant_module_not_in_tools_warned(self, strict_compiler, caplog):
        yaml_text = """
app:
  name: test-grant-no-tool
agent:
  tools:
    - module: filesystem
capabilities:
  grant:
    - module: memory
"""
        with caplog.at_level(logging.WARNING):
            strict_compiler.compile_string(yaml_text)
        assert any("not declared in agent tools" in r.message for r in caplog.records)

    def test_no_module_info_skips_grant_action_check(self, compiler):
        yaml_text = """
app:
  name: test-no-info
agent:
  tools:
    - module: filesystem
capabilities:
  grant:
    - module: filesystem
      actions: [totally_fake]
"""
        app_def = compiler.compile_string(yaml_text)
        assert app_def.capabilities.grant[0].actions == ["totally_fake"]


# ── Deny Action Validation ─────────────────────────────────────────


class TestDenyActionValidation:
    """Test that capabilities.deny actions are validated against module schemas."""

    def test_valid_deny_action(self, strict_compiler):
        yaml_text = """
app:
  name: test-deny-ok
agent:
  tools:
    - module: os_exec
capabilities:
  deny:
    - module: os_exec
      action: run_command
      reason: "Too dangerous"
"""
        app_def = strict_compiler.compile_string(yaml_text)
        assert len(app_def.capabilities.deny) == 1

    def test_unknown_deny_action_rejected(self, strict_compiler):
        yaml_text = """
app:
  name: test-deny-bad-action
agent:
  tools:
    - module: os_exec
capabilities:
  deny:
    - module: os_exec
      action: fake_action
      reason: "No"
"""
        with pytest.raises(CompilationError, match="Unknown action 'fake_action'.*capabilities.deny"):
            strict_compiler.compile_string(yaml_text)

    def test_deny_no_action_ok(self, strict_compiler):
        """Deny with just a module (no specific action) means deny all — ok."""
        yaml_text = """
app:
  name: test-deny-all
agent:
  tools:
    - module: os_exec
capabilities:
  deny:
    - module: os_exec
      reason: "Block everything"
"""
        app_def = strict_compiler.compile_string(yaml_text)
        assert app_def.capabilities.deny[0].action == ""


# ── Flow Action Existence Validation ───────────────────────────────


class TestFlowActionExistence:
    """Test that flow step actions reference existing modules and actions."""

    def test_valid_flow_action(self, strict_compiler):
        yaml_text = """
app:
  name: test-flow-ok
flow:
  - id: step1
    action: filesystem.read_file
    params: { path: "/tmp/test" }
"""
        app_def = strict_compiler.compile_string(yaml_text)
        assert app_def.flow[0].action == "filesystem.read_file"

    def test_unknown_module_in_flow_rejected(self, strict_compiler):
        yaml_text = """
app:
  name: test-flow-bad-module
flow:
  - id: step1
    action: nonexistent.read_file
    params: {}
"""
        with pytest.raises(CompilationError, match="unknown module 'nonexistent'"):
            strict_compiler.compile_string(yaml_text)

    def test_unknown_action_in_flow_rejected(self, strict_compiler):
        yaml_text = """
app:
  name: test-flow-bad-action
flow:
  - id: step1
    action: filesystem.nonexistent_action
    params: {}
"""
        with pytest.raises(CompilationError, match="unknown action 'nonexistent_action'.*module 'filesystem'"):
            strict_compiler.compile_string(yaml_text)

    def test_template_flow_action_skipped(self, strict_compiler):
        yaml_text = """
app:
  name: test-flow-template
flow:
  - id: step1
    action: "{{dynamic_action}}"
    params: {}
"""
        app_def = strict_compiler.compile_string(yaml_text)
        assert app_def.flow[0].action == "{{dynamic_action}}"

    def test_nested_flow_action_validated(self, strict_compiler):
        yaml_text = """
app:
  name: test-nested-flow
agent:
  id: coder
flow:
  - id: outer
    sequence:
      - id: inner
        action: filesystem.fake_action
        params: {}
"""
        with pytest.raises(CompilationError, match="unknown action 'fake_action'"):
            strict_compiler.compile_string(yaml_text)

    def test_no_module_info_skips_flow_check(self, compiler):
        yaml_text = """
app:
  name: test-no-info
flow:
  - id: step1
    action: nonexistent.fake_action
    params: {}
"""
        app_def = compiler.compile_string(yaml_text)
        assert len(app_def.flow) == 1


# ── Module Config Validation ──────────────────────────────────────


class TestModuleConfigValidation:
    """Test that module_config keys reference existing modules."""

    def test_valid_module_config(self, strict_compiler):
        yaml_text = """
app:
  name: test-config-ok
agent:
  tools:
    - module: database
module_config:
  database:
    connection_string: "sqlite:///test.db"
"""
        app_def = strict_compiler.compile_string(yaml_text)
        assert "database" in app_def.module_config

    def test_unknown_module_in_config_rejected(self, strict_compiler):
        yaml_text = """
app:
  name: test-config-bad
module_config:
  nonexistent_module:
    key: value
"""
        with pytest.raises(CompilationError, match="module_config references unknown module 'nonexistent_module'"):
            strict_compiler.compile_string(yaml_text)

    def test_no_module_info_skips_config_check(self, compiler):
        yaml_text = """
app:
  name: test-no-info
module_config:
  fake_module:
    key: value
"""
        app_def = compiler.compile_string(yaml_text)
        assert "fake_module" in app_def.module_config


# ── Security Profile Consistency ──────────────────────────────────


class TestSecurityProfileConsistency:
    """Test that security profile is consistent with declared tools."""

    def test_readonly_with_write_tools_warned(self, compiler, caplog):
        yaml_text = """
app:
  name: test-readonly-write
agent:
  tools:
    - module: os_exec
security:
  profile: readonly
"""
        with caplog.at_level(logging.WARNING):
            compiler.compile_string(yaml_text)
        assert any("readonly" in r.message and "os_exec" in r.message for r in caplog.records)

    def test_readonly_with_read_tools_ok(self, compiler, caplog):
        yaml_text = """
app:
  name: test-readonly-read
agent:
  tools:
    - module: filesystem
security:
  profile: readonly
"""
        with caplog.at_level(logging.WARNING):
            compiler.compile_string(yaml_text)
        profile_warns = [r for r in caplog.records if "readonly" in r.message and "write-capable" in r.message]
        assert len(profile_warns) == 0

    def test_power_user_with_write_tools_ok(self, compiler, caplog):
        yaml_text = """
app:
  name: test-power-write
agent:
  tools:
    - module: os_exec
security:
  profile: power_user
"""
        with caplog.at_level(logging.WARNING):
            compiler.compile_string(yaml_text)
        profile_warns = [r for r in caplog.records if "write-capable" in r.message]
        assert len(profile_warns) == 0

    def test_no_security_block_ok(self, compiler):
        """App without security: block should compile fine."""
        yaml_text = """
app:
  name: test-no-security
agent:
  tools:
    - module: os_exec
"""
        app_def = compiler.compile_string(yaml_text)
        assert app_def is not None


# ── Tool Constraints Duration/Size Validation ─────────────────────


class TestToolConstraintsValidation:
    """Test that tool constraints duration and size fields are validated."""

    def test_valid_constraint_timeout(self, compiler):
        yaml_text = """
app:
  name: test-constraint-timeout
agent:
  tools:
    - module: filesystem
      constraints:
        timeout: "30s"
"""
        app_def = compiler.compile_string(yaml_text)
        assert app_def.get_all_tools()[0].constraints.timeout == "30s"

    def test_invalid_constraint_timeout(self, compiler):
        yaml_text = """
app:
  name: test-bad-timeout
agent:
  tools:
    - module: filesystem
      constraints:
        timeout: "forever"
"""
        with pytest.raises(CompilationError, match="Invalid duration 'forever'"):
            compiler.compile_string(yaml_text)

    def test_valid_max_file_size(self, compiler):
        yaml_text = """
app:
  name: test-size
agent:
  tools:
    - module: filesystem
      constraints:
        max_file_size: "50MB"
"""
        app_def = compiler.compile_string(yaml_text)
        assert app_def.get_all_tools()[0].constraints.max_file_size == "50MB"

    def test_invalid_max_file_size(self, compiler):
        yaml_text = """
app:
  name: test-bad-size
agent:
  tools:
    - module: filesystem
      constraints:
        max_file_size: "huge"
"""
        with pytest.raises(CompilationError, match="Invalid size 'huge'"):
            compiler.compile_string(yaml_text)

    def test_valid_max_response_size(self, compiler):
        yaml_text = """
app:
  name: test-resp-size
agent:
  tools:
    - module: filesystem
      constraints:
        max_response_size: "1GB"
"""
        app_def = compiler.compile_string(yaml_text)
        assert app_def.get_all_tools()[0].constraints.max_response_size == "1GB"

    def test_invalid_max_response_size(self, compiler):
        yaml_text = """
app:
  name: test-bad-resp
agent:
  tools:
    - module: filesystem
      constraints:
        max_response_size: "lots"
"""
        with pytest.raises(CompilationError, match="Invalid size 'lots'"):
            compiler.compile_string(yaml_text)

    def test_grant_constraint_timeout_validated(self, compiler):
        yaml_text = """
app:
  name: test-grant-timeout
agent:
  tools:
    - module: filesystem
capabilities:
  grant:
    - module: filesystem
      constraints:
        timeout: "not_a_duration"
"""
        with pytest.raises(CompilationError, match="Invalid duration 'not_a_duration'"):
            compiler.compile_string(yaml_text)

    def test_template_constraint_skipped(self, compiler):
        yaml_text = """
app:
  name: test-tmpl-constraint
agent:
  tools:
    - module: filesystem
      constraints:
        timeout: "{{default_timeout}}"
"""
        app_def = compiler.compile_string(yaml_text)
        assert app_def.get_all_tools()[0].constraints.timeout == "{{default_timeout}}"


# ── Duplicate Tool Module Detection ────────────────────────────────


class TestDuplicateToolModules:
    """Test that duplicate tool module declarations are warned."""

    def test_duplicate_module_warned(self, compiler, caplog):
        yaml_text = """
app:
  name: test-dup
agent:
  tools:
    - module: filesystem
    - module: filesystem
"""
        with caplog.at_level(logging.WARNING):
            compiler.compile_string(yaml_text)
        dup_warns = [r for r in caplog.records if "declared 2 times" in r.message]
        assert len(dup_warns) == 1
        assert "filesystem" in dup_warns[0].message

    def test_no_duplicate_ok(self, compiler, caplog):
        yaml_text = """
app:
  name: test-no-dup
agent:
  tools:
    - module: filesystem
    - module: os_exec
"""
        with caplog.at_level(logging.WARNING):
            compiler.compile_string(yaml_text)
        dup_warns = [r for r in caplog.records if "declared" in r.message and "times" in r.message]
        assert len(dup_warns) == 0


# ── Multi-Agent Tools Validation ───────────────────────────────────


class TestMultiAgentToolsValidation:
    """Test that multi-agent tools are included in validation."""

    def test_multi_agent_tools_collected(self, strict_compiler):
        yaml_text = """
app:
  name: test-multi-tools
agents:
  - id: reader
    role: specialist
    tools:
      - module: filesystem
  - id: executor
    role: specialist
    tools:
      - module: os_exec
flow:
  - id: step1
    agent: reader
    input: "Read"
"""
        app_def = strict_compiler.compile_string(yaml_text)
        all_modules = {t.module for t in app_def.get_all_tools()}
        assert "filesystem" in all_modules
        assert "os_exec" in all_modules

    def test_multi_agent_unknown_module_rejected(self, strict_compiler):
        yaml_text = """
app:
  name: test-multi-bad
agents:
  - id: worker
    role: specialist
    tools:
      - module: nonexistent_module
flow:
  - id: step1
    agent: worker
    input: "Work"
"""
        with pytest.raises(CompilationError, match="Unknown module 'nonexistent_module'"):
            strict_compiler.compile_string(yaml_text)


# ── Perception Actions Validation ──────────────────────────────────


class TestPerceptionActionsValidation:
    """Test that perception.actions keys are validated."""

    def test_valid_perception_action_key(self, compiler):
        yaml_text = """
app:
  name: test-percept-ok
perception:
  enabled: true
  actions:
    filesystem.read_file:
      capture_before: true
"""
        app_def = compiler.compile_string(yaml_text)
        assert "filesystem.read_file" in app_def.perception.actions

    def test_invalid_perception_key_format(self, compiler):
        yaml_text = """
app:
  name: test-percept-bad
perception:
  enabled: true
  actions:
    bad_format:
      capture_before: true
"""
        with pytest.raises(CompilationError, match="perception.actions key 'bad_format'.*module.action"):
            compiler.compile_string(yaml_text)

    def test_perception_unknown_module_rejected(self, strict_compiler):
        yaml_text = """
app:
  name: test-percept-mod
perception:
  enabled: true
  actions:
    nonexistent.some_action:
      capture_before: true
"""
        with pytest.raises(CompilationError, match="perception.actions.*unknown module 'nonexistent'"):
            strict_compiler.compile_string(yaml_text)

    def test_perception_unknown_action_rejected(self, strict_compiler):
        yaml_text = """
app:
  name: test-percept-act
perception:
  enabled: true
  actions:
    filesystem.fake_action:
      capture_before: true
"""
        with pytest.raises(CompilationError, match="perception.actions.*unknown action 'fake_action'"):
            strict_compiler.compile_string(yaml_text)
