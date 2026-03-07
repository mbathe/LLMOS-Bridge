"""Tests for Phase E — macro execution, spawn wiring, and runtime integration."""

import asyncio
from pathlib import Path
import pytest

from llmos_bridge.apps.expression import ExpressionContext, ExpressionEngine
from llmos_bridge.apps.flow_executor import (
    FlowExecutor,
    FlowResult,
    StepResult,
)
from llmos_bridge.apps.models import (
    AppConfig,
    AppDefinition,
    AgentConfig,
    BrainConfig,
    FlowStep,
    MacroDefinition,
    MacroParam,
    SpawnConfig,
)
from llmos_bridge.apps.runtime import AppRuntime


# ─── Helpers ──────────────────────────────────────────────────────────


async def mock_action(module_id, action_name, params):
    return {"module": module_id, "action": action_name, "params": params}


async def mock_agent(agent_id, input_text):
    return {"output": f"Agent {agent_id}: {input_text}"}


def make_executor(
    macros: list[MacroDefinition] | None = None,
    spawn_app=None,
    ctx: ExpressionContext | None = None,
) -> FlowExecutor:
    return FlowExecutor(
        expr_context=ctx or ExpressionContext(variables={"workspace": "/test"}),
        execute_action=mock_action,
        run_agent=mock_agent,
        macros=macros,
        spawn_app=spawn_app,
    )


# ─── Macro Tests ─────────────────────────────────────────────────────


