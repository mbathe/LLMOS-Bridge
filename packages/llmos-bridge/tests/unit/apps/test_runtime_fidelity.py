"""Tests for runtime fidelity — guarantees that YAML specs are enforced at runtime.

These tests verify that every constraint, limit, and rule defined in .app.yaml
is actually enforced during execution. The compiler validates syntax; these tests
validate that the RUNTIME respects the contract.

Gaps addressed:
  1. app.timeout — hard deadline on entire run
  2. app.max_concurrent_runs — semaphore-based concurrency limit
  3. app.max_actions_per_turn — caps tool calls per turn
  4. flow step timeout and retry
  5. grant-level constraints enforcement
  6. max_file_size and max_response_size
  7. max_turns_per_run as hard cap
  8. working_directory constraint
  9. forbidden_tables constraint
"""

from __future__ import annotations

import asyncio
import json
import pytest
import time
from unittest.mock import AsyncMock, MagicMock

from llmos_bridge.apps.agent_runtime import (
    AgentRuntime,
    AgentRunResult,
    LLMProvider,
    StreamEvent,
    ToolCallRequest,
    ToolCallResult,
)
from llmos_bridge.apps.daemon_executor import (
    DaemonToolExecutor,
    _ExecutionScope,
    _current_scope,
    _get_scope,
    _parse_size,
)
from llmos_bridge.apps.flow_executor import FlowExecutor, FlowResult, StepResult
from llmos_bridge.apps.expression import ExpressionContext, ExpressionEngine
from llmos_bridge.apps.models import (
    AgentConfig,
    AppConfig,
    AppDefinition,
    ApprovalRule,
    BrainConfig,
    CapabilitiesConfig,
    CapabilityGrant,
    ContextConfig,
    FlowStep,
    LoopConfig,
    MemoryConfig,
    ObservabilityConfig,
    LoggingConfig,
    ProjectMemoryConfig,
    EpisodicMemoryConfig,
    EpisodicRecallConfig,
    RetryConfig,
    StreamingConfig,
    ToolConstraints,
    ToolDefinition,
    WorkingMemoryConfig,
)
from llmos_bridge.apps.runtime import AppRuntime, AppRuntimeError
from llmos_bridge.apps.tool_registry import ResolvedTool


# ── Helpers ──────────────────────────────────────────────────────────


def _stub_llm(responses: list[dict] | None = None):
    """Create a stub LLM that returns canned responses."""
    llm = AsyncMock(spec=LLMProvider)
    if responses is None:
        responses = [{"text": "Done", "tool_calls": [], "done": True}]
    call_count = 0

    async def _chat(**kwargs):
        nonlocal call_count
        idx = min(call_count, len(responses) - 1)
        call_count += 1
        return responses[idx]

    llm.chat = _chat
    llm.close = AsyncMock()
    return llm


def _make_app_def(
    timeout: str = "3600s",
    max_concurrent_runs: int = 5,
    max_turns_per_run: int = 200,
    max_actions_per_turn: int = 50,
    **kwargs,
) -> AppDefinition:
    """Create a minimal AppDefinition for testing."""
    return AppDefinition(
        app=AppConfig(
            name="test-app",
            timeout=timeout,
            max_concurrent_runs=max_concurrent_runs,
            max_turns_per_run=max_turns_per_run,
            max_actions_per_turn=max_actions_per_turn,
        ),
        agent=AgentConfig(
            brain=BrainConfig(provider="stub", model="stub"),
            system_prompt="You are a test agent.",
            loop=LoopConfig(max_turns=200),
        ),
        **kwargs,
    )


def _mock_registry():
    """Create a mock ModuleRegistry."""
    registry = MagicMock()
    module = AsyncMock()
    module.execute = AsyncMock(return_value={"result": "ok"})
    registry.get = MagicMock(return_value=module)
    registry.all_manifests = MagicMock(return_value=[])
    return registry, module


