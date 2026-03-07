"""Tests for enhanced compiler validations — module existence, agent refs,
expression syntax, and security profile enum."""

import logging

import pytest

from llmos_bridge.apps.compiler import AppCompiler, CompilationError


# ── Fixtures ──────────────────────────────────────────────────────────

FAKE_MODULE_INFO = {
    "filesystem": {
        "actions": [
            {"name": "read_file", "description": "Read file", "params": {}},
            {"name": "write_file", "description": "Write file", "params": {}},
            {"name": "list_directory", "description": "List dir", "params": {}},
            {"name": "delete_file", "description": "Delete file", "params": {}},
        ],
    },
    "os_exec": {
        "actions": [
            {"name": "run_command", "description": "Run cmd", "params": {}},
            {"name": "get_env", "description": "Get env", "params": {}},
        ],
    },
    "memory": {
        "actions": [
            {"name": "store", "description": "Store", "params": {}},
            {"name": "recall", "description": "Recall", "params": {}},
        ],
    },
}


@pytest.fixture
def compiler():
    """Compiler without module info — backward compatible."""
    return AppCompiler()


@pytest.fixture
def strict_compiler():
    """Compiler with module info — validates modules/actions."""
    return AppCompiler(module_info=FAKE_MODULE_INFO)


# ── Security Profile Enum Tests ──────────────────────────────────────


class TestSecurityProfileValidation:
    """Test that security.profile is validated as an enum."""

    def test_valid_profiles(self, compiler):
        for profile in ("readonly", "local_worker", "power_user", "unrestricted"):
            yaml_text = f"""
app:
  name: test-{profile}
security:
  profile: {profile}
"""
            app_def = compiler.compile_string(yaml_text)
            assert app_def.security.profile.value == profile

    def test_invalid_profile_rejected(self, compiler):
        yaml_text = """
app:
  name: test-bad
security:
  profile: nonexistent_profile
"""
        with pytest.raises(CompilationError, match="validation failed"):
            compiler.compile_string(yaml_text)

    def test_default_is_power_user(self, compiler):
        yaml_text = """
app:
  name: test-default
security: {}
"""
        app_def = compiler.compile_string(yaml_text)
        assert app_def.security.profile.value == "power_user"


# ── Module/Action Existence Tests ────────────────────────────────────


class TestModuleValidation:
    """Test that module/action references are validated when module_info is provided."""

    def test_valid_module_and_action(self, strict_compiler):
        yaml_text = """
app:
  name: test-valid
agent:
  tools:
    - module: filesystem
      action: read_file
    - module: os_exec
      action: run_command
"""
        app_def = strict_compiler.compile_string(yaml_text)
        assert app_def.app.name == "test-valid"

    def test_unknown_module_rejected(self, strict_compiler):
        yaml_text = """
app:
  name: test-bad-module
agent:
  tools:
    - module: nonexistent_module
      action: some_action
"""
        with pytest.raises(CompilationError, match="Unknown module 'nonexistent_module'"):
            strict_compiler.compile_string(yaml_text)

    def test_unknown_action_rejected(self, strict_compiler):
        yaml_text = """
app:
  name: test-bad-action
agent:
  tools:
    - module: filesystem
      action: nonexistent_action
"""
        with pytest.raises(CompilationError, match="Unknown action 'nonexistent_action'.*filesystem"):
            strict_compiler.compile_string(yaml_text)

    def test_unknown_action_in_actions_list(self, strict_compiler):
        yaml_text = """
app:
  name: test-bad-actions-list
agent:
  tools:
    - module: filesystem
      actions: [read_file, bogus_action]
"""
        with pytest.raises(CompilationError, match="Unknown action 'bogus_action'"):
            strict_compiler.compile_string(yaml_text)

    def test_unknown_action_in_exclude(self, strict_compiler):
        yaml_text = """
app:
  name: test-bad-exclude
agent:
  tools:
    - module: os_exec
      exclude: [nonexistent_thing]
"""
        with pytest.raises(CompilationError, match="Excluded action 'nonexistent_thing' does not exist"):
            strict_compiler.compile_string(yaml_text)

    def test_whole_module_passes(self, strict_compiler):
        """Including a whole module (no action/actions) should pass."""
        yaml_text = """
app:
  name: test-whole-module
agent:
  tools:
    - module: filesystem
"""
        app_def = strict_compiler.compile_string(yaml_text)
        assert app_def.app.name == "test-whole-module"

    def test_builtin_tools_ignored(self, strict_compiler):
        """Builtin tools like ask_user should not be checked against modules."""
        yaml_text = """
app:
  name: test-builtins
agent:
  tools:
    - builtin: ask_user
    - module: filesystem
      action: read_file
"""
        app_def = strict_compiler.compile_string(yaml_text)
        assert len(app_def.agent.tools) == 2

    def test_capabilities_grant_module_validated(self, strict_compiler):
        yaml_text = """
app:
  name: test-cap-grant
agent:
  tools:
    - module: filesystem
capabilities:
  grant:
    - module: nonexistent_module
"""
        with pytest.raises(CompilationError, match="Unknown module 'nonexistent_module'.*capabilities.grant"):
            strict_compiler.compile_string(yaml_text)

    def test_capabilities_deny_module_validated(self, strict_compiler):
        yaml_text = """
app:
  name: test-cap-deny
agent:
  tools:
    - module: filesystem
capabilities:
  deny:
    - module: nonexistent_module
      action: something
"""
        with pytest.raises(CompilationError, match="Unknown module 'nonexistent_module'.*capabilities.deny"):
            strict_compiler.compile_string(yaml_text)

    def test_no_module_info_skips_validation(self, compiler):
        """Without module_info, module references are NOT validated (backward compat)."""
        yaml_text = """
app:
  name: test-no-check
agent:
  tools:
    - module: totally_fake_module
      action: imaginary_action
"""
        app_def = compiler.compile_string(yaml_text)
        assert app_def.app.name == "test-no-check"