class TestMacroExecution:
    @pytest.mark.asyncio
    async def test_basic_macro(self):
        """A simple macro with one action step executes correctly."""
        macro = MacroDefinition(
            name="greet",
            params={"name": MacroParam(type="string", required=True)},
            body=[
                FlowStep(
                    id="say_hi",
                    action="os_exec.run_command",
                    params={"command": "echo Hello {{macro.name}}"},
                ),
            ],
        )
        executor = make_executor(macros=[macro])
        steps = [FlowStep(id="m1", use="greet", with_params={"name": "World"})]
        result = await executor.execute(steps)

        assert result.success
        assert "m1" in result.results
        # The macro body executed the action
        child = result.results["m1"]
        assert child.output["module"] == "os_exec"

    @pytest.mark.asyncio
    async def test_macro_not_found(self):
        """Using a nonexistent macro fails gracefully."""
        executor = make_executor(macros=[])
        steps = [FlowStep(id="m1", use="nonexistent")]
        result = await executor.execute(steps)

        assert not result.results["m1"].success
        assert "not found" in result.results["m1"].error

    @pytest.mark.asyncio
    async def test_macro_no_name(self):
        """A use_macro step with empty name fails."""
        executor = make_executor()
        steps = [FlowStep(id="m1", use="")]
        # use="" won't trigger use_macro type — it'll be action fallback
        # That's by design in infer_type()
        result = await executor.execute(steps)
        # Empty action string → invalid action at step level
        assert not result.results["m1"].success

    @pytest.mark.asyncio
    async def test_macro_default_params(self):
        """Macro params with defaults work when not provided."""
        macro = MacroDefinition(
            name="deploy",
            params={
                "target": MacroParam(type="string", required=True),
                "env": MacroParam(type="string", required=False, default="staging"),
            },
            body=[
                FlowStep(
                    id="deploy_step",
                    action="os_exec.run_command",
                    params={"command": "deploy {{macro.target}} {{macro.env}}"},
                ),
            ],
        )
        executor = make_executor(macros=[macro])
        steps = [FlowStep(id="m1", use="deploy", with_params={"target": "app-v2"})]
        result = await executor.execute(steps)

        assert result.success

    @pytest.mark.asyncio
    async def test_macro_missing_required_param(self):
        """Missing a required macro param produces an error."""
        macro = MacroDefinition(
            name="deploy",
            params={
                "target": MacroParam(type="string", required=True),
            },
            body=[
                FlowStep(id="s1", action="os_exec.run_command", params={"command": "deploy"}),
            ],
        )
        executor = make_executor(macros=[macro])
        steps = [FlowStep(id="m1", use="deploy", with_params={})]
        result = await executor.execute(steps)

        assert not result.results["m1"].success
        assert "missing required param" in result.results["m1"].error.lower()

    @pytest.mark.asyncio
    async def test_macro_multi_step_body(self):
        """A macro with multiple body steps executes all sequentially."""
        macro = MacroDefinition(
            name="build_test",
            body=[
                FlowStep(id="build", action="os_exec.run_command", params={"command": "build"}),
                FlowStep(id="test", action="os_exec.run_command", params={"command": "test"}),
            ],
        )
        executor = make_executor(macros=[macro])
        steps = [FlowStep(id="m1", use="build_test")]
        result = await executor.execute(steps)

        assert result.success
        assert result.results["m1"].children is not None
        assert len(result.results["m1"].children) == 2

    @pytest.mark.asyncio
    async def test_macro_body_failure_stops_execution(self):
        """If a macro body step fails, remaining steps are skipped."""
        async def failing_action(module_id, action_name, params):
            raise RuntimeError("boom")

        macro = MacroDefinition(
            name="fail_macro",
            body=[
                FlowStep(id="step1", action="os_exec.run_command", params={"command": "fail"}),
                FlowStep(id="step2", action="os_exec.run_command", params={"command": "skip"}),
            ],
        )
        executor = FlowExecutor(
            execute_action=failing_action,
            macros=[macro],
        )
        steps = [FlowStep(id="m1", use="fail_macro")]
        result = await executor.execute(steps)

        assert not result.results["m1"].success
        # Only first step should have run
        assert len(result.results["m1"].children) == 1

    @pytest.mark.asyncio
    async def test_macro_context_isolation(self):
        """Macro params don't leak into the outer context after execution."""
        macro = MacroDefinition(
            name="isolated",
            params={"secret": MacroParam(type="string", required=True)},
            body=[
                FlowStep(id="s1", action="os_exec.run_command", params={"command": "echo {{macro.secret}}"}),
            ],
        )
        ctx = ExpressionContext(variables={"workspace": "/test"})
        executor = make_executor(macros=[macro], ctx=ctx)
        steps = [FlowStep(id="m1", use="isolated", with_params={"secret": "password123"})]
        await executor.execute(steps)

        # After execution, "macro" should not be in context
        assert "macro" not in ctx.variables

    @pytest.mark.asyncio
    async def test_macro_with_agent_step(self):
        """A macro body can contain agent steps."""
        macro = MacroDefinition(
            name="ask_agent",
            params={"question": MacroParam(type="string", required=True)},
            body=[
                FlowStep(id="ask", agent="coder", input="{{macro.question}}"),
            ],
        )
        executor = make_executor(macros=[macro])
        steps = [FlowStep(id="m1", use="ask_agent", with_params={"question": "What is 2+2?"})]
        result = await executor.execute(steps)

        assert result.success

    @pytest.mark.asyncio
    async def test_nested_macro_calls(self):
        """One macro can call another macro in its body."""
        inner = MacroDefinition(
            name="inner",
            body=[
                FlowStep(id="inner_s1", action="filesystem.read_file", params={"path": "/tmp/test"}),
            ],
        )
        outer = MacroDefinition(
            name="outer",
            body=[
                FlowStep(id="call_inner", use="inner"),
                FlowStep(id="outer_s1", action="os_exec.run_command", params={"command": "echo done"}),
            ],
        )
        executor = make_executor(macros=[inner, outer])
        steps = [FlowStep(id="m1", use="outer")]
        result = await executor.execute(steps)

        assert result.success
        assert result.results["m1"].children is not None
        assert len(result.results["m1"].children) == 2

    @pytest.mark.asyncio
    async def test_macro_params_with_dict_format(self):
        """Macro params defined as plain dicts (not MacroParam objects) work."""
        macro = MacroDefinition(
            name="dict_params",
            params={
                "x": {"type": "string", "required": True},
                "y": {"type": "string", "required": False, "default": "42"},
            },
            body=[
                FlowStep(id="s1", action="os_exec.run_command", params={"command": "echo {{macro.x}} {{macro.y}}"}),
            ],
        )
        executor = make_executor(macros=[macro])
        steps = [FlowStep(id="m1", use="dict_params", with_params={"x": "hello"})]
        result = await executor.execute(steps)

        assert result.success

    @pytest.mark.asyncio
    async def test_macro_dict_missing_required(self):
        """Dict-format params also enforce required."""
        macro = MacroDefinition(
            name="strict",
            params={"x": {"type": "string", "required": True}},
            body=[
                FlowStep(id="s1", action="os_exec.run_command", params={"command": "noop"}),
            ],
        )
        executor = make_executor(macros=[macro])
        steps = [FlowStep(id="m1", use="strict", with_params={})]
        result = await executor.execute(steps)

        assert not result.results["m1"].success
        assert "missing required param" in result.results["m1"].error.lower()

    @pytest.mark.asyncio
    async def test_macro_expression_resolution_in_params(self):
        """Macro with_params values are resolved through expression engine."""
        macro = MacroDefinition(
            name="echo_ws",
            params={"dir": MacroParam(type="string", required=True)},
            body=[
                FlowStep(id="s1", action="os_exec.run_command", params={"command": "ls {{macro.dir}}"}),
            ],
        )
        ctx = ExpressionContext(variables={"workspace": "/my/project"})
        executor = make_executor(macros=[macro], ctx=ctx)
        steps = [FlowStep(id="m1", use="echo_ws", with_params={"dir": "{{workspace}}"})]
        result = await executor.execute(steps)

        assert result.success