# ── GAP 1: app.timeout ──────────────────────────────────────────────


class TestAppTimeout:
    @pytest.mark.asyncio
    async def test_app_timeout_enforced(self):
        """App execution must stop when app.timeout is exceeded."""
        app_def = _make_app_def(timeout="100ms")

        # LLM that takes forever to respond
        async def slow_chat(**kwargs):
            await asyncio.sleep(10)
            return {"text": "done", "tool_calls": [], "done": True}

        llm = AsyncMock(spec=LLMProvider)
        llm.chat = slow_chat
        llm.close = AsyncMock()

        runtime = AppRuntime(
            llm_provider_factory=lambda brain: llm,
            execute_tool=AsyncMock(),
        )

        result = await runtime.run(app_def, "test input")
        assert not result.success
        assert result.stop_reason == "timeout"
        assert "timeout" in result.error.lower()

    @pytest.mark.asyncio
    async def test_app_timeout_not_triggered_for_fast_runs(self):
        """Normal runs should not hit the timeout."""
        app_def = _make_app_def(timeout="60s")
        llm = _stub_llm()

        runtime = AppRuntime(
            llm_provider_factory=lambda brain: llm,
            execute_tool=AsyncMock(),
        )
        result = await runtime.run(app_def, "quick task")
        assert result.success


# ── GAP 2: max_concurrent_runs ──────────────────────────────────────


class TestMaxConcurrentRuns:
    @pytest.mark.asyncio
    async def test_concurrent_runs_limited(self):
        """Cannot exceed max_concurrent_runs simultaneously."""
        app_def = _make_app_def(max_concurrent_runs=1, timeout="10s")
        barrier = asyncio.Event()

        async def slow_chat(**kwargs):
            await barrier.wait()
            return {"text": "done", "tool_calls": [], "done": True}

        llm = AsyncMock(spec=LLMProvider)
        llm.chat = slow_chat
        llm.close = AsyncMock()

        runtime = AppRuntime(
            llm_provider_factory=lambda brain: llm,
            execute_tool=AsyncMock(),
        )

        # Start first run (holds semaphore)
        task1 = asyncio.create_task(runtime.run(app_def, "first"))
        await asyncio.sleep(0.05)  # Let it acquire semaphore

        # Second run should be rejected
        result2 = await runtime.run(app_def, "second")
        assert not result2.success
        assert "concurrent" in result2.error.lower()

        # Release first run
        barrier.set()
        result1 = await task1
        assert result1.success


# ── GAP 3: max_actions_per_turn ─────────────────────────────────────


class TestMaxActionsPerTurn:
    @pytest.mark.asyncio
    async def test_tool_calls_capped(self):
        """LLM returning too many tool calls should be capped."""
        config = AgentConfig(
            brain=BrainConfig(provider="stub", model="stub"),
            loop=LoopConfig(max_turns=2),
        )

        # LLM returns 10 tool calls, but max is 3
        tool_calls = [
            {"id": f"tc_{i}", "name": f"filesystem__read_file", "arguments": {"path": f"/tmp/{i}"}}
            for i in range(10)
        ]
        responses = [
            {"text": None, "tool_calls": tool_calls, "done": False},
            {"text": "Done", "tool_calls": [], "done": True},
        ]
        llm = _stub_llm(responses)

        execute_tool = AsyncMock(return_value={"result": "ok"})

        tools = [ResolvedTool(
            name="filesystem.read_file",
            module="filesystem",
            action="read_file",
            description="Read file",
            parameters={"path": {"type": "string", "required": True}},
        )]

        agent = AgentRuntime(
            agent_config=config,
            llm=llm,
            tools=tools,
            execute_tool=execute_tool,
            max_actions_per_turn=3,
        )

        result = await agent.run("test")
        # Should have been capped to 3 tool calls in the first turn
        assert len(result.turns[0].tool_calls) == 3
        assert execute_tool.call_count == 3


# ── GAP 4+5: Flow step timeout and retry ────────────────────────────


