"""Agent runtime — the LLM reactive loop for LLMOS App Language.

Implements the core agent execution cycle:
1. Build system prompt (with template resolution)
2. Send messages + tools to LLM
3. Execute tool calls via PlanExecutor or builtins
4. Observe results
5. Replan or finish

This is the YAML-declarative replacement for the SDK's ReactivePlanLoop.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Awaitable

from .builtins import BuiltinToolExecutor
from .context_manager import ContextManager, Message, estimate_tokens
from .expression import ExpressionContext, ExpressionEngine
from .models import AgentConfig, BrainConfig, LoopConfig, LoopType, OnToolError
from .tool_registry import AppToolRegistry, ResolvedTool

logger = logging.getLogger(__name__)


# Sentinel for stream completion (never yielded to caller)
_STREAM_DONE = object()

# ─── Data types ──────────────────────────────────────────────────────


@dataclass
class ToolCallRequest:
    """A tool call requested by the LLM."""
    id: str
    name: str                       # "module.action" or builtin name
    arguments: dict[str, Any]


@dataclass
class ToolCallResult:
    """Result of executing a tool call."""
    tool_call_id: str
    name: str
    output: str
    is_error: bool = False


@dataclass
class AgentTurn:
    """One turn in the agent loop."""
    turn_number: int
    text: str | None                # Assistant text response
    tool_calls: list[ToolCallRequest]
    tool_results: list[ToolCallResult]
    timestamp: float = 0.0


@dataclass
class AgentRunResult:
    """Final result of an agent run."""
    success: bool
    output: str
    turns: list[AgentTurn]
    total_turns: int
    total_tokens: int
    duration_ms: float
    stop_reason: str                # "task_complete" | "max_turns" | "error" | "stopped"
    error: str | None = None


@dataclass
class StreamEvent:
    """Event emitted during agent execution for streaming."""
    type: str                       # "thinking" | "tool_call" | "tool_result" | "text" | "error" | "done"
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: float = 0.0


# ─── LLM Provider Protocol ──────────────────────────────────────────

# We define a minimal protocol so the agent runtime doesn't depend on
# the SDK package directly. The AppRuntime wires in the actual provider.


class LLMProvider:
    """Minimal LLM provider interface for the agent runtime.

    This is a protocol — actual implementations come from the SDK
    (AnthropicProvider, OpenAICompatibleProvider, etc.) or mocks for testing.
    """

    async def chat(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int = 4096,
        temperature: float | None = None,
        top_p: float | None = None,
    ) -> dict[str, Any]:
        """Send a chat request to the LLM.

        Returns:
            {
                "text": str | None,
                "tool_calls": [{"id": str, "name": str, "arguments": dict}],
                "done": bool,
            }
        """
        raise NotImplementedError

    async def close(self) -> None:
        """Release resources."""
        pass


# ─── Agent Runtime ───────────────────────────────────────────────────


class AgentRuntime:
    """Executes the agent loop defined by an AgentConfig.

    The runtime manages:
    - LLM conversation (via ContextManager)
    - Tool execution (via execute_tool callback)
    - Built-in tool execution (ask_user, todo, etc.)
    - Loop control (stop conditions, max turns, error handling)
    - Streaming events (for real-time UI)
    """

    def __init__(
        self,
        agent_config: AgentConfig,
        llm: LLMProvider,
        tools: list[ResolvedTool],
        *,
        execute_tool: Callable[[str, str, dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
        builtin_executor: BuiltinToolExecutor | None = None,
        expression_engine: ExpressionEngine | None = None,
        expression_context: ExpressionContext | None = None,
        streaming_config: Any | None = None,
        message_queue: asyncio.Queue | None = None,
        event_callback: Callable[[StreamEvent], Awaitable[None]] | None = None,
        max_actions_per_turn: int = 50,
        max_turns_per_run: int = 0,
    ):
        self._config = agent_config
        self._llm = llm
        self._tools = tools
        self._execute_tool = execute_tool
        self._builtins = builtin_executor or BuiltinToolExecutor()
        self._expr = expression_engine or ExpressionEngine()
        self._expr_ctx = expression_context or ExpressionContext()
        self._streaming = streaming_config  # ObservabilityConfig.streaming
        self._message_queue = message_queue
        self._event_callback = event_callback
        self._max_actions_per_turn = max_actions_per_turn
        self._max_turns_per_run = max_turns_per_run  # 0 = use loop.max_turns only

        self._context_manager = ContextManager(agent_config.loop.context)
        self._turns: list[AgentTurn] = []
        self._stopped = False
        self._stream_queue: asyncio.Queue[StreamEvent] | None = None
        self._conversation_history: list[dict[str, Any]] = []
        self._cognitive_prompt_fn: Callable[[], str] | None = None
        self._context_module: Any = None  # ContextManagerModule (optional)

    def set_cognitive_prompt_fn(self, fn: Callable[[], str]) -> None:
        """Set a callback that returns cognitive context text for auto-injection.

        This text is prepended to the system prompt on EVERY LLM call,
        giving the agent real-time awareness of its objectives, state,
        and recent decisions without any explicit memory lookup.
        """
        self._cognitive_prompt_fn = fn

    def set_context_module(self, module: Any) -> None:
        """Set the ContextManagerModule for budget-aware context management.

        When set, the runtime will:
        - Update the module's state before each LLM call
        - Auto-compress history when the budget is exceeded
        - Bound cognitive text to prevent context overflow
        """
        self._context_module = module

    @property
    def turns(self) -> list[AgentTurn]:
        """All completed turns."""
        return list(self._turns)

    @property
    def turn_count(self) -> int:
        """Number of turns completed."""
        return len(self._turns)

    def stop(self) -> None:
        """Signal the agent to stop after the current turn."""
        self._stopped = True

    def inject_conversation_history(self, messages: list[dict[str, Any]]) -> None:
        """Pre-populate the context with conversation history from a previous session.

        Messages should be dicts with 'role' and 'content' keys (standard chat format).
        This enables cross-turn and cross-session conversation persistence.
        """
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            tool_calls = msg.get("tool_calls")
            tool_call_id = msg.get("tool_call_id", "")
            name = msg.get("name", "")
            if role == "user":
                self._context_manager.add_user_message(content)
            elif role == "assistant":
                self._context_manager.add_assistant_message(content, tool_calls)
            elif role == "tool":
                self._context_manager.add_tool_result(tool_call_id, name, content)

    def get_conversation_messages(self) -> list[dict[str, Any]]:
        """Extract all conversation messages for persistence.

        Returns messages in standard chat format (role/content dicts).
        """
        return [m.to_dict() for m in self._context_manager.messages]

    async def run(self, input_text: str) -> AgentRunResult:
        """Execute the agent loop for a given input.

        This is the main entry point. It:
        1. Resolves the system prompt
        2. Enters the reactive loop
        3. Returns the final result
        """
        start_time = time.monotonic()
        self._stopped = False
        self._turns.clear()

        # Resolve system prompt
        system_prompt = self._resolve_system_prompt()
        self._context_manager.set_system_prompt(system_prompt)

        # Inject on_start context snippets (loop.context.inject_on_start)
        inject_texts = self._config.loop.context.inject_on_start
        if inject_texts:
            for snippet in inject_texts:
                resolved = str(self._expr.resolve(snippet, self._expr_ctx))
                if resolved:
                    self._context_manager.add_user_message(resolved)
                    self._context_manager.add_assistant_message(
                        "Understood, I've noted this context."
                    )

        # Add initial user message
        self._context_manager.add_user_message(input_text)
        await self._emit(StreamEvent(type="text", data={"role": "user", "content": input_text}))

        loop_config = self._config.loop
        max_turns = loop_config.max_turns
        # Enforce app.max_turns_per_run as hard cap over loop.max_turns
        if self._max_turns_per_run > 0:
            max_turns = min(max_turns, self._max_turns_per_run)
        stop_reason = "max_turns"
        error_msg: str | None = None

        # Build tools in OpenAI format for LLM
        tool_defs = self._build_tool_defs()

        try:
            for turn_num in range(1, max_turns + 1):
                if self._stopped:
                    stop_reason = "stopped"
                    break

                # Call LLM
                try:
                    response = await self._call_llm(system_prompt, tool_defs)
                except Exception as e:
                    logger.error("LLM call failed: %s", e)
                    if loop_config.on_llm_error.value == "retry":
                        retry_config = loop_config.retry
                        retried = False
                        for attempt in range(retry_config.max_attempts):
                            await asyncio.sleep(2 ** attempt)
                            try:
                                response = await self._call_llm(system_prompt, tool_defs)
                                retried = True
                                break
                            except Exception:
                                continue
                        if not retried:
                            error_msg = str(e)
                            stop_reason = "error"
                            break
                    else:
                        error_msg = str(e)
                        stop_reason = "error"
                        break

                text = response.get("text")
                tool_calls_raw = response.get("tool_calls", [])
                is_done = response.get("done", False)

                # Parse tool calls (cap at max_actions_per_turn)
                tool_calls = [
                    ToolCallRequest(
                        id=tc.get("id", str(uuid.uuid4())[:8]),
                        name=tc.get("name", ""),
                        arguments=tc.get("arguments", {}),
                    )
                    for tc in tool_calls_raw
                ]
                if self._max_actions_per_turn > 0 and len(tool_calls) > self._max_actions_per_turn:
                    logger.warning(
                        "Capping tool calls from %d to %d (max_actions_per_turn)",
                        len(tool_calls), self._max_actions_per_turn,
                    )
                    tool_calls = tool_calls[:self._max_actions_per_turn]

                # Emit assistant response
                if text:
                    await self._emit(StreamEvent(type="thinking", data={"text": text}))

                # Add assistant message to context
                self._context_manager.add_assistant_message(
                    text or "",
                    tool_calls=[
                        {"id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)}}
                        for tc in tool_calls
                    ] if tool_calls else None,
                )

                # Execute tool calls (concurrently when independent)
                tool_results: list[ToolCallResult] = []
                if tool_calls:
                    # Emit all tool_call events first
                    for tc in tool_calls:
                        await self._emit(StreamEvent(
                            type="tool_call",
                            data={"id": tc.id, "name": tc.name, "arguments": tc.arguments},
                        ))

                    if len(tool_calls) == 1:
                        # Single tool call — execute directly
                        result = await self._execute_tool_call(tool_calls[0])
                        tool_results.append(result)
                    else:
                        # Multiple tool calls — execute concurrently
                        coros = [self._execute_tool_call(tc) for tc in tool_calls]
                        raw_results = await asyncio.gather(*coros, return_exceptions=True)
                        for i, r in enumerate(raw_results):
                            tc = tool_calls[i]
                            if isinstance(r, Exception):
                                tool_results.append(ToolCallResult(
                                    tool_call_id=tc.id,
                                    name=tc.name.replace("__", "."),
                                    output=json.dumps({"error": str(r)}),
                                    is_error=True,
                                ))
                            else:
                                tool_results.append(r)

                    # Emit results and add to context (preserving order)
                    for i, tr in enumerate(tool_results):
                        tc = tool_calls[i]
                        await self._emit(StreamEvent(
                            type="tool_result",
                            data={"id": tc.id, "name": tr.name, "output": tr.output[:500], "is_error": tr.is_error},
                        ))
                        self._context_manager.add_tool_result(
                            tc.id, tr.name, tr.output
                        )

                # Record turn
                turn = AgentTurn(
                    turn_number=turn_num,
                    text=text,
                    tool_calls=tool_calls,
                    tool_results=tool_results,
                    timestamp=time.time(),
                )
                self._turns.append(turn)

                # Update expression context with latest results
                for tr in tool_results:
                    self._expr_ctx.results[tr.name] = {
                        "output": tr.output,
                        "is_error": tr.is_error,
                    }

                # Drain incoming messages (inter-agent communication)
                await self._drain_messages()

                # Check stop conditions
                if is_done or not tool_calls:
                    stop_reason = "task_complete"
                    break

                if self._check_stop_conditions(turn):
                    stop_reason = "task_complete"
                    break

        except Exception as e:
            logger.exception("Agent runtime error")
            error_msg = str(e)
            stop_reason = "error"

        duration_ms = (time.monotonic() - start_time) * 1000

        # Build final output from last assistant text
        output = ""
        for turn in reversed(self._turns):
            if turn.text:
                output = turn.text
                break

        result = AgentRunResult(
            success=stop_reason in ("task_complete", "stopped"),
            output=output,
            turns=self._turns,
            total_turns=len(self._turns),
            total_tokens=self._context_manager.total_tokens,
            duration_ms=duration_ms,
            stop_reason=stop_reason,
            error=error_msg,
        )

        await self._emit(StreamEvent(type="done", data={"stop_reason": stop_reason}))
        return result

    async def stream(self, input_text: str) -> AsyncIterator[StreamEvent]:
        """Run the agent and yield streaming events.

        Uses a sentinel-based approach instead of polling: the run() task
        pushes a _STREAM_DONE sentinel when finished, so the consumer
        never busy-waits.  This gives zero-latency event delivery.
        """
        self._stream_queue = asyncio.Queue()
        task = asyncio.create_task(self._run_and_signal(input_text))

        try:
            while True:
                event = await self._stream_queue.get()
                if event is _STREAM_DONE:
                    break
                yield event

            # Await the task to propagate exceptions
            await task
        finally:
            self._stream_queue = None

    async def _run_and_signal(self, input_text: str) -> AgentRunResult:
        """Run the agent loop and push a sentinel when done."""
        try:
            return await self.run(input_text)
        finally:
            if self._stream_queue is not None:
                await self._stream_queue.put(_STREAM_DONE)

    # ─── Internal methods ────────────────────────────────────────────

    def _resolve_system_prompt(self) -> str:
        """Resolve template variables in the system prompt."""
        raw = self._config.system_prompt
        if not raw:
            return ""
        return str(self._expr.resolve(raw, self._expr_ctx))

    def _build_tool_defs(self) -> list[dict[str, Any]]:
        """Build OpenAI-compatible tool definitions from resolved tools."""
        result = []
        for tool in self._tools:
            properties = {}
            required_fields = []
            for pname, pinfo in tool.parameters.items():
                if isinstance(pinfo, dict):
                    prop: dict[str, Any] = {"type": pinfo.get("type", "string")}
                    if "description" in pinfo:
                        prop["description"] = pinfo["description"]
                    if "enum" in pinfo:
                        prop["enum"] = pinfo["enum"]
                    properties[pname] = prop
                    if pinfo.get("required", False):
                        required_fields.append(pname)
                else:
                    properties[pname] = {"type": "string"}

            result.append({
                "type": "function",
                "function": {
                    "name": tool.name.replace(".", "__"),
                    "description": tool.description or tool.name,
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required_fields,
                    },
                },
            })
        return result

    async def _call_llm(
        self, system: str, tools: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Call the LLM with current context.

        Cognitive context is auto-injected before EVERY call, giving the
        LLM real-time awareness of its objectives and state.

        When a ContextManagerModule is set, the runtime:
        1. Bounds cognitive text to budget (objectives NEVER lost)
        2. Updates the module with current state
        3. Auto-compresses history if budget exceeded
        """
        # Auto-inject cognitive context into system prompt
        effective_system = system
        cognitive_text = ""
        if self._cognitive_prompt_fn:
            try:
                cognitive_text = self._cognitive_prompt_fn()
                if cognitive_text:
                    # Bound cognitive text if context module available
                    if self._context_module is not None:
                        cognitive_text = self._context_module.bound_cognitive_text(cognitive_text)
                    effective_system = cognitive_text + "\n\n" + system
            except Exception as e:
                logger.debug("Cognitive prompt injection failed: %s", e)

        messages = self._context_manager.get_messages_for_llm()
        # Remove system from messages list (passed separately)
        if messages and messages[0].get("role") == "system":
            messages = messages[1:]

        # Pre-flight token budget check: estimate total and warn if too large.
        # This prevents sending requests that will definitely be rejected.
        tools_json = json.dumps(tools) if tools else ""
        system_tokens = estimate_tokens(effective_system)
        tools_tokens = estimate_tokens(tools_json)
        messages_tokens = sum(
            estimate_tokens(json.dumps(m) if isinstance(m, dict) else str(m))
            for m in messages
        )
        total_estimated = system_tokens + tools_tokens + messages_tokens
        model_limit = self._config.loop.context.model_context_window
        if total_estimated > model_limit * 0.95:
            logger.warning(
                "Pre-flight: estimated %d tokens exceeds 95%% of model limit %d "
                "(system=%d, tools=%d, messages=%d). Trimming messages.",
                total_estimated, model_limit, system_tokens, tools_tokens, messages_tokens,
            )
            # Aggressive trim: keep only recent messages to fit budget
            available_for_messages = int(model_limit * 0.7) - system_tokens - tools_tokens
            if available_for_messages < 0:
                available_for_messages = int(model_limit * 0.3)
            trimmed = []
            running = 0
            for m in reversed(messages):
                m_tokens = estimate_tokens(json.dumps(m) if isinstance(m, dict) else str(m))
                if running + m_tokens > available_for_messages:
                    break
                trimmed.insert(0, m)
                running += m_tokens
            if len(trimmed) < len(messages):
                logger.info(
                    "Trimmed messages: %d → %d (saved ~%d tokens)",
                    len(messages), len(trimmed), messages_tokens - running,
                )
                messages = trimmed

        # Update context module state and auto-compress if needed
        if self._context_module is not None:
            try:
                self._context_module.update_state(
                    system_prompt=system,
                    tools_json=tools_json,
                    cognitive_text=cognitive_text,
                    messages=messages,
                )
                budget = self._context_module.compute_budget()
                if budget.compression_needed and len(messages) > self._context_module._config.min_recent_messages:
                    new_msgs, _summary = await self._context_module.compress_messages(messages)
                    # Replace messages in context manager
                    self._context_manager.clear()
                    for msg in new_msgs:
                        self._context_manager.add_message(Message(
                            role=msg.get("role", "user"),
                            content=msg.get("content", ""),
                            tool_calls=msg.get("tool_calls", []),
                            tool_call_id=msg.get("tool_call_id", ""),
                            name=msg.get("name", ""),
                        ))
                    messages = self._context_manager.get_messages_for_llm()
                    if messages and messages[0].get("role") == "system":
                        messages = messages[1:]
                    logger.info("Auto-compressed context: %d tokens saved", budget.history_used - self._context_module._current_history_tokens)
            except Exception as e:
                logger.debug("Context budget management failed: %s", e)

        timeout = self._config.brain.timeout
        kwargs: dict[str, Any] = {}
        if self._config.brain.temperature is not None:
            kwargs["temperature"] = self._config.brain.temperature
        if self._config.brain.top_p is not None:
            kwargs["top_p"] = self._config.brain.top_p

        # Safety net: filter params against provider capabilities so
        # misconfigurations produce a warning instead of an API crash.
        if kwargs:
            from .providers import filter_params_for_provider
            kwargs = filter_params_for_provider(self._config.brain.provider, kwargs)

        coro = self._llm.chat(
            system=effective_system,
            messages=messages,
            tools=tools,
            max_tokens=self._config.brain.max_tokens,
            **kwargs,
        )
        if timeout and timeout > 0:
            return await asyncio.wait_for(coro, timeout=timeout)
        return await coro

    async def _execute_tool_call(self, tc: ToolCallRequest) -> ToolCallResult:
        """Execute a single tool call (builtin or module action)."""
        # Restore original name (replace __ back to .)
        original_name = tc.name.replace("__", ".")

        try:
            # Check if it's a builtin
            bare_name = original_name.split(".")[-1] if "." not in original_name else ""
            if not bare_name:
                bare_name = original_name

            if self._builtins.is_builtin(bare_name):
                result = await self._builtins.execute(bare_name, tc.arguments)
                return ToolCallResult(
                    tool_call_id=tc.id,
                    name=original_name,
                    output=json.dumps(result, default=str),
                )

            # Module action — split "module.action"
            parts = original_name.split(".", 1)
            if len(parts) != 2:
                return ToolCallResult(
                    tool_call_id=tc.id,
                    name=original_name,
                    output=json.dumps({"error": f"Invalid tool name: {original_name}"}),
                    is_error=True,
                )

            module_id, action_name = parts

            if self._execute_tool:
                result = await self._execute_tool(module_id, action_name, tc.arguments)
                output = json.dumps(result, default=str)
                is_error = isinstance(result, dict) and result.get("error") is not None
                return ToolCallResult(
                    tool_call_id=tc.id,
                    name=original_name,
                    output=output,
                    is_error=is_error,
                )
            else:
                return ToolCallResult(
                    tool_call_id=tc.id,
                    name=original_name,
                    output=json.dumps({"error": "No tool executor configured"}),
                    is_error=True,
                )

        except Exception as e:
            logger.error("Tool execution error for %s: %s", original_name, e)
            error_output = json.dumps({"error": str(e)})

            if self._config.loop.on_tool_error == OnToolError.show_to_agent:
                return ToolCallResult(
                    tool_call_id=tc.id,
                    name=original_name,
                    output=error_output,
                    is_error=True,
                )
            elif self._config.loop.on_tool_error == OnToolError.skip:
                return ToolCallResult(
                    tool_call_id=tc.id,
                    name=original_name,
                    output=json.dumps({"skipped": True, "reason": str(e)}),
                    is_error=False,
                )
            else:
                raise

    def _check_stop_conditions(self, turn: AgentTurn) -> bool:
        """Check if any stop condition is met."""
        for condition in self._config.loop.stop_conditions:
            # Extend the main expression context with agent-specific fields
            # so stop conditions can access variables, memory, trigger, etc.
            saved_extra = self._expr_ctx.extra
            saved_agent = self._expr_ctx.agent
            self._expr_ctx.extra = {
                **saved_extra,
                "turns": self.turn_count,
                "max_turns": self._config.loop.max_turns,
            }
            self._expr_ctx.agent = {
                "no_tool_calls": len(turn.tool_calls) == 0,
                "says_done": turn.text and any(
                    phrase in (turn.text or "").lower()
                    for phrase in ["task complete", "task is complete", "i'm done", "all done"]
                ),
                "last_turn": turn,
            }
            try:
                if self._expr.evaluate_condition(condition, self._expr_ctx):
                    return True
            except Exception:
                pass  # Don't crash on expression errors
            finally:
                self._expr_ctx.extra = saved_extra
                self._expr_ctx.agent = saved_agent
        return False

    async def _drain_messages(self) -> None:
        """Drain messages from the inter-agent message queue and inject as user messages."""
        if self._message_queue is None:
            return
        injected = 0
        while not self._message_queue.empty():
            try:
                msg = self._message_queue.get_nowait()
                content = msg.get("content", "") if isinstance(msg, dict) else str(msg)
                sender = msg.get("from", "system") if isinstance(msg, dict) else "system"
                injected_text = f"[Message from {sender}]: {content}"
                self._context_manager.add_user_message(injected_text)
                await self._emit(StreamEvent(
                    type="text",
                    data={"role": "injected_message", "from": sender, "content": content},
                ))
                injected += 1
            except asyncio.QueueEmpty:
                break
        if injected:
            logger.debug("Injected %d messages into agent context", injected)

    async def _emit(self, event: StreamEvent) -> None:
        """Emit a streaming event, filtered by observability.streaming config."""
        # Filter events based on streaming config
        if self._streaming is not None:
            if not self._streaming.enabled:
                return
            if event.type == "thinking" and not self._streaming.include_thoughts:
                return
            if event.type == "tool_call" and not self._streaming.include_tool_calls:
                return
            if event.type == "tool_result" and not self._streaming.include_results:
                return

        event.timestamp = time.time()
        if self._stream_queue is not None:
            await self._stream_queue.put(event)
        if self._event_callback is not None:
            try:
                await self._event_callback(event)
            except Exception:
                pass