# ─── Spawn Tests ─────────────────────────────────────────────────────


class TestSpawnExecution:
    @pytest.mark.asyncio
    async def test_spawn_basic(self):
        """Spawn calls the spawn_app callback with correct args."""
        called_with = {}

        async def mock_spawn(app_path, input_text, timeout):
            called_with["path"] = app_path
            called_with["input"] = input_text
            called_with["timeout"] = timeout
            return {"output": "spawned result", "success": True}

        executor = make_executor(spawn_app=mock_spawn)
        steps = [
            FlowStep(
                id="s1",
                spawn=SpawnConfig(app="sub-app.app.yaml", input="Do something", timeout="10s"),
            ),
        ]
        result = await executor.execute(steps)

        assert result.success
        assert called_with["path"] == "sub-app.app.yaml"
        assert called_with["input"] == "Do something"
        assert called_with["timeout"] == 10.0

    @pytest.mark.asyncio
    async def test_spawn_no_handler(self):
        """Spawn without a handler configured fails gracefully."""
        executor = make_executor(spawn_app=None)
        steps = [
            FlowStep(id="s1", spawn=SpawnConfig(app="sub.yaml", input="test")),
        ]
        result = await executor.execute(steps)

        assert not result.results["s1"].success
        assert "no spawn handler" in result.results["s1"].error.lower()

    @pytest.mark.asyncio
    async def test_spawn_result_accessible(self):
        """Spawn result is accessible via expression context."""
        async def mock_spawn(app_path, input_text, timeout):
            return {"output": "42", "success": True}

        executor = make_executor(spawn_app=mock_spawn)
        steps = [
            FlowStep(id="sub", spawn=SpawnConfig(app="calc.yaml", input="compute")),
            FlowStep(
                id="use_result",
                action="os_exec.run_command",
                params={"command": "echo {{result.sub.output}}"},
            ),
        ]
        result = await executor.execute(steps)

        assert result.success
        assert result.results["use_result"].output["params"]["command"] == "echo 42"

    @pytest.mark.asyncio
    async def test_spawn_expression_in_input(self):
        """Spawn input field supports expression resolution."""
        async def mock_spawn(app_path, input_text, timeout):
            return {"input_received": input_text}

        ctx = ExpressionContext(variables={"task": "fix the bug"})
        executor = make_executor(spawn_app=mock_spawn, ctx=ctx)
        steps = [
            FlowStep(id="s1", spawn=SpawnConfig(app="helper.yaml", input="{{task}}")),
        ]
        result = await executor.execute(steps)

        assert result.success
        assert result.results["s1"].output["input_received"] == "fix the bug"


# ─── Runtime Integration ─────────────────────────────────────────────