class TestFlowStepTimeoutRetry:
    @pytest.mark.asyncio
    async def test_flow_step_timeout(self):
        """Flow action step should respect timeout field."""
        async def slow_action(module, action, params):
            await asyncio.sleep(5)
            return {"result": "should never get here"}

        executor = FlowExecutor(execute_action=slow_action)
        step = FlowStep(
            id="slow_step",
            action="test.slow_action",
            timeout="100ms",
            on_error="continue",
        )
        result = await executor.execute([step])
        # Step should have timed out
        step_result = result.results.get("slow_step")
        assert step_result is not None
        assert not step_result.success
        assert "timed out" in step_result.error.lower()

    @pytest.mark.asyncio
    async def test_flow_step_retry(self):
        """Flow action step should retry on failure when retry is configured."""
        call_count = 0

        async def flaky_action(module, action, params):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return {"error": "temporary failure"}
            return {"result": "success"}

        executor = FlowExecutor(execute_action=flaky_action)
        step = FlowStep(
            id="retry_step",
            action="test.flaky",
            retry=RetryConfig(max_attempts=3, backoff="fixed"),
        )
        result = await executor.execute([step])
        step_result = result.results.get("retry_step")
        assert step_result is not None
        assert step_result.success
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_flow_step_retry_exhausted(self):
        """Flow step should fail after all retry attempts are exhausted."""
        async def always_fails(module, action, params):
            return {"error": "permanent failure"}

        executor = FlowExecutor(execute_action=always_fails)
        step = FlowStep(
            id="fail_step",
            action="test.fail",
            retry=RetryConfig(max_attempts=2, backoff="fixed"),
            on_error="continue",
        )
        result = await executor.execute([step])
        step_result = result.results.get("fail_step")
        assert step_result is not None
        assert not step_result.success

    @pytest.mark.asyncio
    async def test_flow_step_no_timeout_no_retry(self):
        """Flow steps without timeout/retry should work as before."""
        async def normal_action(module, action, params):
            return {"result": "ok"}

        executor = FlowExecutor(execute_action=normal_action)
        step = FlowStep(id="normal", action="test.action")
        result = await executor.execute([step])
        assert result.success


# ── GAP 6: Grant-level constraints ──────────────────────────────────


class TestGrantConstraints:
    @pytest.mark.asyncio
    async def test_grant_path_constraint_enforced(self):
        """Grant-level path constraints should block actions outside allowed paths."""
        registry, module = _mock_registry()
        executor = DaemonToolExecutor(
            module_registry=registry,
            capabilities=CapabilitiesConfig(
                grant=[
                    CapabilityGrant(
                        module="filesystem",
                        actions=["read_file"],
                        constraints=ToolConstraints(paths=["/tmp"]),
                    ),
                ],
            ),
        )

        # Should block — path outside /tmp
        result = await executor.execute("filesystem", "read_file", {"path": "/etc/passwd"})
        assert "error" in result
        assert "not in allowed paths" in result["error"]

    @pytest.mark.asyncio
    async def test_grant_constraint_allows_valid_path(self):
        """Grant-level path constraints should allow actions within allowed paths."""
        registry, module = _mock_registry()
        executor = DaemonToolExecutor(
            module_registry=registry,
            capabilities=CapabilitiesConfig(
                grant=[
                    CapabilityGrant(
                        module="filesystem",
                        actions=["read_file"],
                        constraints=ToolConstraints(paths=["/tmp"]),
                    ),
                ],
            ),
        )

        result = await executor.execute("filesystem", "read_file", {"path": "/tmp/test.txt"})
        assert "error" not in result or "not in allowed paths" not in result.get("error", "")


# ── GAP 7: max_file_size and max_response_size ──────────────────────