# ── Agent ID Cross-Reference Tests ───────────────────────────────────


class TestAgentRefValidation:
    """Test that flow steps reference valid agent IDs."""

    def test_valid_agent_ref_single(self, compiler):
        yaml_text = """
app:
  name: test-valid-ref
agent:
  id: coder
  tools:
    - module: filesystem
flow:
  - id: step1
    agent: coder
    input: "Hello"
"""
        app_def = compiler.compile_string(yaml_text)
        assert app_def.flow[0].agent == "coder"

    def test_default_agent_ref(self, compiler):
        yaml_text = """
app:
  name: test-default-ref
agent:
  tools:
    - module: filesystem
flow:
  - id: step1
    agent: default
    input: "Hello"
"""
        # "default" should always be valid
        app_def = compiler.compile_string(yaml_text)
        assert app_def.flow[0].agent == "default"

    def test_unknown_agent_rejected(self, compiler):
        yaml_text = """
app:
  name: test-bad-ref
agent:
  id: coder
  tools:
    - module: filesystem
flow:
  - id: step1
    agent: nonexistent_agent
    input: "Hello"
"""
        with pytest.raises(CompilationError, match="unknown agent 'nonexistent_agent'"):
            compiler.compile_string(yaml_text)

    def test_multi_agent_valid_refs(self, compiler):
        yaml_text = """
app:
  name: test-multi-ref
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
    input: "Work"
"""
        app_def = compiler.compile_string(yaml_text)
        assert len(app_def.flow) == 2

    def test_multi_agent_unknown_ref(self, compiler):
        yaml_text = """
app:
  name: test-multi-bad
agents:
  - id: planner
    role: coordinator
  - id: worker
    role: specialist
flow:
  - id: step1
    agent: nonexistent
    input: "Boom"
"""
        with pytest.raises(CompilationError, match="unknown agent 'nonexistent'"):
            compiler.compile_string(yaml_text)

    def test_template_agent_ref_skipped(self, compiler):
        """Dynamic agent refs ({{...}}) should NOT be validated."""
        yaml_text = """
app:
  name: test-dynamic-ref
agent:
  id: coder
  tools:
    - module: filesystem
flow:
  - id: step1
    agent: "{{result.select_agent}}"
    input: "Dynamic"
"""
        app_def = compiler.compile_string(yaml_text)
        assert app_def.flow[0].agent == "{{result.select_agent}}"

    def test_nested_flow_agent_refs(self, compiler):
        """Agent refs inside branches, loops, etc. should be validated."""
        yaml_text = """
app:
  name: test-nested
agent:
  id: coder
  tools:
    - module: filesystem
flow:
  - id: step1
    branch:
      "on": "{{result.check}}"
      cases:
        "ok":
          - id: ok_step
            agent: coder
            input: "OK"
      default:
        - id: fail_step
          agent: nonexistent
          input: "Fail"
"""
        with pytest.raises(CompilationError, match="unknown agent 'nonexistent'"):
            compiler.compile_string(yaml_text)

    def test_macro_body_agent_refs(self, compiler):
        """Agent refs inside macro bodies should be validated."""
        yaml_text = """
app:
  name: test-macro-agent
agent:
  id: coder
  tools:
    - module: filesystem
macros:
  - name: my_macro
    body:
      - id: macro_step
        agent: ghost_agent
        input: "Hello"
"""
        with pytest.raises(CompilationError, match="unknown agent 'ghost_agent'"):
            compiler.compile_string(yaml_text)


