"""Computer Use Agent — autonomous GUI control via any LLM provider.

Uses the **Reactive Plan Loop** (Plan → Execute → Observe → Re-plan) for
efficient multi-step task execution with error recovery and safeguards.

Supports Anthropic Claude, OpenAI GPT-4o, Ollama (local), Mistral,
and any OpenAI-compatible API.  The provider abstraction handles
tool schemas, message formats, and multimodal encoding automatically.

Usage::

    from langchain_llmos import ComputerUseAgent

    # Anthropic (default)
    agent = ComputerUseAgent(provider="anthropic")

    # OpenAI
    agent = ComputerUseAgent(provider="openai", api_key="sk-...")

    # Ollama (local, free)
    agent = ComputerUseAgent(provider="ollama", model="llama3.2")

    # Legacy (backward compatible)
    agent = ComputerUseAgent(anthropic_api_key="sk-ant-...")

    result = await agent.run("Open the file manager")
    print(result.output)

Prerequisites:
    - LLMOS Bridge daemon running (``llmos-bridge serve``)
    - Provider SDK installed (``pip install langchain-llmos[anthropic]``
      or ``pip install langchain-llmos[openai]``)
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

from langchain_llmos.client import AsyncLLMOSClient
from langchain_llmos.providers.base import (
    AgentLLMProvider,
    LLMTurn,
    ToolCall,
    ToolDefinition,
    ToolResult,
)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class StepRecord:
    """Records a single agent step (one tool call + result)."""

    tool_name: str
    tool_input: dict[str, Any]
    tool_output: dict[str, Any] | str
    duration_ms: float = 0.0


@dataclass
class AgentResult:
    """Final result of a ``ComputerUseAgent.run()`` invocation."""

    success: bool
    output: str
    steps: list[StepRecord] = field(default_factory=list)
    total_duration_ms: float = 0.0


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

# Modules that the Computer Use Agent should expose by default.
_DEFAULT_MODULES = ["computer_control", "gui", "os_exec", "filesystem", "window_tracker"]

# Maximum characters for a tool result text block sent to the LLM.
_MAX_TOOL_RESULT_CHARS = 12_000

# Maximum number of screenshots to keep in message history.
# Older screenshots are stripped to avoid token explosion.
_MAX_SCREENSHOTS_IN_HISTORY = 2

# Maximum screenshot dimension (longest side) before resize.
_MAX_SCREENSHOT_DIM = 1024

# Maximum number of UI elements to include in tool results.
_MAX_ELEMENTS_IN_RESULT = 50


# Approval callback type: (plan_id, action_data) → decision dict.
ApprovalCallback = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


def _resize_screenshot_b64(b64: str, max_dim: int = _MAX_SCREENSHOT_DIM) -> str:
    """Resize a base64 PNG screenshot so longest side ≤ *max_dim*.

    Returns a JPEG base64 string (much smaller than PNG for photos).
    """
    import base64 as b64mod
    import io

    try:
        from PIL import Image
    except ImportError:
        return b64  # Can't resize without Pillow.

    raw = b64mod.b64decode(b64)
    img = Image.open(io.BytesIO(raw))
    w, h = img.size

    if max(w, h) <= max_dim:
        # Already small — still convert to JPEG for size savings.
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=70)
        return b64mod.b64encode(buf.getvalue()).decode("ascii")

    ratio = max_dim / max(w, h)
    new_w, new_h = int(w * ratio), int(h * ratio)
    img = img.resize((new_w, new_h), Image.LANCZOS)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=70)
    return b64mod.b64encode(buf.getvalue()).decode("ascii")


def _strip_old_screenshots(
    messages: list[dict[str, Any]],
    keep_last: int = _MAX_SCREENSHOTS_IN_HISTORY,
) -> list[dict[str, Any]]:
    """Remove image blocks from older messages, keeping only the last *keep_last*.

    This prevents token explosion from accumulating full screenshots
    in every subsequent API call.
    """
    # Collect indices of messages that contain image blocks.
    img_msg_indices: list[tuple[int, int]] = []  # (msg_idx, content_block_idx)
    for i, msg in enumerate(messages):
        content = msg.get("content")
        if isinstance(content, list):
            for j, block in enumerate(content):
                if isinstance(block, dict):
                    # Anthropic tool_result with image content
                    inner = block.get("content")
                    if isinstance(inner, list):
                        for k, inner_block in enumerate(inner):
                            if isinstance(inner_block, dict) and inner_block.get("type") == "image":
                                img_msg_indices.append((i, j, k))
                    elif block.get("type") == "image":
                        img_msg_indices.append((i, j, -1))

    if len(img_msg_indices) <= keep_last:
        return messages  # Nothing to strip.

    # Strip all but the last `keep_last` images.
    to_strip = img_msg_indices[:-keep_last]
    for entry in to_strip:
        if len(entry) == 3:
            msg_idx, block_idx, inner_idx = entry
            content = messages[msg_idx]["content"]
            if inner_idx >= 0 and isinstance(content[block_idx].get("content"), list):
                # Replace image block with a text placeholder inside tool_result.
                content[block_idx]["content"][inner_idx] = {
                    "type": "text",
                    "text": "[Previous screenshot removed to save tokens]",
                }
            elif content[block_idx].get("type") == "image":
                content[block_idx] = {
                    "type": "text",
                    "text": "[Previous screenshot removed to save tokens]",
                }

    return messages


def _compact_elements(result: dict[str, Any], max_elems: int = _MAX_ELEMENTS_IN_RESULT) -> dict[str, Any]:
    """Reduce the elements list to the most useful entries for the LLM.

    Keeps text elements and interactable icons, drops low-value entries.
    """
    elements = result.get("elements")
    if not elements or not isinstance(elements, list):
        return result

    total = len(elements)
    if total <= max_elems:
        return result

    result = dict(result)

    # Prioritize: interactable elements first, then text elements.
    interactable = [e for e in elements if e.get("interactable")]
    text_only = [e for e in elements if not e.get("interactable") and e.get("text")]
    others = [e for e in elements if e not in interactable and e not in text_only]

    # Take interactable first, then text, then others — up to max_elems.
    kept = interactable[:max_elems]
    remaining = max_elems - len(kept)
    if remaining > 0:
        kept.extend(text_only[:remaining])
        remaining = max_elems - len(kept)
    if remaining > 0:
        kept.extend(others[:remaining])

    result["elements"] = kept
    result["_elements_truncated"] = total - len(kept)
    result["_elements_total"] = total
    return result


class ComputerUseAgent:
    """Autonomous agent that controls the computer via LLMOS Bridge + any LLM.

    The agent implements the perceive→act→verify loop:
    1. The LLM sees the screen (via ``read_screen`` with annotated screenshot)
    2. The LLM decides the next action
    3. The agent executes the action via the LLMOS daemon
    4. The LLM sees the result + new screen state
    5. Repeat until task is done or ``max_steps`` reached

    Supports multiple LLM providers via the ``provider`` parameter.

    Args:
        provider:           Provider name (``"anthropic"``, ``"openai"``,
                            ``"ollama"``, ``"mistral"``) or a pre-built
                            :class:`AgentLLMProvider` instance.
        api_key:            API key for the provider.
        model:              Model name (provider-specific default if omitted).
        base_url:           Override base URL for OpenAI-compatible providers.
        supports_vision:    Override vision support detection.
        daemon_url:         LLMOS Bridge daemon URL.
        daemon_api_token:   Optional daemon API token.
        max_tokens:         Max tokens per LLM response.
        system_prompt:      Custom system prompt (auto-fetched if None).
        allowed_modules:    Module IDs to expose.
        max_steps:          Maximum tool-call iterations before stopping.
        verbose:            Print step-by-step progress to stdout.
        approval_mode:      ``"auto"`` (default, auto-approve all),
                            ``"always_reject"`` (reject all), or
                            ``"callback"`` (use ``approval_callback``).
        approval_callback:  Async function called for approval decisions
                            when ``approval_mode="callback"``.
        anthropic_api_key:  **Legacy** — equivalent to
                            ``provider="anthropic", api_key=<key>``.
    """

    def __init__(
        self,
        provider: AgentLLMProvider | str | None = None,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        supports_vision: bool | None = None,
        daemon_url: str = "http://127.0.0.1:40000",
        daemon_api_token: str | None = None,
        max_tokens: int = 4096,
        system_prompt: str | None = None,
        allowed_modules: list[str] | None = None,
        max_steps: int = 30,
        verbose: bool = False,
        approval_mode: str = "auto",
        approval_callback: ApprovalCallback | None = None,
        # Legacy backward compatibility.
        anthropic_api_key: str | None = None,
    ) -> None:
        # Resolve provider.
        if provider is not None and not isinstance(provider, str):
            # Pre-built provider instance (AgentLLMProvider or mock).
            self._provider = provider
        else:
            self._provider = self._resolve_provider(
                provider_name=provider,
                api_key=api_key,
                model=model,
                base_url=base_url,
                supports_vision=supports_vision,
                anthropic_api_key=anthropic_api_key,
            )

        self._daemon = AsyncLLMOSClient(
            base_url=daemon_url, api_token=daemon_api_token, timeout=300.0
        )
        self._max_tokens = max_tokens
        self._system_prompt = system_prompt
        self._allowed_modules = allowed_modules or _DEFAULT_MODULES
        self._max_steps = max_steps
        self._verbose = verbose
        self._approval_mode = approval_mode
        self._approval_callback = approval_callback

    # ------------------------------------------------------------------
    # Provider resolution
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_provider(
        provider_name: str | None,
        api_key: str | None,
        model: str | None,
        base_url: str | None,
        supports_vision: bool | None,
        anthropic_api_key: str | None,
    ) -> AgentLLMProvider:
        """Resolve the provider from arguments."""
        from langchain_llmos.providers import build_agent_provider

        # Legacy: anthropic_api_key takes precedence when no provider given.
        if provider_name is None and anthropic_api_key is not None:
            return build_agent_provider(
                "anthropic",
                api_key=anthropic_api_key,
                model=model,
                vision=supports_vision,
            )

        # Default to anthropic if nothing specified.
        name = provider_name or "anthropic"
        return build_agent_provider(
            name,
            api_key=api_key,
            model=model,
            base_url=base_url,
            vision=supports_vision,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(
        self,
        task: str,
        max_steps: int | None = None,
        *,
        use_reactive_loop: bool = True,
    ) -> AgentResult:
        """Run an autonomous task.

        Uses the **Reactive Plan Loop** by default: the LLM generates
        multi-step plans, which are executed as batches via the daemon's
        DAG scheduler.  On failure the LLM re-plans with error context.

        Args:
            task:      Natural language task description.
            max_steps: Override the default max_steps for this run.
            use_reactive_loop:  Use the Plan→Execute→Observe→Re-plan
                        loop (default True).  Set to False for the legacy
                        1-action-at-a-time behavior.

        Returns:
            AgentResult with success status, output text, and step log.
        """
        # 1. Fetch system prompt if not provided.
        system = self._system_prompt
        if system is None:
            system = await self._daemon.get_system_prompt()

        # 2. Build provider-agnostic tool definitions.
        tool_defs = await self._build_tool_definitions()

        # 3. Run the agent loop.
        if use_reactive_loop:
            from langchain_llmos.loop import ReactivePlanLoop
            from langchain_llmos.safeguards import SafeguardConfig

            loop = ReactivePlanLoop(
                provider=self._provider,
                daemon=self._daemon,
                max_replans=8,
                max_steps_per_plan=8,
                max_total_actions=max_steps or self._max_steps,
                max_tokens=self._max_tokens,
                verbose=self._verbose,
                safeguards=SafeguardConfig(),
                approval_mode=self._approval_mode,
            )
            return await loop.run(task, system, tool_defs)

        # Legacy fallback: 1-action-at-a-time loop.
        return await self._run_legacy_loop(
            task, system, tool_defs, max_steps or self._max_steps
        )

    async def _run_legacy_loop(
        self,
        task: str,
        system: str,
        tool_defs: list[ToolDefinition],
        steps_limit: int,
    ) -> AgentResult:
        """Legacy agent loop: 1 action per LLM turn."""
        t0 = time.monotonic()
        messages: list[dict[str, Any]] = self._provider.build_user_message(task)
        steps: list[StepRecord] = []

        for step_idx in range(steps_limit):
            if self._verbose:
                print(f"\n--- Step {step_idx + 1}/{steps_limit} ---")

            _strip_old_screenshots(messages, keep_last=_MAX_SCREENSHOTS_IN_HISTORY)

            for attempt in range(4):
                try:
                    turn = await self._provider.create_message(
                        system=system,
                        messages=messages,
                        tools=tool_defs,
                        max_tokens=self._max_tokens,
                    )
                    break
                except Exception as exc:
                    if "rate_limit" in str(exc).lower() or "429" in str(exc):
                        wait = 15 * (attempt + 1)
                        if self._verbose:
                            print(f"  Rate limited, waiting {wait}s...")
                        await asyncio.sleep(wait)
                    else:
                        raise

            if turn.is_done:
                output = turn.text or ""
                if self._verbose:
                    print(f"Agent finished: {output[:200]}")
                return AgentResult(
                    success=True,
                    output=output,
                    steps=steps,
                    total_duration_ms=(time.monotonic() - t0) * 1000,
                )

            if not turn.tool_calls:
                return AgentResult(
                    success=True,
                    output=turn.text or "",
                    steps=steps,
                    total_duration_ms=(time.monotonic() - t0) * 1000,
                )

            messages.append(self._provider.build_assistant_message(turn))

            tool_results: list[ToolResult] = []
            for tc in turn.tool_calls:
                if self._verbose:
                    print(f"  Tool: {tc.name}({json.dumps(tc.arguments, default=str)[:200]})")

                step_t0 = time.monotonic()
                result = await self._execute_tool_with_approval(tc.name, tc.arguments)
                step_ms = (time.monotonic() - step_t0) * 1000

                steps.append(StepRecord(
                    tool_name=tc.name,
                    tool_input=tc.arguments,
                    tool_output=result,
                    duration_ms=step_ms,
                ))

                if self._verbose:
                    preview = json.dumps(result, default=str)[:300]
                    print(f"  Result ({step_ms:.0f}ms): {preview}")

                tool_results.append(self._make_tool_result(tc.id, result))

            messages.extend(self._provider.build_tool_results_message(tool_results))

        return AgentResult(
            success=False,
            output=f"Task not completed within {steps_limit} steps.",
            steps=steps,
            total_duration_ms=(time.monotonic() - t0) * 1000,
        )

    async def close(self) -> None:
        """Close HTTP clients and provider resources."""
        await self._provider.close()
        await self._daemon.close()

    async def __aenter__(self) -> "ComputerUseAgent":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # Tool schema generation (provider-agnostic)
    # ------------------------------------------------------------------

    async def _build_tool_definitions(self) -> list[ToolDefinition]:
        """Fetch module manifests and convert to provider-agnostic tools."""
        modules = await self._daemon.list_modules()
        tools: list[ToolDefinition] = []

        for mod in modules:
            mod_id = mod.get("module_id", "")
            if not mod.get("available") or mod_id not in self._allowed_modules:
                continue

            try:
                manifest = await self._daemon.get_module_manifest(mod_id)
            except Exception:
                continue

            for action in manifest.get("actions", []):
                tool_name = f"{mod_id}__{action['name']}"
                schema = action.get(
                    "params_schema", {"type": "object", "properties": {}}
                )
                tools.append(ToolDefinition(
                    name=tool_name,
                    description=f"[{mod_id}] {action.get('description', '')}",
                    parameters_schema=schema,
                ))

        return tools

    # ------------------------------------------------------------------
    # Tool execution with approval support
    # ------------------------------------------------------------------

    async def _execute_tool_with_approval(
        self, tool_name: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Execute a tool call with approval handling.

        Uses ``async_execution=True`` and polls the plan status so that
        approval requests can be handled without deadlocking.
        """
        parts = tool_name.split("__", 1)
        if len(parts) != 2:
            return {"error": f"Invalid tool name format: {tool_name}"}

        module_id, action_name = parts

        # For read_screen, request screenshots if provider supports vision.
        if action_name == "read_screen":
            params = {**params, "include_screenshot": self._provider.supports_vision}

        clean_params = {k: v for k, v in params.items() if v is not None}

        plan = {
            "plan_id": str(uuid.uuid4()),
            "protocol_version": "2.0",
            "description": f"ComputerUseAgent: {module_id}.{action_name}",
            "actions": [
                {
                    "id": "action",
                    "action": action_name,
                    "module": module_id,
                    "params": clean_params,
                }
            ],
        }

        try:
            result = await self._daemon.submit_plan(plan, async_execution=True)
        except Exception as exc:
            return {"error": f"Daemon submission failed: {exc}"}

        plan_id = result.get("plan_id", plan["plan_id"])

        try:
            return await self._poll_plan(plan_id)
        except Exception as exc:
            return {"error": f"Daemon execution failed: {exc}"}

    async def _poll_plan(
        self, plan_id: str, max_wait: float = 300.0
    ) -> dict[str, Any]:
        """Poll plan status until terminal, handling approval requests."""
        start = time.monotonic()

        while (time.monotonic() - start) < max_wait:
            plan_state = await self._daemon.get_plan(plan_id)
            plan_status = plan_state.get("status", "")

            if plan_status in ("completed", "failed", "cancelled"):
                return self._extract_action_result(plan_state)

            # Check for pending approvals.
            actions = plan_state.get("actions", [])
            for action_data in actions:
                if action_data.get("status") == "awaiting_approval":
                    await self._handle_approval(plan_id, action_data)
                    break  # Re-poll after handling.

            await asyncio.sleep(0.5)

        return {"error": f"Plan execution timed out after {max_wait}s"}

    async def _handle_approval(
        self, plan_id: str, action_data: dict[str, Any]
    ) -> None:
        """Handle a pending approval request."""
        action_id = action_data.get("action_id", action_data.get("id", "action"))

        if self._approval_mode in ("auto", "always_approve"):
            decision = "approve"
            reason = "Auto-approved by ComputerUseAgent"
        elif self._approval_mode == "always_reject":
            decision = "reject"
            reason = "Rejected by ComputerUseAgent policy"
        elif self._approval_mode == "callback" and self._approval_callback:
            cb_result = await self._approval_callback(plan_id, action_data)
            decision = cb_result.get("decision", "approve")
            reason = cb_result.get("reason")
        else:
            decision = "approve"
            reason = "Auto-approved (no callback configured)"

        if self._verbose:
            mod = action_data.get("module", "?")
            act = action_data.get("action", "?")
            print(f"  Approval: {decision} for {mod}.{act}")

        await self._daemon.approve_action(
            plan_id=plan_id,
            action_id=action_id,
            decision=decision,
            reason=reason,
            approved_by="computer_use_agent",
        )

    # ------------------------------------------------------------------
    # Tool result formatting
    # ------------------------------------------------------------------

    def _make_tool_result(
        self, tool_call_id: str, result: dict[str, Any]
    ) -> ToolResult:
        """Build a provider-agnostic ToolResult from raw daemon output."""
        if isinstance(result, dict):
            result = dict(result)  # shallow copy
        else:
            result = {"output": result}

        screenshot_b64 = result.pop("screenshot_b64", None)

        # Drop screenshot if provider doesn't support vision.
        if not self._provider.supports_vision:
            screenshot_b64 = None

        # Resize screenshot to save tokens (1920x1080 → 1024px max).
        if screenshot_b64:
            screenshot_b64 = _resize_screenshot_b64(screenshot_b64)

        # Compact element list to avoid sending 280+ elements as JSON.
        result = _compact_elements(result)

        text = json.dumps(result, default=str)
        if len(text) > _MAX_TOOL_RESULT_CHARS:
            text = text[:_MAX_TOOL_RESULT_CHARS] + "\n... [TRUNCATED]"

        return ToolResult(
            tool_call_id=tool_call_id,
            text=text,
            image_b64=screenshot_b64,
            image_media_type="image/jpeg" if screenshot_b64 else "image/png",
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_action_result(plan_result: dict[str, Any]) -> dict[str, Any]:
        """Extract the action result dict from a plan execution response."""
        actions = plan_result.get("actions", [])
        if actions and len(actions) == 1:
            action = actions[0]
            if action.get("result") is not None:
                return action["result"]
            if action.get("error"):
                return {"error": str(action["error"])}
        # Fallback: return the whole plan result.
        return plan_result

    # ------------------------------------------------------------------
    # Legacy compat (kept for existing tests)
    # ------------------------------------------------------------------

    async def _build_tools(self) -> list[dict[str, Any]]:
        """Legacy: build Anthropic-format tools. Use _build_tool_definitions()."""
        tool_defs = await self._build_tool_definitions()
        return self._provider.format_tool_definitions(tool_defs)

    async def _execute_tool(
        self, tool_name: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Legacy: execute tool without approval polling."""
        return await self._execute_tool_with_approval(tool_name, params)

    def _format_tool_result(
        self, result: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Legacy: format result as Anthropic content blocks."""
        content: list[dict[str, Any]] = []

        screenshot_b64 = None
        if isinstance(result, dict):
            result = dict(result)
            screenshot_b64 = result.pop("screenshot_b64", None)

        if screenshot_b64:
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": screenshot_b64,
                },
            })

        text = json.dumps(result, default=str)
        if len(text) > _MAX_TOOL_RESULT_CHARS:
            text = text[:_MAX_TOOL_RESULT_CHARS] + "\n... [TRUNCATED]"
        content.append({"type": "text", "text": text})

        return content