class TestSizeConstraints:
    def test_parse_size(self):
        """_parse_size correctly converts size strings to bytes."""
        assert _parse_size("50MB") == 50 * 1024 * 1024
        assert _parse_size("1GB") == 1024 ** 3
        assert _parse_size("100KB") == 100 * 1024
        assert _parse_size("512B") == 512
        assert _parse_size("1.5MB") == int(1.5 * 1024 * 1024)
        assert _parse_size("") == 0
        assert _parse_size("invalid") == 0

    @pytest.mark.asyncio
    async def test_max_file_size_blocks_large_content(self):
        """max_file_size constraint should block writes with oversized content."""
        registry, module = _mock_registry()
        executor = DaemonToolExecutor(module_registry=registry)

        # Set tool constraints with max_file_size
        scope = _get_scope()
        scope.tool_constraints = {
            "filesystem.write_file": {"max_file_size": "100B"},
        }

        # Content larger than 100 bytes
        large_content = "x" * 200
        result = await executor.execute(
            "filesystem", "write_file", {"path": "/tmp/test", "content": large_content}
        )
        assert "error" in result
        assert "max_file_size" in result["error"]

    @pytest.mark.asyncio
    async def test_max_response_size_truncates(self):
        """max_response_size constraint should flag oversized responses."""
        registry, module = _mock_registry()
        # Module returns a large response
        module.execute = AsyncMock(return_value={"data": "x" * 10000})

        executor = DaemonToolExecutor(module_registry=registry)
        scope = _get_scope()
        scope.tool_constraints = {
            "filesystem.read_file": {"max_response_size": "100B"},
        }

        result = await executor.execute("filesystem", "read_file", {"path": "/tmp/test"})
        assert "error" in result
        assert "max_response_size" in result["error"]


# ── GAP 8: max_turns_per_run ────────────────────────────────────────


class TestMaxTurnsPerRun:
    @pytest.mark.asyncio
    async def test_max_turns_per_run_caps_loop(self):
        """max_turns_per_run should be enforced as hard cap over loop.max_turns."""
        config = AgentConfig(
            brain=BrainConfig(provider="stub", model="stub"),
            loop=LoopConfig(max_turns=100),  # loop says 100
        )

        call_count = 0

        async def chat(**kwargs):
            nonlocal call_count
            call_count += 1
            return {
                "text": None,
                "tool_calls": [{"id": f"tc_{call_count}", "name": "filesystem__read_file", "arguments": {"path": "/tmp"}}],
                "done": False,
            }

        llm = AsyncMock(spec=LLMProvider)
        llm.chat = chat
        llm.close = AsyncMock()

        tools = [ResolvedTool(
            name="filesystem.read_file",
            module="filesystem",
            action="read_file",
            description="Read file",
            parameters={"path": {"type": "string", "required": True}},
        )]

        agent = AgentRuntime(
            agent_config=config,
            llm=llm,
            tools=tools,
            execute_tool=AsyncMock(return_value={"result": "ok"}),
            max_turns_per_run=3,  # hard cap at 3
        )

        result = await agent.run("test")
        # Should stop at 3 turns (not 100)
        assert result.total_turns == 3
        assert result.stop_reason == "max_turns"


# ── GAP 9: working_directory constraint ─────────────────────────────


class TestWorkingDirectoryConstraint:
    @pytest.mark.asyncio
    async def test_working_directory_blocks_outside(self):
        """working_directory constraint should block commands outside allowed dir."""
        registry, module = _mock_registry()
        executor = DaemonToolExecutor(module_registry=registry)

        scope = _get_scope()
        scope.tool_constraints = {
            "os_exec.run_command": {"working_directory": "/home/user/project"},
        }

        result = await executor.execute(
            "os_exec", "run_command",
            {"command": "ls", "working_directory": "/etc"},
        )
        assert "error" in result
        assert "outside allowed directory" in result["error"]

    @pytest.mark.asyncio
    async def test_working_directory_allows_inside(self):
        """working_directory constraint should allow commands inside allowed dir."""
        registry, module = _mock_registry()
        executor = DaemonToolExecutor(module_registry=registry)

        scope = _get_scope()
        scope.tool_constraints = {
            "os_exec.run_command": {"working_directory": "/home/user/project"},
        }

        result = await executor.execute(
            "os_exec", "run_command",
            {"command": "ls", "working_directory": "/home/user/project/src"},
        )
        # Should not be blocked by working_directory
        assert "outside allowed directory" not in result.get("error", "")