# ── Expression Syntax Validation Tests ───────────────────────────────


class TestExpressionValidation:
    """Test that expression syntax issues are detected at compile time."""

    def test_unknown_filter_warned(self, compiler, caplog):
        yaml_text = """
app:
  name: test-bad-filter
agent:
  system_prompt: "{{name | nonexistent_filter}}"
"""
        with caplog.at_level(logging.WARNING):
            compiler.compile_string(yaml_text)
        assert "Unknown filter '|nonexistent_filter'" in caplog.text

    def test_known_filter_no_warning(self, compiler, caplog):
        yaml_text = """
app:
  name: test-good-filter
agent:
  system_prompt: "{{name | upper}}"
"""
        with caplog.at_level(logging.WARNING):
            compiler.compile_string(yaml_text)
        assert "Unknown filter" not in caplog.text

    def test_chained_filters_validated(self, compiler, caplog):
        yaml_text = """
app:
  name: test-chained
agent:
  system_prompt: "{{params.command | join(' ') | startswith('git')}}"
"""
        with caplog.at_level(logging.WARNING):
            compiler.compile_string(yaml_text)
        assert "Unknown filter" not in caplog.text

    def test_unmatched_brackets_warned(self, compiler, caplog):
        yaml_text = """
app:
  name: test-brackets
agent:
  system_prompt: "{{name}"
"""
        with caplog.at_level(logging.WARNING):
            compiler.compile_string(yaml_text)
        assert "Unmatched brackets" in caplog.text

    def test_logical_operators_not_flagged(self, compiler, caplog):
        """'and', 'or', 'not' should not be flagged as unknown filters."""
        yaml_text = """
app:
  name: test-logic
capabilities:
  deny:
    - module: filesystem
      action: delete_file
      when: "{{params.path | endswith('.env') or params.path | endswith('.key')}}"
"""
        with caplog.at_level(logging.WARNING):
            compiler.compile_string(yaml_text)
        assert "Unknown filter" not in caplog.text

    def test_multiple_filters_in_when(self, compiler, caplog):
        """Complex when: expression from claude-code should produce no warnings."""
        yaml_text = """
app:
  name: test-complex-when
capabilities:
  approval_required:
    - module: os_exec
      action: run_command
      when: "{{params.command | join(' ') | startswith('git push') or params.command | join(' ') | startswith('git reset')}}"
"""
        with caplog.at_level(logging.WARNING):
            compiler.compile_string(yaml_text)
        assert "Unknown filter" not in caplog.text


# ── Integration: Compile claude-code.app.yaml ────────────────────────


class TestClaudeCodeCompilation:
    """Ensure the reference claude-code.app.yaml compiles cleanly."""

    def test_claude_code_compiles(self, compiler):
        """claude-code.app.yaml should compile without errors."""
        from pathlib import Path
        app_path = Path(__file__).parents[5] / "examples" / "claude-code.app.yaml"
        if not app_path.exists():
            pytest.skip("claude-code.app.yaml not found")
        app_def = compiler.compile_file(app_path)
        assert app_def.app.name == "claude-code"
        assert app_def.security.profile.value == "power_user"

    def test_claude_code_no_expression_warnings(self, compiler, caplog):
        """claude-code.app.yaml should produce no expression warnings."""
        from pathlib import Path
        app_path = Path(__file__).parents[5] / "examples" / "claude-code.app.yaml"
        if not app_path.exists():
            pytest.skip("claude-code.app.yaml not found")
        with caplog.at_level(logging.WARNING):
            compiler.compile_file(app_path)
        # Should have no unknown filter warnings
        unknown_filters = [r for r in caplog.records if "Unknown filter" in r.message]
        assert len(unknown_filters) == 0, f"Unexpected filter warnings: {[r.message for r in unknown_filters]}"


# ══════════════════════════════════════════════════════════════════════
# Macro Body Action Validation
# ══════════════════════════════════════════════════════════════════════