class TestRuntimeMacroIntegration:
    @pytest.mark.asyncio
    async def test_runtime_passes_macros_to_flow_executor(self):
        """AppRuntime.run_flow() passes app macros to FlowExecutor."""
        action_calls = []

        async def track_action(module_id, action_name, params):
            action_calls.append((module_id, action_name))
            return {"result": "ok"}

        runtime = AppRuntime(execute_tool=track_action)
        app_def = AppDefinition(
            app=AppConfig(name="macro-test", version="1.0"),
            macros=[
                MacroDefinition(
                    name="build",
                    body=[
                        FlowStep(id="b1", action="os_exec.run_command", params={"command": "make"}),
                    ],
                ),
            ],
            flow=[
                FlowStep(id="run_build", use="build"),
            ],
        )

        result = await runtime.run_flow(app_def, "test input")

        assert result.success
        assert ("os_exec", "run_command") in action_calls

    @pytest.mark.asyncio
    async def test_runtime_run_delegates_to_flow_when_flow_defined(self):
        """AppRuntime.run() uses flow execution when app has a flow."""
        action_calls = []

        async def track_action(module_id, action_name, params):
            action_calls.append((module_id, action_name))
            return {"result": "ok"}

        runtime = AppRuntime(execute_tool=track_action)
        app_def = AppDefinition(
            app=AppConfig(name="flow-test", version="1.0"),
            flow=[
                FlowStep(id="s1", action="filesystem.read_file", params={"path": "/tmp/x"}),
            ],
        )

        result = await runtime.run(app_def, "test")

        assert result.success
        assert ("filesystem", "read_file") in action_calls

    @pytest.mark.asyncio
    async def test_runtime_macro_in_flow_with_params(self):
        """Full integration: macro with params used in flow via runtime."""
        results = []

        async def capture_action(module_id, action_name, params):
            results.append(params)
            return {"done": True}

        runtime = AppRuntime(execute_tool=capture_action)
        app_def = AppDefinition(
            app=AppConfig(name="param-macro-test", version="1.0"),
            macros=[
                MacroDefinition(
                    name="deploy",
                    params={"target": MacroParam(type="string", required=True)},
                    body=[
                        FlowStep(
                            id="d1",
                            action="os_exec.run_command",
                            params={"command": "deploy to {{macro.target}}"},
                        ),
                    ],
                ),
            ],
            flow=[
                FlowStep(id="m1", use="deploy", with_params={"target": "production"}),
            ],
        )

        result = await runtime.run_flow(app_def, "deploy now")

        assert result.success
        assert len(results) == 1
        assert "production" in str(results[0]["command"])


# ─── YAML Demo Validation ────────────────────────────────────────────

# Example YAML files live at the repo root, not the package root
_REPO_ROOT = Path(__file__).resolve().parents[5]  # apps → unit → tests → llmos-bridge → packages → repo root


class TestClaudeCodeYAML:
    def test_claude_code_yaml_loads(self):
        """The claude-code.app.yaml demo compiles without errors."""
        from llmos_bridge.apps.compiler import AppCompiler

        compiler = AppCompiler()
        app_def = compiler.compile_file(str(_REPO_ROOT / "examples/claude-code.app.yaml"))

        assert app_def.app.name == "claude-code"
        assert app_def.app.version == "6.0"
        # Verify security: block is parsed
        assert app_def.security is not None
        assert app_def.security.profile == "power_user"
        assert "rm -rf /" in app_def.security.sandbox.blocked_commands

    def test_claude_code_yaml_has_macros(self):
        """The demo defines macros."""
        from llmos_bridge.apps.compiler import AppCompiler

        compiler = AppCompiler()
        app_def = compiler.compile_file(str(_REPO_ROOT / "examples/claude-code.app.yaml"))

        assert len(app_def.macros) == 4
        macro_names = [m.name for m in app_def.macros]
        assert "read_and_lint" in macro_names
        assert "run_tests" in macro_names
        assert "git_status" in macro_names
        assert "safe_shell" in macro_names

    def test_claude_code_yaml_has_agent(self):
        """The demo has an agent configured."""
        from llmos_bridge.apps.compiler import AppCompiler

        compiler = AppCompiler()
        app_def = compiler.compile_file(str(_REPO_ROOT / "examples/claude-code.app.yaml"))

        assert app_def.agent is not None
        assert app_def.agent.brain.provider == "anthropic"

    def test_claude_code_yaml_has_triggers(self):
        """The demo defines CLI and HTTP triggers."""
        from llmos_bridge.apps.compiler import AppCompiler

        compiler = AppCompiler()
        app_def = compiler.compile_file(str(_REPO_ROOT / "examples/claude-code.app.yaml"))

        assert len(app_def.triggers) >= 4
        trigger_types = [t.type.value for t in app_def.triggers]
        assert "cli" in trigger_types
        assert "http" in trigger_types
        assert "watch" in trigger_types
        assert "schedule" in trigger_types

    def test_claude_code_yaml_has_memory(self):
        """The demo configures memory."""
        from llmos_bridge.apps.compiler import AppCompiler

        compiler = AppCompiler()
        app_def = compiler.compile_file(str(_REPO_ROOT / "examples/claude-code.app.yaml"))

        assert app_def.memory is not None
        assert app_def.memory.conversation.max_history == 500