# ── GAP 10: forbidden_tables constraint ─────────────────────────────


class TestForbiddenTablesConstraint:
    @pytest.mark.asyncio
    async def test_forbidden_tables_blocks_query(self):
        """forbidden_tables constraint should block queries referencing forbidden tables."""
        registry, module = _mock_registry()
        executor = DaemonToolExecutor(module_registry=registry)

        scope = _get_scope()
        scope.tool_constraints = {
            "database.query": {"forbidden_tables": ["users", "credentials"]},
        }

        result = await executor.execute(
            "database", "query",
            {"query": "SELECT * FROM users WHERE id = 1"},
        )
        assert "error" in result
        assert "forbidden table" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_forbidden_tables_allows_safe_query(self):
        """forbidden_tables should allow queries that don't reference forbidden tables."""
        registry, module = _mock_registry()
        executor = DaemonToolExecutor(module_registry=registry)

        scope = _get_scope()
        scope.tool_constraints = {
            "database.query": {"forbidden_tables": ["credentials"]},
        }

        result = await executor.execute(
            "database", "query",
            {"query": "SELECT * FROM products WHERE price > 10"},
        )
        assert "forbidden table" not in result.get("error", "").lower()


# ── GAP 10: brain.temperature & top_p ─────────────────────────────


class TestBrainTemperatureTopP:
    @pytest.mark.asyncio
    async def test_temperature_passed_to_llm(self):
        """brain.temperature must be passed through to LLM.chat()."""
        app_def = _make_app_def()
        app_def.agent.brain.temperature = 0.7
        app_def.agent.brain.top_p = 0.9

        # Track what params LLM receives
        received_kwargs = {}

        async def capture_chat(**kwargs):
            received_kwargs.update(kwargs)
            return {"text": "Done", "tool_calls": [], "done": True}

        llm = AsyncMock(spec=LLMProvider)
        llm.chat = capture_chat
        llm.close = AsyncMock()

        agent = AgentRuntime(
            agent_config=app_def.agent,
            llm=llm,
            tools=[],
            execute_tool=AsyncMock(),
        )
        await agent.run("test input")

        assert received_kwargs.get("temperature") == 0.7
        assert received_kwargs.get("top_p") == 0.9

    @pytest.mark.asyncio
    async def test_default_none_omits_temperature_and_top_p(self):
        """Default temperature=None and top_p=None should NOT be sent to LLM."""
        app_def = _make_app_def()  # Default: temperature=None, top_p=None

        received_kwargs = {}

        async def capture_chat(**kwargs):
            received_kwargs.update(kwargs)
            return {"text": "Done", "tool_calls": [], "done": True}

        llm = AsyncMock(spec=LLMProvider)
        llm.chat = capture_chat
        llm.close = AsyncMock()

        agent = AgentRuntime(
            agent_config=app_def.agent,
            llm=llm,
            tools=[],
            execute_tool=AsyncMock(),
        )
        await agent.run("test")

        assert "temperature" not in received_kwargs
        assert "top_p" not in received_kwargs


# ── GAP 11: memory.project.auto_inject ────────────────────────────


