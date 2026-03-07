"""AppRuntime — top-level lifecycle manager for LLMOS applications (Agentique Mode).

This is the YAML App Language equivalent of the IML PlanExecutor (Compiler Mode).
Where PlanExecutor executes a deterministic DAG of actions, AppRuntime lets
the LLM decide autonomously which tools to call.

Orchestrates:
- AppCompiler (YAML → AppDefinition)
- AgentRuntime (LLM loop)
- FlowExecutor (explicit 18-step flow engine)
- MultiAgentOrchestrator (multi-agent strategies)
- ToolRegistry (module action filtering)
- ExpressionEngine (template resolution)
- BuiltinToolExecutor (ask_user, todo, etc.)
- ContextManager (conversation window)

In daemon mode, tool calls route through DaemonToolExecutor which applies the
full security pipeline (same as IML plans).  In standalone CLI mode, a
StandaloneToolExecutor provides filesystem + os_exec only.

Usage:
    runtime = AppRuntime()
    app_def = runtime.load("my-app.app.yaml")
    result = await runtime.run(app_def, "Fix the bug in main.py")
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Awaitable

from .agent_runtime import AgentRunResult, AgentRuntime, LLMProvider, StreamEvent
from .builtins import BuiltinToolExecutor
from .compiler import AppCompiler, CompilationError
from .expression import ExpressionContext, ExpressionEngine
from .flow_executor import FlowExecutor, FlowResult
from .memory_manager import AppMemoryManager
from .models import AppDefinition, AgentConfig, BrainConfig
from .multi_agent import AgentInstance, MultiAgentOrchestrator, MultiAgentResult
from .observability import TracingManager, MetricsCollector
from .tool_registry import AppToolRegistry, ResolvedTool

logger = logging.getLogger(__name__)


class AppRuntimeError(Exception):
    """Raised when an app runtime operation fails."""
    pass


class AppRuntime:
    """Top-level runtime for LLMOS applications.

    Loads .app.yaml files, compiles them, and runs agent loops.
    """

    def __init__(
        self,
        *,
        module_info: dict[str, Any] | None = None,
        llm_provider_factory: Callable[[BrainConfig], LLMProvider] | None = None,
        execute_tool: Callable[[str, str, dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
        input_handler: Callable[[str], Awaitable[str]] | None = None,
        output_handler: Callable[[str], Awaitable[None]] | None = None,
        kv_store: Any = None,
        vector_store: Any = None,
        event_bus: Any = None,
    ):
        self._compiler = AppCompiler()
        self._expr_engine = ExpressionEngine()
        self._module_info = module_info or {}
        self._llm_factory = llm_provider_factory
        self._execute_tool = execute_tool
        self._input_handler = input_handler
        self._output_handler = output_handler
        self._kv_store = kv_store
        self._vector_store = vector_store
        self._event_bus = event_bus
        self._memory_module: Any = None
        self._context_manager_module: Any = None
        # LLM provider pool: reuse connections across runs to avoid TCP/TLS overhead.
        # Bounded to prevent memory leaks with many model variants.
        self._llm_pool: dict[str, LLMProvider] = {}  # key = "provider:model"
        self._llm_pool_max: int = 32  # max cached providers (covers typical multi-model apps)
        # Per-app concurrency semaphores: keyed by app name
        self._concurrency_semaphores: dict[str, asyncio.Semaphore] = {}

    def set_memory_module(self, memory_module: Any) -> None:
        """Set the memory module for cognitive context auto-injection."""
        self._memory_module = memory_module

    def set_context_manager_module(self, context_module: Any) -> None:
        """Set the context manager module for budget-aware context management."""
        self._context_manager_module = context_module

    async def prepare(self, app_def: AppDefinition) -> dict[str, Any]:
        """Pre-load all resources needed for fast app execution.

        This is the daemon's "prepare" step — called after registration,
        before the first run. It ensures that:
        - All required modules are available and healthy
        - LLM providers are pre-warmed (connection pool established)
        - Memory backends are initialized
        - Tools are resolved and validated
        - Security settings (capabilities, constraints) are applied

        Returns a dict with preparation status and timing info.
        """
        t0 = time.time()
        results: dict[str, Any] = {
            "app_name": app_def.app.name,
            "modules_checked": 0,
            "modules_missing": [],
            "tools_resolved": 0,
            "llm_warmed": False,
            "memory_ready": False,
            "capabilities_applied": False,
            "duration_ms": 0,
        }

        # 1. Check all required modules are available
        required_modules = app_def.get_all_module_ids()
        available_modules = set(self._module_info.keys())
        missing = required_modules - available_modules
        results["modules_checked"] = len(required_modules)
        results["modules_missing"] = sorted(missing)
        if missing:
            logger.warning("App %s requires missing modules: %s", app_def.app.name, missing)

        # 2. Resolve all tools (validates they exist in module_info)
        tool_registry = AppToolRegistry(self._module_info)
        all_tools = app_def.get_all_tools()
        if not all_tools and app_def.agent and app_def.agent.tools:
            all_tools = app_def.agent.tools
        if all_tools:
            resolved = tool_registry.resolve_tools(all_tools)
            results["tools_resolved"] = len(resolved)

        # 3. Pre-warm LLM provider (establish connection pool)
        agents_to_warm = []
        if app_def.agent:
            agents_to_warm.append(app_def.agent)
        if app_def.agents and app_def.agents.agents:
            agents_to_warm.extend(app_def.agents.agents)

        for agent_config in agents_to_warm:
            try:
                llm = await self._create_llm(agent_config.brain, pooled=True)
                results["llm_warmed"] = True
                logger.info(
                    "Pre-warmed LLM: %s/%s",
                    agent_config.brain.provider,
                    agent_config.brain.model,
                )
            except Exception as e:
                logger.warning("LLM pre-warm failed for %s: %s", agent_config.brain.model, e)

        # 4. Initialize memory backends
        if self._memory_module is not None:
            try:
                await self._memory_module.health_check()
                results["memory_ready"] = True
            except Exception as e:
                logger.warning("Memory health check failed: %s", e)
        elif self._kv_store is not None:
            results["memory_ready"] = True

        # 5. Apply capabilities and security settings
        try:
            self._apply_capabilities(app_def)
            results["capabilities_applied"] = True
        except Exception as e:
            logger.warning("Capabilities apply failed: %s", e)

        results["duration_ms"] = (time.time() - t0) * 1000
        logger.info(
            "App '%s' prepared in %.0fms — %d modules, %d tools, LLM=%s, memory=%s",
            app_def.app.name,
            results["duration_ms"],
            results["modules_checked"],
            results["tools_resolved"],
            results["llm_warmed"],
            results["memory_ready"],
        )
        return results

    def load(self, path: str | Path) -> AppDefinition:
        """Load and compile a .app.yaml file."""
        return self._compiler.compile_file(path)

    def load_string(self, yaml_text: str) -> AppDefinition:
        """Load and compile from a YAML string."""
        return self._compiler.compile_string(yaml_text)

    def validate(self, path: str | Path) -> list[str]:
        """Validate a .app.yaml file without running it.

        Returns:
            Empty list if valid, list of error messages otherwise.
        """
        try:
            self._compiler.compile_file(path)
            return []
        except CompilationError as e:
            return e.errors or [str(e)]

    def _build_expr_context(
        self, app_def: AppDefinition, input_text: str, variables: dict[str, Any] | None = None
    ) -> ExpressionContext:
        """Build expression context for a run.

        Variable values may contain template expressions (e.g. ``{{env.PWD}}``).
        We resolve them in two passes:
        1. Build a temporary context with the raw variable values.
        2. Resolve each variable value through the expression engine.
        This lets ``workspace: "{{env.PWD}}"`` evaluate to the actual PWD.
        """
        merged_vars = {**app_def.variables, **(variables or {})}
        merged_vars.setdefault("workspace", os.environ.get("PWD", os.getcwd()))
        merged_vars.setdefault(
            "data_dir",
            str(Path.home() / ".llmos" / "apps" / app_def.app.name),
        )

        # Resolve template expressions inside variable values (e.g. {{env.PWD}})
        tmp_ctx = ExpressionContext(
            variables=merged_vars,
            trigger={"input": input_text},
            app={
                "name": app_def.app.name,
                "version": app_def.app.version,
                "description": app_def.app.description,
            },
        )
        resolved_vars = {
            k: self._expr_engine.resolve(v, tmp_ctx) if isinstance(v, str) else v
            for k, v in merged_vars.items()
        }

        return ExpressionContext(
            variables=resolved_vars,
            trigger={"input": input_text},
            app={
                "name": app_def.app.name,
                "version": app_def.app.version,
                "description": app_def.app.description,
            },
        )

    def _get_concurrency_semaphore(self, app_def: AppDefinition) -> asyncio.Semaphore:
        """Get or create a concurrency semaphore for the given app."""
        key = app_def.app.name
        if key not in self._concurrency_semaphores:
            self._concurrency_semaphores[key] = asyncio.Semaphore(
                app_def.app.max_concurrent_runs
            )
        return self._concurrency_semaphores[key]

    async def run(
        self,
        app_def: AppDefinition,
        input_text: str,
        *,
        variables: dict[str, Any] | None = None,
    ) -> AgentRunResult:
        """Run an application with given input."""
        # Enforce max_concurrent_runs via per-app semaphore
        sem = self._get_concurrency_semaphore(app_def)
        if sem.locked() and sem._value == 0:
            return AgentRunResult(
                success=False, output="", turns=[], total_turns=0,
                total_tokens=0, duration_ms=0,
                stop_reason="error",
                error=f"Max concurrent runs ({app_def.app.max_concurrent_runs}) exceeded for '{app_def.app.name}'",
            )
        async with sem:
            return await self._run_inner(app_def, input_text, variables=variables)

    async def _run_inner(
        self,
        app_def: AppDefinition,
        input_text: str,
        *,
        variables: dict[str, Any] | None = None,
    ) -> AgentRunResult:
        """Inner run method — called within concurrency semaphore.

        Enforces app.timeout as a hard deadline on the entire run.
        """
        # Parse app-level timeout
        from .flow_executor import _parse_duration
        app_timeout = _parse_duration(app_def.app.timeout)

        try:
            if app_timeout > 0:
                return await asyncio.wait_for(
                    self._run_core(app_def, input_text, variables=variables),
                    timeout=app_timeout,
                )
            return await self._run_core(app_def, input_text, variables=variables)
        except asyncio.TimeoutError:
            return AgentRunResult(
                success=False, output="", turns=[], total_turns=0,
                total_tokens=0, duration_ms=app_timeout * 1000,
                stop_reason="timeout",
                error=f"App timeout exceeded ({app_def.app.timeout})",
            )

    async def _run_core(
        self,
        app_def: AppDefinition,
        input_text: str,
        *,
        variables: dict[str, Any] | None = None,
    ) -> AgentRunResult:
        """Core run logic — agent loop, flow, or multi-agent execution."""
        # Apply observability.logging.level for this app's namespace
        self._apply_logging_config(app_def)

        # Initialize tracing + metrics from observability config
        tracing, metrics = self._create_observability(app_def)

        # Start root trace span
        root_span = tracing.start_trace(
            f"app.run:{app_def.app.name}",
            attributes={"input_length": len(input_text)},
        )

        # Build expression context early so security templates can be resolved
        expr_ctx = self._build_expr_context(app_def, input_text, variables)

        # Inject app-level capabilities into the executor (grant/deny/approval rules)
        self._apply_capabilities(app_def)
        self._apply_security(app_def, expr_ctx)
        # Apply per-module configuration from YAML module_config block
        await self._apply_module_config(app_def)

        # If app has a flow, execute it instead of the agent loop
        if app_def.flow:
            flow_result = await self.run_flow(app_def, input_text, variables=variables)
            return AgentRunResult(
                success=flow_result.success,
                output=str(flow_result.output or ""),
                turns=[],
                total_turns=0,
                total_tokens=0,
                duration_ms=flow_result.duration_ms,
                stop_reason=flow_result.status,
                error=flow_result.error,
            )

        # Multi-agent apps: route to orchestrator
        if app_def.agents and app_def.agents.agents and len(app_def.agents.agents) > 1:
            multi_result = await self.run_multi_agent(app_def, input_text, variables=variables)
            return AgentRunResult(
                success=multi_result.success,
                output=multi_result.output,
                turns=[],
                total_turns=sum(
                    r.total_turns for r in multi_result.agent_results.values()
                ),
                total_tokens=sum(
                    r.total_tokens for r in multi_result.agent_results.values()
                ),
                duration_ms=sum(
                    r.duration_ms for r in multi_result.agent_results.values()
                ),
                stop_reason="task_complete" if multi_result.success else "error",
                error=multi_result.error,
            )

        agent_config = app_def.agent
        if agent_config is None:
            if app_def.agents and app_def.agents.agents:
                agent_config = app_def.agents.agents[0]
            else:
                raise AppRuntimeError("No agent configuration found in app definition")

        # Auto-cap context config to model limits
        self._cap_context_to_model(agent_config)

        # Build memory manager and inject memory into context
        memory_mgr = AppMemoryManager(
            config=app_def.memory,
            kv_store=self._kv_store,
            vector_store=self._vector_store,
            expr_engine=self._expr_engine,
            expr_context=expr_ctx,
        )
        memory_context = await memory_mgr.build_memory_context(input_text)
        if memory_context:
            expr_ctx.memory = memory_context

        # Resolve tools + auto-include builtins
        tool_registry = AppToolRegistry(self._module_info)
        all_tools = app_def.get_all_tools()
        if not all_tools and agent_config.tools:
            all_tools = agent_config.tools
        resolved_tools = tool_registry.resolve_tools(all_tools)
        resolved_tools = self._auto_include_builtins(resolved_tools, app_def, tool_registry)

        # Inject tool constraints into executor
        self._apply_tool_constraints(app_def, resolved_tools, expr_ctx)

        # Create LLM provider (with fallback support)
        llm = await self._create_llm(agent_config.brain)

        # Create builtin executor with event bus and memory manager wired
        builtins = self._create_builtins(memory_mgr)

        # Create agent runtime with observability config
        streaming_config = app_def.observability.streaming if app_def.observability else None

        # Wrap execute_tool with tracing spans + metrics + procedural auto-learn
        traced_execute = self._make_traced_execute(tracing, metrics, memory_mgr)

        agent = AgentRuntime(
            agent_config=agent_config,
            llm=llm,
            tools=resolved_tools,
            execute_tool=traced_execute,
            builtin_executor=builtins,
            expression_engine=self._expr_engine,
            expression_context=expr_ctx,
            streaming_config=streaming_config,
            max_actions_per_turn=app_def.app.max_actions_per_turn,
            max_turns_per_run=app_def.app.max_turns_per_run,
        )

        # Wire cognitive context auto-injection
        self._wire_cognitive_prompt(agent)

        # Wire context manager module for budget-aware context management
        self._wire_context_module(agent, app_def)

        try:
            result = await agent.run(input_text)
            tracing.end_trace(root_span, status="ok" if result.success else "error")
            # Auto-record episodic memory after successful run
            await self._auto_record_episode(memory_mgr, app_def, input_text, result)
            return result
        except Exception:
            tracing.end_trace(root_span, status="error")
            raise
        finally:
            await llm.close()

    async def run_flow(
        self,
        app_def: AppDefinition,
        input_text: str,
        *,
        variables: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Execute the flow defined in the app."""
        if not app_def.flow:
            raise AppRuntimeError("No flow defined in app definition")

        expr_ctx = self._build_expr_context(app_def, input_text, variables)

        # Wire tracing + metrics for flow actions
        tracing, metrics = self._create_observability(app_def)
        root_span = tracing.start_trace(
            f"flow.run:{app_def.app.name}",
            attributes={"input_length": len(input_text)},
        )
        traced_execute = self._make_traced_execute(tracing, metrics)

        # Agent runner for 'agent' steps in flow
        async def run_agent(agent_id: str, agent_input: str) -> Any:
            agent_config = app_def.agent
            if agent_id != "default" and app_def.agents:
                agent_config = app_def.get_agent(agent_id)
            if agent_config is None:
                return {"error": f"Agent '{agent_id}' not found"}
            llm = await self._create_llm(agent_config.brain)
            try:
                tool_registry = AppToolRegistry(self._module_info)
                tools = tool_registry.resolve_tools(agent_config.tools)
                runtime = AgentRuntime(
                    agent_config=agent_config,
                    llm=llm,
                    tools=tools,
                    execute_tool=self._execute_tool,
                    expression_engine=self._expr_engine,
                    expression_context=expr_ctx,
                )
                result = await runtime.run(agent_input)
                return {"output": result.output, "success": result.success}
            finally:
                await llm.close()

        # Event emission via bus
        async def emit_event(topic: str, event: dict[str, Any]) -> None:
            if self._event_bus:
                await self._event_bus.emit(topic, event)

        # Spawn callback — loads and runs a sub-app
        async def spawn_app(app_path: str, input_text_inner: str, timeout: float) -> Any:
            sub_def = self.load(app_path)
            result = await asyncio.wait_for(
                self.run(sub_def, input_text_inner, variables=variables),
                timeout=timeout if timeout > 0 else None,
            )
            return {"output": result.output, "success": result.success}

        flow_id = f"{app_def.app.name}-{uuid.uuid4().hex[:8]}"
        executor = FlowExecutor(
            expr_engine=self._expr_engine,
            expr_context=expr_ctx,
            execute_action=traced_execute or self._execute_tool,
            run_agent=run_agent,
            emit_event=emit_event,
            spawn_app=spawn_app,
            macros=app_def.macros,
            kv_store=self._kv_store,
            flow_id=flow_id,
        )

        try:
            result = await executor.execute(
                app_def.flow,
                resume=app_def.app.checkpoint,
            )
            tracing.end_trace(root_span, status="ok" if result.success else "error")
            return result
        except Exception:
            tracing.end_trace(root_span, status="error")
            raise

    async def run_multi_agent(
        self,
        app_def: AppDefinition,
        input_text: str,
        *,
        variables: dict[str, Any] | None = None,
    ) -> MultiAgentResult:
        """Run a multi-agent application."""
        if not app_def.agents or not app_def.agents.agents:
            raise AppRuntimeError("No agents defined")

        # Wire tracing + metrics
        tracing, metrics = self._create_observability(app_def)
        root_span = tracing.start_trace(
            f"multi_agent.run:{app_def.app.name}",
            attributes={"input_length": len(input_text), "strategy": str(app_def.agents.strategy)},
        )
        traced_execute = self._make_traced_execute(tracing, metrics)

        expr_ctx = self._build_expr_context(app_def, input_text, variables)
        tool_registry = AppToolRegistry(self._module_info)

        agents: dict[str, AgentInstance] = {}
        llms: list[LLMProvider] = []

        try:
            for agent_cfg in app_def.agents.agents:
                agent_id = agent_cfg.id or f"agent_{len(agents)}"
                llm = await self._create_llm(agent_cfg.brain)
                llms.append(llm)
                tools = tool_registry.resolve_tools(agent_cfg.tools)
                agents[agent_id] = AgentInstance(config=agent_cfg, llm=llm, tools=tools)

            orchestrator = MultiAgentOrchestrator(
                config=app_def.agents,
                agents=agents,
                execute_tool=traced_execute or self._execute_tool,
                expression_engine=self._expr_engine,
                expression_context=expr_ctx,
            )
            result = await orchestrator.run(input_text)
            tracing.end_trace(root_span, status="ok" if result.success else "error")
            return result
        except Exception:
            tracing.end_trace(root_span, status="error")
            raise
        finally:
            for llm in llms:
                await llm.close()

    async def run_interactive(
        self,
        app_def: AppDefinition,
        *,
        variables: dict[str, Any] | None = None,
        on_event: Callable[[StreamEvent], Awaitable[None]] | None = None,
    ) -> None:
        """Run an application in interactive CLI mode.

        This is the ``llmos app run`` entry point for conversation mode.

        **Performance**: All expensive objects (LLM provider, tool registry,
        memory manager, builtins, expression context) are created ONCE and
        reused across all turns.  Only the AgentRuntime is recreated per turn
        because it holds per-turn state (turns list, stopped flag).
        The LLM connection pool is shared via ``pooled=True``.
        """
        agent_config = app_def.agent
        if agent_config is None:
            if app_def.agents and app_def.agents.agents:
                agent_config = app_def.agents.agents[0]
            else:
                raise AppRuntimeError("No agent configuration found")

        # Print greeting
        greeting = ""
        for trigger in app_def.triggers:
            if trigger.type.value == "cli" and trigger.greeting:
                greeting = trigger.greeting
                break
        if greeting:
            print(greeting)
        else:
            print(f"{app_def.app.name} v{app_def.app.version}")
            print("Type your request (Ctrl+C to exit)\n")

        # Get CLI prompt
        prompt = "> "
        for trigger in app_def.triggers:
            if trigger.type.value == "cli" and trigger.prompt:
                prompt = trigger.prompt
                break

        # ── Session-scoped setup (created ONCE, reused across all turns) ──
        expr_ctx = self._build_expr_context(app_def, "", variables)
        self._apply_capabilities(app_def)
        self._apply_security(app_def, expr_ctx)
        await self._apply_module_config(app_def)

        # Resolve tools once
        tool_registry = AppToolRegistry(self._module_info)
        all_tools = app_def.get_all_tools()
        if not all_tools and agent_config.tools:
            all_tools = agent_config.tools
        resolved_tools = tool_registry.resolve_tools(all_tools)
        resolved_tools = self._auto_include_builtins(resolved_tools, app_def, tool_registry)
        self._apply_tool_constraints(app_def, resolved_tools, expr_ctx)

        # Create LLM once (pooled — shares HTTP connection across turns)
        llm = await self._create_llm(agent_config.brain, pooled=True)

        # Create memory manager once
        memory_mgr = AppMemoryManager(
            config=app_def.memory,
            kv_store=self._kv_store,
            vector_store=self._vector_store,
            expr_engine=self._expr_engine,
            expr_context=expr_ctx,
        )
        builtins = self._create_builtins(memory_mgr)
        streaming_config = app_def.observability.streaming if app_def.observability else None

        # Load conversation history from KV store (cross-session persistence)
        conv_key = f"llmos:app:conversation:{app_def.app.name}"
        conversation_history: list[dict[str, Any]] = []
        if self._kv_store:
            try:
                raw = await self._kv_store.get(conv_key)
                if raw:
                    loaded = raw if isinstance(raw, list) else __import__("json").loads(raw)
                    conversation_history = loaded
                    logger.info("Restored %d messages from previous session", len(conversation_history))
            except Exception:
                pass

        # ── Interactive loop ──
        while True:
            try:
                user_input = input(prompt)
                if not user_input.strip():
                    continue
                if user_input.strip().lower() in ("exit", "quit", "/quit", "/exit"):
                    print("Goodbye!")
                    break
                if user_input.strip() == "/clear":
                    conversation_history.clear()
                    if self._kv_store:
                        await self._kv_store.delete(conv_key)
                    print("Conversation cleared.\n")
                    continue
            except (EOFError, KeyboardInterrupt):
                print("\nGoodbye!")
                break

            # Update trigger input in expression context
            expr_ctx.trigger = {"input": user_input}

            # Build memory context for this turn
            memory_context = await memory_mgr.build_memory_context(user_input)
            if memory_context:
                expr_ctx.memory = memory_context

            try:
                if on_event:
                    # Streaming mode — create a lightweight AgentRuntime per turn
                    agent = AgentRuntime(
                        agent_config=agent_config,
                        llm=llm,
                        tools=resolved_tools,
                        execute_tool=self._execute_tool,
                        builtin_executor=builtins,
                        expression_engine=self._expr_engine,
                        expression_context=expr_ctx,
                        streaming_config=streaming_config,
                    )
                    self._wire_cognitive_prompt(agent)
                    self._wire_context_module(agent, app_def)

                    if conversation_history:
                        agent.inject_conversation_history(conversation_history)

                    async for event in agent.stream(user_input):
                        await on_event(event)
                        if event.type == "conversation_update":
                            conversation_history = event.data.get("messages", [])

                    # Capture updated history
                    messages = agent.get_conversation_messages()
                    if messages:
                        conversation_history = messages
                else:
                    # Non-streaming — create agent, run, print
                    agent = AgentRuntime(
                        agent_config=agent_config,
                        llm=llm,
                        tools=resolved_tools,
                        execute_tool=self._execute_tool,
                        builtin_executor=builtins,
                        expression_engine=self._expr_engine,
                        expression_context=expr_ctx,
                        streaming_config=streaming_config,
                    )
                    self._wire_cognitive_prompt(agent)
                    self._wire_context_module(agent, app_def)

                    if conversation_history:
                        agent.inject_conversation_history(conversation_history)

                    result = await agent.run(user_input)
                    conversation_history = agent.get_conversation_messages()

                    if result.output:
                        print(f"\n{result.output}\n")
                    if result.error:
                        print(f"Error: {result.error}\n")

                # Save conversation to KV store
                if self._kv_store and conversation_history:
                    try:
                        import json as _json
                        await self._kv_store.set(conv_key, _json.dumps(conversation_history[-200:]))
                    except Exception:
                        pass
            except Exception as e:
                logger.exception("Run failed")
                print(f"Error: {e}\n")

    async def stream(
        self,
        app_def: AppDefinition,
        input_text: str,
        *,
        variables: dict[str, Any] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Run and stream events from an application."""
        expr_ctx = self._build_expr_context(app_def, input_text, variables)
        self._apply_capabilities(app_def)
        self._apply_security(app_def, expr_ctx)

        agent_config = app_def.agent
        if agent_config is None:
            if app_def.agents and app_def.agents.agents:
                agent_config = app_def.agents.agents[0]
            else:
                raise AppRuntimeError("No agent configuration found")

        # Build memory manager
        memory_mgr = AppMemoryManager(
            config=app_def.memory,
            kv_store=self._kv_store,
            vector_store=self._vector_store,
            expr_engine=self._expr_engine,
            expr_context=expr_ctx,
        )
        memory_context = await memory_mgr.build_memory_context(input_text)
        if memory_context:
            expr_ctx.memory = memory_context

        # Resolve tools + auto-include builtins
        tool_registry = AppToolRegistry(self._module_info)
        all_tools = app_def.get_all_tools()
        if not all_tools and agent_config.tools:
            all_tools = agent_config.tools
        resolved_tools = tool_registry.resolve_tools(all_tools)
        resolved_tools = self._auto_include_builtins(resolved_tools, app_def, tool_registry)

        # Inject tool constraints
        self._apply_tool_constraints(app_def, resolved_tools, expr_ctx)

        # Wire tracing + metrics
        tracing, metrics = self._create_observability(app_def)
        root_span = tracing.start_trace(
            f"stream.run:{app_def.app.name}",
            attributes={"input_length": len(input_text)},
        )
        traced_execute = self._make_traced_execute(tracing, metrics)

        llm = await self._create_llm(agent_config.brain)

        builtins = self._create_builtins(memory_mgr)

        streaming_config = app_def.observability.streaming if app_def.observability else None
        agent = AgentRuntime(
            agent_config=agent_config,
            llm=llm,
            tools=resolved_tools,
            execute_tool=traced_execute or self._execute_tool,
            builtin_executor=builtins,
            expression_engine=self._expr_engine,
            expression_context=expr_ctx,
            streaming_config=streaming_config,
        )

        # Wire cognitive context auto-injection
        self._wire_cognitive_prompt(agent)
        self._wire_context_module(agent, app_def)

        try:
            async for event in agent.stream(input_text):
                yield event
            tracing.end_trace(root_span, status="ok")
        except Exception:
            tracing.end_trace(root_span, status="error")
            raise
        finally:
            await llm.close()

    async def _stream_with_history(
        self,
        app_def: AppDefinition,
        input_text: str,
        conversation_history: list[dict[str, Any]],
        *,
        variables: dict[str, Any] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Stream with conversation history injected for cross-turn persistence.

        After the agent finishes, emits a 'conversation_update' event
        containing the full message history for the caller to persist.
        """
        expr_ctx = self._build_expr_context(app_def, input_text, variables)
        self._apply_capabilities(app_def)
        self._apply_security(app_def, expr_ctx)

        agent_config = app_def.agent
        if agent_config is None:
            if app_def.agents and app_def.agents.agents:
                agent_config = app_def.agents.agents[0]
            else:
                raise AppRuntimeError("No agent configuration found")

        memory_mgr = AppMemoryManager(
            config=app_def.memory,
            kv_store=self._kv_store,
            vector_store=self._vector_store,
            expr_engine=self._expr_engine,
            expr_context=expr_ctx,
        )
        memory_context = await memory_mgr.build_memory_context(input_text)
        if memory_context:
            expr_ctx.memory = memory_context

        tool_registry = AppToolRegistry(self._module_info)
        all_tools = app_def.get_all_tools()
        if not all_tools and agent_config.tools:
            all_tools = agent_config.tools
        resolved_tools = tool_registry.resolve_tools(all_tools)
        resolved_tools = self._auto_include_builtins(resolved_tools, app_def, tool_registry)
        self._apply_tool_constraints(app_def, resolved_tools, expr_ctx)

        # Wire tracing + metrics
        tracing, metrics = self._create_observability(app_def)
        root_span = tracing.start_trace(
            f"stream_history.run:{app_def.app.name}",
            attributes={"input_length": len(input_text), "history_len": len(conversation_history)},
        )
        traced_execute = self._make_traced_execute(tracing, metrics)

        llm = await self._create_llm(agent_config.brain)
        builtins = self._create_builtins(memory_mgr)

        streaming_config = app_def.observability.streaming if app_def.observability else None
        agent = AgentRuntime(
            agent_config=agent_config,
            llm=llm,
            tools=resolved_tools,
            execute_tool=traced_execute or self._execute_tool,
            builtin_executor=builtins,
            expression_engine=self._expr_engine,
            expression_context=expr_ctx,
            streaming_config=streaming_config,
        )

        # Wire cognitive context auto-injection
        self._wire_cognitive_prompt(agent)
        self._wire_context_module(agent, app_def)

        # Inject previous conversation history
        if conversation_history:
            agent.inject_conversation_history(conversation_history)

        try:
            async for event in agent.stream(input_text):
                yield event

            # After completion, emit conversation update for persistence
            messages = agent.get_conversation_messages()
            yield StreamEvent(
                type="conversation_update",
                data={"messages": messages},
            )
            tracing.end_trace(root_span, status="ok")
        except Exception:
            tracing.end_trace(root_span, status="error")
            raise
        finally:
            await llm.close()

    def _apply_capabilities(self, app_def: AppDefinition) -> None:
        """Inject app-level capabilities, perception, and expression engine into executor."""
        if self._execute_tool is None:
            return
        # The execute_tool callback may be a bound method on DaemonToolExecutor
        obj = getattr(self._execute_tool, "__self__", None)
        if obj is None:
            return

        # Inject capabilities (grant/deny/approval/audit)
        if hasattr(obj, "set_capabilities"):
            caps = app_def.capabilities
            if caps and (caps.grant or caps.deny or caps.approval_required or caps.audit):
                obj.set_capabilities(caps)

        # Inject perception config
        if hasattr(obj, "set_perception"):
            if app_def.perception and app_def.perception.enabled:
                obj.set_perception(app_def.perception)

    _LOG_LEVELS = {
        "debug": logging.DEBUG,
        "info": logging.INFO,
        "warning": logging.WARNING,
        "error": logging.ERROR,
        "critical": logging.CRITICAL,
    }

    def _apply_logging_config(self, app_def: AppDefinition) -> None:
        """Apply observability.logging.level to the llmos_bridge.apps logger."""
        log_cfg = app_def.observability.logging
        level_str = log_cfg.level.lower()
        level = self._LOG_LEVELS.get(level_str)
        if level is not None:
            apps_logger = logging.getLogger("llmos_bridge.apps")
            apps_logger.setLevel(level)

    def _apply_security(
        self, app_def: AppDefinition, expr_ctx: ExpressionContext | None = None,
    ) -> None:
        """Apply the security: shorthand block (profile + sandbox) to the executor."""
        if app_def.security is None or self._execute_tool is None:
            return
        obj = getattr(self._execute_tool, "__self__", None)
        if obj is None:
            return

        # Set permission profile on the executor
        if hasattr(obj, "set_security_profile") and app_def.security.profile:
            profile_str = app_def.security.profile.value if hasattr(app_def.security.profile, "value") else str(app_def.security.profile)
            obj.set_security_profile(profile_str)

        # Convert sandbox config into global tool constraints
        sandbox = app_def.security.sandbox
        if sandbox and (sandbox.allowed_paths or sandbox.blocked_commands):
            if hasattr(obj, "set_sandbox"):
                # Resolve template expressions in paths (e.g. {{workspace}})
                paths = list(sandbox.allowed_paths)
                commands = list(sandbox.blocked_commands)
                if expr_ctx is not None:
                    paths = [
                        str(self._expr_engine.resolve(p, expr_ctx))
                        for p in paths
                    ]
                    commands = [
                        str(self._expr_engine.resolve(c, expr_ctx))
                        for c in commands
                    ]
                obj.set_sandbox(
                    allowed_paths=paths,
                    blocked_commands=commands,
                )

    async def _apply_module_config(self, app_def: AppDefinition) -> None:
        """Apply per-module configuration from the YAML module_config block.

        Calls on_config_update() on each module that has configuration specified.
        This allows YAML apps to configure community modules (e.g., API keys,
        search engine preferences, custom endpoints).
        """
        if not app_def.module_config:
            return
        obj = getattr(self._execute_tool, "__self__", None) if self._execute_tool else None
        if obj is None or not hasattr(obj, "_registry"):
            return
        registry = obj._registry
        for module_id, config in app_def.module_config.items():
            try:
                module = registry.get(module_id)
                if module is not None and hasattr(module, "on_config_update"):
                    await module.on_config_update(config)
                    logger.debug("module_config_applied: %s", module_id)
            except Exception as e:
                logger.warning("module_config_failed: %s — %s", module_id, e)

    def _apply_tool_constraints(
        self,
        app_def: AppDefinition,
        resolved_tools: list[ResolvedTool],
        expr_ctx: ExpressionContext | None = None,
    ) -> None:
        """Inject per-tool constraints from YAML into the executor."""
        obj = getattr(self._execute_tool, "__self__", None) if self._execute_tool else None
        if obj is None or not hasattr(obj, "set_tool_constraints"):
            return
        constraints: dict[str, dict[str, Any]] = {}
        for tool in resolved_tools:
            if tool.constraints:
                c = dict(tool.constraints)
                # Resolve template expressions in constraint values
                if expr_ctx is not None:
                    if "paths" in c and isinstance(c["paths"], list):
                        c["paths"] = [
                            str(self._expr_engine.resolve(p, expr_ctx))
                            for p in c["paths"]
                        ]
                constraints[tool.name] = c
        if constraints:
            obj.set_tool_constraints(constraints)

    async def _auto_record_episode(
        self,
        memory_mgr: AppMemoryManager,
        app_def: AppDefinition,
        input_text: str,
        result: AgentRunResult,
    ) -> None:
        """Auto-record an episodic memory entry after a run completes."""
        if not app_def.memory.episodic or not app_def.memory.episodic.auto_record:
            return
        try:
            episode_text = (
                f"Input: {input_text[:500]}\n"
                f"Outcome: {'success' if result.success else 'failure'}\n"
                f"Output: {result.output[:500]}\n"
                f"Turns: {result.total_turns}, Tokens: {result.total_tokens}"
            )
            await memory_mgr.record_episode(
                episode_id=f"{app_def.app.name}-{uuid.uuid4().hex[:8]}",
                text=episode_text,
                metadata={
                    "app_name": app_def.app.name,
                    "success": result.success,
                    "turns": result.total_turns,
                    "tokens": result.total_tokens,
                    "duration_ms": result.duration_ms,
                },
            )
        except Exception:
            logger.debug("Failed to auto-record episode", exc_info=True)

    def _wire_metrics(self, metrics: MetricsCollector) -> None:
        """Wire a MetricsCollector into the daemon executor for per-action tracking."""
        if self._execute_tool is None:
            return
        obj = getattr(self._execute_tool, "__self__", None)
        if obj is not None and hasattr(obj, "set_metrics_collector"):
            obj.set_metrics_collector(metrics)

    def _create_observability(
        self, app_def: AppDefinition
    ) -> tuple[TracingManager, MetricsCollector]:
        """Create TracingManager + MetricsCollector from app observability config."""
        tracing = TracingManager(
            app_def.observability.tracing,
            event_bus=self._event_bus,
            app_name=app_def.app.name,
        )
        metrics = MetricsCollector(
            app_def.observability.metrics,
            event_bus=self._event_bus,
            expr_engine=self._expr_engine,
        )
        self._wire_metrics(metrics)
        return tracing, metrics

    def _make_traced_execute(
        self,
        tracing: TracingManager,
        metrics: MetricsCollector,
        memory_mgr: AppMemoryManager | None = None,
    ) -> Callable[[str, str, dict[str, Any]], Awaitable[dict[str, Any]]] | None:
        """Wrap self._execute_tool with tracing spans, metrics, and procedural memory."""
        original = self._execute_tool
        if original is None:
            return None

        async def _traced(module_id: str, action: str, params: dict[str, Any]) -> dict[str, Any]:
            async with tracing.span(
                f"tool:{module_id}.{action}",
                attributes={"module": module_id, "action": action},
            ) as span:
                start = time.monotonic()
                result = await original(module_id, action, params)
                duration_ms = (time.monotonic() - start) * 1000
                is_error = isinstance(result, dict) and result.get("error") is not None
                if is_error:
                    span.status = "error"
                await metrics.record_action(module_id, action, params, result, duration_ms)

                # Auto-learn procedural memory from tool execution (fire-and-forget,
                # never blocks tool result delivery — SQLite writes happen in background)
                if memory_mgr is not None:
                    async def _learn(
                        _mgr=memory_mgr, _mod=module_id, _act=action,
                        _params=params, _result=result, _err=is_error, _dur=duration_ms,
                    ):
                        try:
                            await _mgr.learn_procedure(
                                procedure_id=f"{_mod}.{_act}.{uuid.uuid4().hex[:8]}",
                                pattern=f"{_mod}.{_act}({', '.join(f'{k}={v!r}' for k, v in list(_params.items())[:3])})",
                                outcome=str(_result.get("error", ""))[:200] if _err else "success",
                                success=not _err,
                                context={"duration_ms": _dur, "module": _mod, "action": _act},
                            )
                        except Exception:
                            logger.debug("Failed to auto-learn procedure", exc_info=True)
                    asyncio.get_event_loop().create_task(_learn())

                return result

        return _traced

    def _wire_cognitive_prompt(self, agent: AgentRuntime) -> None:
        """Wire cognitive context auto-injection into the agent runtime.

        If the memory module has a cognitive backend, this sets a callback
        that returns the cognitive context text. The agent runtime calls this
        before EVERY LLM call, prepending it to the system prompt.

        This gives the LLM real-time awareness of its objectives, active context,
        and recent decisions without ANY explicit memory lookup.
        """
        if self._memory_module is None:
            return
        try:
            fn = getattr(self._memory_module, "get_cognitive_prompt", None)
            if fn and callable(fn):
                agent.set_cognitive_prompt_fn(fn)
        except Exception:
            pass

    @staticmethod
    def _cap_context_to_model(agent_config: Any) -> None:
        """Cap context config values to actual model limits.

        Ensures both ContextConfig.max_tokens (basic ContextManager) and
        model_context_window (ContextManagerModule budget) don't exceed
        the real model's context window.
        """
        from llmos_bridge.apps.providers import get_model_limits

        model_name = agent_config.brain.model
        limits = get_model_limits(model_name)
        if not limits:
            return

        ctx = agent_config.loop.context
        if ctx.max_tokens > limits.context_window:
            logger.info(
                "Capping context.max_tokens %d → %d (model %s)",
                ctx.max_tokens, limits.context_window, model_name,
            )
            ctx.max_tokens = limits.context_window
        if ctx.model_context_window > limits.context_window:
            logger.info(
                "Capping context.model_context_window %d → %d (model %s)",
                ctx.model_context_window, limits.context_window, model_name,
            )
            ctx.model_context_window = limits.context_window
        if ctx.output_reserved > limits.max_output:
            logger.info(
                "Capping context.output_reserved %d → %d (model %s)",
                ctx.output_reserved, limits.max_output, model_name,
            )
            ctx.output_reserved = limits.max_output

    def _wire_context_module(self, agent: AgentRuntime, app_def: AppDefinition) -> None:
        """Wire context manager module into the agent runtime.

        Configures the context_manager with budget settings from the app's
        memory.context config, sets Application identity permissions for
        tool filtering, and wires LLM summarization.
        """
        if self._context_manager_module is None:
            return
        try:
            from llmos_bridge.modules.context_manager.module import ContextBudgetConfig

            loop_ctx = agent._config.loop.context
            # Values already capped by _cap_context_to_model()

            budget_config = ContextBudgetConfig(
                model_context_window=loop_ctx.model_context_window,
                output_reserved=loop_ctx.output_reserved,
                cognitive_max_tokens=loop_ctx.cognitive_max_tokens,
                memory_max_tokens=loop_ctx.memory_max_tokens,
                compression_trigger_ratio=loop_ctx.compression_trigger_ratio,
                summarization_model=loop_ctx.summarization_model,
                min_recent_messages=loop_ctx.min_recent_messages,
            )
            self._context_manager_module.configure(budget_config)

            # Connect Application identity permissions for tool filtering
            if app_def.capabilities and app_def.capabilities.grant:
                allowed_modules: list[str] = []
                for grant in app_def.capabilities.grant:
                    if "." in grant:
                        mod = grant.split(".")[0]
                        if mod not in allowed_modules:
                            allowed_modules.append(mod)
                if allowed_modules:
                    self._context_manager_module.set_application_permissions(
                        allowed_modules=allowed_modules,
                    )

            # Wire LLM summarizer for conversation compression
            if self._llm_factory:
                async def _summarize(text: str, instruction: str) -> str:
                    brain = BrainConfig(
                        provider="anthropic",
                        model=loop_ctx.summarization_model or "claude-haiku-4-5-20251001",
                        max_tokens=1024,
                    )
                    llm = self._llm_factory(brain)
                    try:
                        result = await llm.chat(
                            system=instruction,
                            messages=[{"role": "user", "content": text}],
                            tools=[],
                            max_tokens=1024,
                        )
                        return result.get("text", "")
                    finally:
                        await llm.close()

                self._context_manager_module.set_summarizer(_summarize)

            agent.set_context_module(self._context_manager_module)
        except Exception:
            pass

    def _create_builtins(self, memory_mgr: AppMemoryManager | None = None) -> BuiltinToolExecutor:
        """Create a BuiltinToolExecutor with all dependencies wired."""
        emit_handler = None
        if self._event_bus:
            async def _emit_to_bus(topic: str, data: dict[str, Any]) -> None:
                await self._event_bus.emit(topic, data)
            emit_handler = _emit_to_bus

        builtins = BuiltinToolExecutor(
            input_handler=self._input_handler,
            output_handler=self._output_handler,
            emit_handler=emit_handler,
            kv_store=self._kv_store,
        )
        if memory_mgr is not None:
            builtins.set_memory_manager(memory_mgr)
        return builtins

    @staticmethod
    def _auto_include_builtins(
        resolved_tools: list,
        app_def: AppDefinition,
        tool_registry: AppToolRegistry,
    ) -> list:
        """Auto-include standard builtins that aren't already declared.

        Always includes: todo (task tracking)
        Includes if memory configured: memory (read/write)
        """
        from .models import ToolDefinition

        existing_names = {t.name for t in resolved_tools}

        # Always auto-include todo
        if "todo" not in existing_names:
            td = ToolDefinition(builtin="todo")
            resolved_tools.extend(tool_registry.resolve_tools([td]))

        # Auto-include memory module actions if memory module is available
        has_memory_module = "memory" in (tool_registry._modules or {})
        has_memory_config = (
            app_def.memory.conversation is not None
            or app_def.memory.episodic is not None
            or app_def.memory.project is not None
        )

        if has_memory_module and has_memory_config:
            # Include memory module actions (store, recall, search, set_objective, etc.)
            memory_action_names = {t.name for t in resolved_tools if t.module == "memory"}
            if not memory_action_names:
                td = ToolDefinition(module="memory")
                resolved_tools.extend(tool_registry.resolve_tools([td]))
        elif has_memory_config and "memory" not in existing_names:
            # Fallback: include memory builtin if no memory module available
            td = ToolDefinition(builtin="memory")
            resolved_tools.extend(tool_registry.resolve_tools([td]))

        return resolved_tools

    async def _create_llm(self, brain: BrainConfig, *, pooled: bool = False) -> LLMProvider:
        """Create an LLM provider from brain config, with fallback chain support.

        Args:
            pooled: If True, reuse an existing provider from the pool instead of
                creating a new one.  Pooled providers share the underlying HTTP
                connection pool, avoiding TCP/TLS setup per call.  The caller
                MUST NOT call close() on a pooled provider.
        """
        if self._llm_factory:
            pool_key = f"{brain.provider}:{brain.model}"

            if pooled and pool_key in self._llm_pool:
                provider = self._llm_pool[pool_key]
                if brain.fallback:
                    return _FallbackLLMProvider(
                        provider, brain.fallback, self._llm_factory,
                        primary_provider=brain.provider,
                    )
                return provider

            primary = self._llm_factory(brain)

            if pooled:
                # Evict oldest entry if pool is full
                if len(self._llm_pool) >= self._llm_pool_max:
                    oldest_key = next(iter(self._llm_pool))
                    evicted = self._llm_pool.pop(oldest_key)
                    # Close evicted provider in the background
                    import asyncio
                    asyncio.get_event_loop().create_task(evicted.close())
                self._llm_pool[pool_key] = primary

            if brain.fallback:
                return _FallbackLLMProvider(
                    primary, brain.fallback, self._llm_factory,
                    primary_provider=brain.provider,
                )
            return primary

        # Default: stub provider for testing
        return _StubLLMProvider()


class _StubLLMProvider(LLMProvider):
    """Stub LLM provider for testing (returns empty responses)."""

    async def chat(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> dict[str, Any]:
        return {
            "text": "I'm a stub LLM. Configure a real provider in brain.provider.",
            "tool_calls": [],
            "done": True,
        }

    async def close(self) -> None:
        pass


class _FallbackLLMProvider(LLMProvider):
    """LLM provider with automatic fallback chain.

    Tries the primary provider first; on failure, tries each fallback in order.
    This makes ``brain.fallback:`` in YAML actually work at runtime.
    """

    def __init__(
        self,
        primary: LLMProvider,
        fallbacks: list,  # list[FallbackBrain]
        factory: Any,
        primary_provider: str = "anthropic",
    ):
        self._primary = primary
        self._fallbacks = fallbacks
        self._factory = factory
        self._primary_provider = primary_provider
        self._fallback_providers: list[LLMProvider] = []

    async def chat(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> dict[str, Any]:
        # Try primary
        try:
            return await self._primary.chat(
                system=system, messages=messages, tools=tools, max_tokens=max_tokens,
                **kwargs,
            )
        except Exception as primary_error:
            logger.warning("Primary LLM failed: %s — trying fallbacks", primary_error)

        # Try each fallback
        for fb in self._fallbacks:
            # Inherit provider from primary brain when not explicitly set
            if not fb.provider:
                fb = fb.model_copy(update={"provider": self._primary_provider})
            provider = None
            try:
                provider = self._factory(fb)
                result = await provider.chat(
                    system=system, messages=messages, tools=tools, max_tokens=max_tokens,
                    **kwargs,
                )
                # Keep successful provider for cleanup
                self._fallback_providers.append(provider)
                logger.info("Fallback LLM succeeded: %s/%s", fb.provider, fb.model)
                return result
            except Exception as fb_error:
                logger.warning("Fallback %s/%s failed: %s", fb.provider, fb.model, fb_error)
                # Clean up failed provider immediately
                if provider is not None:
                    try:
                        await provider.close()
                    except Exception:
                        pass
                continue

        raise RuntimeError(
            f"All LLM providers failed (primary + {len(self._fallbacks)} fallbacks)"
        )

    async def close(self) -> None:
        await self._primary.close()
        for p in self._fallback_providers:
            try:
                await p.close()
            except Exception:
                pass