class TestCodeReviewerYAML:
    def test_compiles(self):
        from llmos_bridge.apps.compiler import AppCompiler

        app_def = AppCompiler().compile_file(str(_REPO_ROOT / "examples/code-reviewer.app.yaml"))
        assert app_def.app.name == "code-reviewer"
        assert app_def.agent is not None
        assert len(app_def.macros) == 4

    def test_macros_complete(self):
        from llmos_bridge.apps.compiler import AppCompiler

        app_def = AppCompiler().compile_file(str(_REPO_ROOT / "examples/code-reviewer.app.yaml"))
        names = {m.name for m in app_def.macros}
        assert names == {"git_diff", "run_linter", "check_file", "summarize_review"}

    def test_has_triggers_and_memory(self):
        from llmos_bridge.apps.compiler import AppCompiler

        app_def = AppCompiler().compile_file(str(_REPO_ROOT / "examples/code-reviewer.app.yaml"))
        assert len(app_def.triggers) == 2
        assert app_def.memory is not None


class TestResearchAgentYAML:
    def test_compiles(self):
        from llmos_bridge.apps.compiler import AppCompiler

        app_def = AppCompiler().compile_file(str(_REPO_ROOT / "examples/research-agent.app.yaml"))
        assert app_def.app.name == "research-agent"

    def test_multi_agent(self):
        from llmos_bridge.apps.compiler import AppCompiler

        app_def = AppCompiler().compile_file(str(_REPO_ROOT / "examples/research-agent.app.yaml"))
        assert app_def.agents is not None
        assert len(app_def.agents.agents) == 3
        agent_ids = {a.id for a in app_def.agents.agents}
        assert agent_ids == {"planner", "researcher", "writer"}

    def test_has_flow(self):
        from llmos_bridge.apps.compiler import AppCompiler

        app_def = AppCompiler().compile_file(str(_REPO_ROOT / "examples/research-agent.app.yaml"))
        assert app_def.flow is not None
        assert len(app_def.flow) == 6

    def test_flow_uses_macros(self):
        from llmos_bridge.apps.compiler import AppCompiler

        app_def = AppCompiler().compile_file(str(_REPO_ROOT / "examples/research-agent.app.yaml"))
        macro_steps = [s for s in app_def.flow if s.use]
        assert len(macro_steps) >= 2  # setup + save_output

    def test_has_parallel_step(self):
        from llmos_bridge.apps.compiler import AppCompiler

        app_def = AppCompiler().compile_file(str(_REPO_ROOT / "examples/research-agent.app.yaml"))
        parallel_steps = [s for s in app_def.flow if s.parallel]
        assert len(parallel_steps) == 1
        assert parallel_steps[0].parallel.max_concurrent == 3


# ─── Goto Tests ──────────────────────────────────────────────────────