class TestMemoryAutoInject:
    @pytest.mark.asyncio
    async def test_auto_inject_false_skips_project_memory(self):
        """When auto_inject=False, project memory must NOT be in context."""
        from llmos_bridge.apps.memory_manager import AppMemoryManager

        config = MemoryConfig(
            project=ProjectMemoryConfig(
                auto_inject=False,
                path="/tmp/test-memory.md",
            ),
        )
        mgr = AppMemoryManager(config=config)

        # Even if load_project_memory would return content, auto_inject=False skips it
        context = await mgr.build_memory_context("test input")
        assert "project" not in context

    @pytest.mark.asyncio
    async def test_auto_inject_true_loads_project_memory(self):
        """When auto_inject=True (default), project memory is loaded."""
        import tempfile
        from llmos_bridge.apps.memory_manager import AppMemoryManager

        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("# Project context\nImportant info.")
            f.flush()

            config = MemoryConfig(
                project=ProjectMemoryConfig(
                    auto_inject=True,
                    path=f.name,
                ),
            )
            mgr = AppMemoryManager(config=config)
            context = await mgr.build_memory_context("test")
            assert "project" in context
            assert "Important info" in context["project"]

        import os
        os.unlink(f.name)


# ── GAP 12: memory.working.max_size ───────────────────────────────


class TestWorkingMemoryMaxSize:
    def test_max_size_evicts_oldest(self):
        """Working memory must evict oldest entries when max_size exceeded."""
        from llmos_bridge.apps.memory_manager import AppMemoryManager

        config = MemoryConfig(
            working=WorkingMemoryConfig(max_size="100B"),
        )
        mgr = AppMemoryManager(config=config)

        # Fill up working memory
        mgr.set_working("key1", "x" * 40)
        mgr.set_working("key2", "y" * 40)
        mgr.set_working("key3", "z" * 40)

        # With 100B limit, oldest keys should be evicted
        keys = list(mgr.working.keys())
        total_size = len(json.dumps(mgr.working, default=str).encode("utf-8"))
        assert total_size <= 100


# ── GAP 13: episodic.auto_recall.min_similarity ───────────────────


class TestEpisodicMinSimilarity:
    @pytest.mark.asyncio
    async def test_min_similarity_filters_results(self):
        """Episodes with low similarity must be filtered out."""
        from llmos_bridge.apps.memory_manager import AppMemoryManager

        config = MemoryConfig(
            episodic=EpisodicMemoryConfig(
                auto_recall=EpisodicRecallConfig(
                    on_start=True,
                    min_similarity=0.8,
                    limit=10,
                ),
            ),
        )

        # Mock vector store with results of varying similarity
        vector_store = AsyncMock()
        entry_close = MagicMock(id="ep1", text="relevant", metadata={}, distance=0.1)  # high similarity
        entry_far = MagicMock(id="ep2", text="irrelevant", metadata={}, distance=0.5)  # low similarity
        vector_store.search = AsyncMock(return_value=[entry_close, entry_far])

        mgr = AppMemoryManager(config=config, vector_store=vector_store)
        context = await mgr.build_memory_context("test query")

        # min_similarity=0.8 → threshold=0.2 → only ep1 (distance 0.1) passes
        assert "episodic" in context
        assert len(context["episodic"]) == 1
        assert context["episodic"][0]["id"] == "ep1"


# ── GAP 14: inject_on_start ───────────────────────────────────────