class TestMacroBodyValidation:
    """Macro body steps must reference valid modules/actions when module_info is provided."""

    def test_macro_body_invalid_action_rejected(self, strict_compiler):
        """Invalid action name in macro body must be caught at compile time."""
        yaml_text = """
app:
  name: test-macro-bad-action
agent:
  tools:
    - module: filesystem
    - module: memory
macros:
  - name: my_macro
    body:
      - action: filesystem.read_file_FAKE
        params:
          path: /tmp/test
"""
        with pytest.raises(CompilationError, match="unknown action 'read_file_FAKE'.*filesystem"):
            strict_compiler.compile_string(yaml_text)

    def test_macro_body_invalid_module_rejected(self, strict_compiler):
        """Invalid module in macro body must be caught at compile time."""
        yaml_text = """
app:
  name: test-macro-bad-module
agent:
  tools:
    - module: filesystem
macros:
  - name: broken
    body:
      - action: nonexistent_module.do_thing
"""
        with pytest.raises(CompilationError, match="unknown module 'nonexistent_module'"):
            strict_compiler.compile_string(yaml_text)

    def test_macro_body_valid_actions_pass(self, strict_compiler):
        """Valid actions in macro body should compile without error."""
        yaml_text = """
app:
  name: test-macro-ok
agent:
  tools:
    - module: filesystem
    - module: memory
macros:
  - name: good_macro
    body:
      - action: filesystem.read_file
      - action: memory.store
"""
        app_def = strict_compiler.compile_string(yaml_text)
        assert len(app_def.macros) == 1

    def test_macro_body_nested_steps_validated(self, strict_compiler):
        """Actions inside nested flow constructs in macros are validated."""
        yaml_text = """
app:
  name: test-macro-nested
agent:
  tools:
    - module: filesystem
    - module: os_exec
macros:
  - name: nested_macro
    body:
      - action: filesystem.read_file
      - sequence:
          - action: os_exec.run_command_FAKE
"""
        with pytest.raises(CompilationError, match="run_command_FAKE"):
            strict_compiler.compile_string(yaml_text)

    def test_no_module_info_skips_macro_body_check(self, compiler):
        """Without module_info, macro body actions are not validated."""
        yaml_text = """
app:
  name: test-macro-no-info
agent:
  tools:
    - module: filesystem
macros:
  - name: anything
    body:
      - action: nonexistent.fake_action
"""
        app_def = compiler.compile_string(yaml_text)
        assert len(app_def.macros) == 1


# ══════════════════════════════════════════════════════════════════════
# Macro Call Site Param Validation
# ══════════════════════════════════════════════════════════════════════


class TestMacroCallSiteValidation:
    """Macro invocations (use:) must provide required params and not pass unknowns."""

    def test_missing_required_param_rejected(self, compiler):
        yaml_text = """
app:
  name: test-macro-missing-param
agent:
  tools:
    - module: filesystem
macros:
  - name: check_file
    params:
      path: {type: string, required: true}
      format: {type: string, required: true}
    body:
      - action: filesystem.read_file
        params:
          path: "{{macro.path}}"
flow:
  - use: check_file
    with:
      path: /tmp/test
"""
        with pytest.raises(CompilationError, match="missing required param 'format'"):
            compiler.compile_string(yaml_text)

    def test_unknown_param_rejected(self, compiler):
        yaml_text = """
app:
  name: test-macro-unknown-param
agent:
  tools:
    - module: filesystem
macros:
  - name: check_file
    params:
      path: {type: string, required: true}
    body:
      - action: filesystem.read_file
        params:
          path: "{{macro.path}}"
flow:
  - use: check_file
    with:
      path: /tmp/test
      bogus_param: hello
"""
        with pytest.raises(CompilationError, match="unknown param 'bogus_param'"):
            compiler.compile_string(yaml_text)

    def test_all_params_provided_passes(self, compiler):
        yaml_text = """
app:
  name: test-macro-all-params
agent:
  tools:
    - module: filesystem
macros:
  - name: check_file
    params:
      path: {type: string, required: true}
      format: {type: string, required: false, default: json}
    body:
      - action: filesystem.read_file
        params:
          path: "{{macro.path}}"
flow:
  - use: check_file
    with:
      path: /tmp/test
"""
        app_def = compiler.compile_string(yaml_text)
        assert len(app_def.macros) == 1

    def test_optional_param_with_default_not_required(self, compiler):
        """Params with default values should not trigger 'missing required' errors."""
        yaml_text = """
app:
  name: test-macro-optional
agent:
  tools:
    - module: filesystem
macros:
  - name: my_macro
    params:
      required_arg: {type: string, required: true}
      optional_arg: {type: string, required: true, default: fallback}
    body:
      - action: filesystem.read_file
        params:
          path: "{{macro.required_arg}}"
flow:
  - use: my_macro
    with:
      required_arg: value
"""
        app_def = compiler.compile_string(yaml_text)
        assert len(app_def.flow) == 1