class TestGoto:
    """Tests for goto flow step."""

    @pytest.mark.asyncio
    async def test_goto_jumps_to_target(self):
        """goto jumps to a labeled step, skipping intermediate steps."""
        steps = [
            FlowStep(id="step1", action="mod.act1"),
            FlowStep(id="jump", goto="step3"),
            FlowStep(id="step2", action="mod.act2"),  # should be skipped
            FlowStep(id="step3", action="mod.act3"),
        ]
        executor = FlowExecutor(execute_action=mock_action)
        result = await executor.execute(steps)
        assert result.success
        assert "step1" in result.results
        assert "step2" not in result.results  # skipped by goto
        assert "step3" in result.results

    @pytest.mark.asyncio
    async def test_goto_backwards_creates_loop(self):
        """goto can jump backwards, creating a loop (with infinite loop protection)."""
        call_count = 0

        async def counting_action(mod, act, params):
            nonlocal call_count
            call_count += 1
            return {"count": call_count}

        # This will loop: step1 → goto step1 → step1 → goto step1 → ...
        # until max_goto_jumps (100) is hit
        steps = [
            FlowStep(id="step1", action="mod.act1"),
            FlowStep(id="back", goto="step1"),
        ]
        executor = FlowExecutor(execute_action=counting_action)
        result = await executor.execute(steps)
        assert not result.success
        assert "infinite loop" in result.error.lower() or "max goto" in result.error.lower()
        assert call_count > 1  # executed multiple times before failing

    @pytest.mark.asyncio
    async def test_goto_invalid_target_fails(self):
        """goto to a nonexistent step fails at runtime."""
        steps = [
            FlowStep(id="step1", action="mod.act1"),
            FlowStep(id="bad_goto", goto="nonexistent"),
        ]
        executor = FlowExecutor(execute_action=mock_action)
        result = await executor.execute(steps)
        assert not result.success
        assert "nonexistent" in result.error

    @pytest.mark.asyncio
    async def test_goto_with_condition_via_branch(self):
        """goto combined with branch for conditional jumping."""
        from llmos_bridge.apps.models import BranchConfig

        ctx = ExpressionContext(variables={"status": "retry"})
        steps = [
            FlowStep(id="step1", action="mod.act1"),
            FlowStep(
                id="check",
                branch=BranchConfig(
                    on="{{status}}",
                    cases={
                        "retry": [FlowStep(goto="step1")],
                        "done": [FlowStep(action="mod.finish")],
                    },
                ),
            ),
        ]
        # Since "retry" case does goto back to step1, it will loop
        executor = FlowExecutor(
            execute_action=mock_action,
            expr_engine=ExpressionEngine(),
            expr_context=ctx,
        )
        result = await executor.execute(steps)
        # Should hit max goto limit
        assert not result.success

    def test_compiler_validates_goto_targets(self):
        """Compiler catches invalid goto targets at compile time."""
        from llmos_bridge.apps.compiler import AppCompiler, CompilationError

        yaml_text = """
app:
  name: test-goto
  version: "1.0"
agent: {}
flow:
  - id: step1
    action: mod.act1
  - id: bad_goto
    goto: nonexistent_step
"""
        compiler = AppCompiler()
        with pytest.raises(CompilationError, match="nonexistent_step"):
            compiler.compile_string(yaml_text)

    def test_compiler_allows_valid_goto(self):
        """Compiler passes when goto targets exist."""
        from llmos_bridge.apps.compiler import AppCompiler

        yaml_text = """
app:
  name: test-goto
  version: "1.0"
agent: {}
flow:
  - id: step1
    action: mod.act1
  - id: jump
    goto: step1
"""
        compiler = AppCompiler()
        app_def = compiler.compile_string(yaml_text)
        assert app_def.flow[1].goto == "step1"


# ─── Model Constraint Tests ─────────────────────────────────────────


class TestModelConstraints:
    """Tests for enum constraints and type safety."""

    def test_macro_param_type_validated(self):
        """MacroParam.type rejects invalid types."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            MacroParam(type="invalid_type")

    def test_macro_param_accepts_aliases(self):
        """MacroParam.type accepts common type aliases."""
        assert MacroParam(type="integer").type == "integer"
        assert MacroParam(type="number").type == "number"
        assert MacroParam(type="boolean").type == "boolean"
        assert MacroParam(type="list").type == "list"

    def test_macro_definition_normalizes_dict_params(self):
        """MacroDefinition normalizes raw dicts to MacroParam objects."""
        macro = MacroDefinition(
            name="test",
            params={"p1": {"type": "string", "default": "x"}},
            body=[FlowStep(action="mod.act1")],
        )
        assert isinstance(macro.params["p1"], MacroParam)
        assert macro.params["p1"].default == "x"

    def test_end_config_rejects_invalid_status(self):
        """EndConfig.status rejects invalid values."""
        from pydantic import ValidationError
        from llmos_bridge.apps.models import EndConfig

        with pytest.raises(ValidationError):
            EndConfig(status="invalid")

    def test_catch_handler_then_rejects_invalid(self):
        """CatchHandler.then rejects invalid values."""
        from pydantic import ValidationError
        from llmos_bridge.apps.models import CatchHandler

        with pytest.raises(ValidationError):
            CatchHandler(then="invalid")

    def test_trigger_body_accepts_nested_dicts(self):
        """TriggerDefinition body/response accept nested structures."""
        from llmos_bridge.apps.models import TriggerDefinition, TriggerType

        t = TriggerDefinition(
            type=TriggerType.http,
            body={"nested": {"key": 42, "list": [1, 2]}},
            response={"status": 200, "data": {"ok": True}},
        )
        assert t.body["nested"]["key"] == 42
        assert t.response["data"]["ok"] is True