class TestInjectOnStart:
    @pytest.mark.asyncio
    async def test_inject_on_start_adds_to_context(self):
        """loop.context.inject_on_start snippets must be added before first user message."""
        app_def = _make_app_def()
        app_def.agent.loop.context.inject_on_start = [
            "Remember: always use safe file operations.",
            "Project uses Python 3.11+.",
        ]

        messages_sent = []

        async def capture_chat(**kwargs):
            messages_sent.append(list(kwargs.get("messages", [])))
            return {"text": "Done", "tool_calls": [], "done": True}

        llm = AsyncMock(spec=LLMProvider)
        llm.chat = capture_chat
        llm.close = AsyncMock()

        agent = AgentRuntime(
            agent_config=app_def.agent,
            llm=llm,
            tools=[],
            execute_tool=AsyncMock(),
        )
        await agent.run("hello")

        # The first LLM call should have inject_on_start messages before the user input
        first_call_messages = messages_sent[0]
        # inject_on_start adds user/assistant pairs, then the actual user message
        # So we should see at least 5 messages: 2 inject pairs + 1 user
        assert len(first_call_messages) >= 5
        # First inject snippet
        assert "safe file operations" in first_call_messages[0]["content"]

    @pytest.mark.asyncio
    async def test_no_inject_on_start_by_default(self):
        """Without inject_on_start, no extra messages are added."""
        app_def = _make_app_def()
        assert app_def.agent.loop.context.inject_on_start == []

        messages_sent = []

        async def capture_chat(**kwargs):
            messages_sent.append(list(kwargs.get("messages", [])))
            return {"text": "Done", "tool_calls": [], "done": True}

        llm = AsyncMock(spec=LLMProvider)
        llm.chat = capture_chat
        llm.close = AsyncMock()

        agent = AgentRuntime(
            agent_config=app_def.agent,
            llm=llm,
            tools=[],
            execute_tool=AsyncMock(),
        )
        await agent.run("hello")

        # Only 1 message (the user input)
        assert len(messages_sent[0]) == 1


# ── GAP 15: observability.logging.level ───────────────────────────


class TestLoggingLevelEnforcement:
    def test_logging_level_applied(self):
        """observability.logging.level must set the logger level."""
        app_def = _make_app_def()
        app_def = AppDefinition(
            app=app_def.app,
            agent=app_def.agent,
            observability=ObservabilityConfig(
                logging=LoggingConfig(level="debug"),
            ),
        )

        runtime = AppRuntime()
        runtime._apply_logging_config(app_def)

        import logging
        apps_logger = logging.getLogger("llmos_bridge.apps")
        assert apps_logger.level == logging.DEBUG

    def test_logging_level_warning(self):
        """Setting level to warning should work."""
        app_def = _make_app_def()
        app_def = AppDefinition(
            app=app_def.app,
            agent=app_def.agent,
            observability=ObservabilityConfig(
                logging=LoggingConfig(level="warning"),
            ),
        )

        runtime = AppRuntime()
        runtime._apply_logging_config(app_def)

        import logging
        apps_logger = logging.getLogger("llmos_bridge.apps")
        assert apps_logger.level == logging.WARNING


# ── GAP 16: approval_required blocking ────────────────────────────


class TestApprovalBlocking:
    @pytest.mark.asyncio
    async def test_approval_blocks_and_waits_for_decision(self):
        """When approval_gate is wired, approval_required must BLOCK until human decides."""
        from llmos_bridge.orchestration.approval import (
            ApprovalDecision,
            ApprovalGate,
            ApprovalResponse,
        )
        from llmos_bridge.apps.models import ApprovalRule

        registry, module = _mock_registry()
        executor = DaemonToolExecutor(module_registry=registry)

        # Wire a real ApprovalGate
        gate = ApprovalGate(default_timeout=5.0)
        executor.set_approval_gate(gate)

        # Set up scope with approval rule
        scope = _get_scope()
        scope.capabilities = CapabilitiesConfig(
            approval_required=[
                ApprovalRule(
                    module="os_exec",
                    action="run_command",
                    message="Dangerous command needs approval",
                    timeout="3s",
                ),
            ],
        )

        # Submit approval in background after a short delay
        async def approve_after_delay():
            await asyncio.sleep(0.1)
            pending = gate.get_pending()
            assert len(pending) == 1
            req = pending[0]
            gate.submit_decision(
                req.plan_id, req.action_id,
                ApprovalResponse(decision=ApprovalDecision.APPROVE, approved_by="test"),
            )

        asyncio.create_task(approve_after_delay())

        # This should BLOCK until the approval comes in
        result = await executor.execute("os_exec", "run_command", {"command": ["ls"]})

        # Should have been approved and executed
        assert "error" not in result

    @pytest.mark.asyncio
    async def test_approval_reject_returns_error(self):
        """Rejected approval must return an error without executing."""
        from llmos_bridge.orchestration.approval import (
            ApprovalDecision,
            ApprovalGate,
            ApprovalResponse,
        )
        from llmos_bridge.apps.models import ApprovalRule

        registry, module = _mock_registry()
        executor = DaemonToolExecutor(module_registry=registry)

        gate = ApprovalGate(default_timeout=5.0)
        executor.set_approval_gate(gate)

        scope = _get_scope()
        scope.capabilities = CapabilitiesConfig(
            approval_required=[
                ApprovalRule(module="os_exec", action="run_command"),
            ],
        )

        async def reject_after_delay():
            await asyncio.sleep(0.1)
            pending = gate.get_pending()
            req = pending[0]
            gate.submit_decision(
                req.plan_id, req.action_id,
                ApprovalResponse(decision=ApprovalDecision.REJECT, reason="Too risky"),
            )

        asyncio.create_task(reject_after_delay())

        result = await executor.execute("os_exec", "run_command", {"command": ["rm", "-rf", "/"]})

        assert "error" in result or "Rejected" in str(result.get("error", ""))
        # Module should NOT have been called
        module.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_approval_timeout_rejects(self):
        """Approval timeout must result in rejection."""
        from llmos_bridge.orchestration.approval import ApprovalGate
        from llmos_bridge.apps.models import ApprovalRule

        registry, module = _mock_registry()
        executor = DaemonToolExecutor(module_registry=registry)

        gate = ApprovalGate(default_timeout=0.2)  # Very short timeout
        executor.set_approval_gate(gate)

        scope = _get_scope()
        scope.capabilities = CapabilitiesConfig(
            approval_required=[
                ApprovalRule(module="os_exec", action="run_command", timeout="200ms"),
            ],
        )

        # Don't submit any approval — let it timeout
        result = await executor.execute("os_exec", "run_command", {"command": ["ls"]})

        assert "error" in result or "Rejected" in str(result.get("error", ""))
        module.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_gate_returns_immediate_error(self):
        """Without approval gate (standalone mode), approval returns immediate error."""
        from llmos_bridge.apps.models import ApprovalRule

        registry, module = _mock_registry()
        executor = DaemonToolExecutor(module_registry=registry)
        # No gate wired

        scope = _get_scope()
        scope.capabilities = CapabilitiesConfig(
            approval_required=[
                ApprovalRule(module="os_exec", action="run_command"),
            ],
        )

        result = await executor.execute("os_exec", "run_command", {"command": ["ls"]})
        assert "error" in result
        assert "Approval required" in result["error"]

    @pytest.mark.asyncio
    async def test_auto_approve_skips_wait(self):
        """After APPROVE_ALWAYS, subsequent calls should auto-approve without waiting."""
        from llmos_bridge.orchestration.approval import (
            ApprovalDecision,
            ApprovalGate,
            ApprovalResponse,
        )
        from llmos_bridge.apps.models import ApprovalRule

        registry, module = _mock_registry()
        executor = DaemonToolExecutor(module_registry=registry)

        gate = ApprovalGate(default_timeout=5.0)
        executor.set_approval_gate(gate)

        scope = _get_scope()
        scope.capabilities = CapabilitiesConfig(
            approval_required=[
                ApprovalRule(module="os_exec", action="run_command"),
            ],
        )

        # First call: approve_always
        async def approve_always():
            await asyncio.sleep(0.1)
            pending = gate.get_pending()
            req = pending[0]
            gate.submit_decision(
                req.plan_id, req.action_id,
                ApprovalResponse(decision=ApprovalDecision.APPROVE_ALWAYS),
            )

        asyncio.create_task(approve_always())
        result1 = await executor.execute("os_exec", "run_command", {"command": ["ls"]})
        assert "error" not in result1

        # Second call: should auto-approve without blocking
        result2 = await executor.execute("os_exec", "run_command", {"command": ["pwd"]})
        assert "error" not in result2
