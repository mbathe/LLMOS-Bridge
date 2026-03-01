"""Reactive Plan Loop — Plan → Execute → Observe → Re-plan.

The core agent loop that replaces the slow 1-action-at-a-time approach
with batch plan execution and intelligent re-planning on failure.

Usage::

    loop = ReactivePlanLoop(
        provider=anthropic_provider,
        daemon=async_client,
        verbose=True,
    )
    result = await loop.run(task, system_prompt, tool_defs)

The loop works in 4 phases:
  1. **Plan**:    LLM generates a multi-step plan (JSON array of actions)
  2. **Execute**: Submit as a single IML plan to the daemon (DAG execution)
  3. **Observe**: Check results — all passed? task complete?
  4. **Re-plan**: If failures, LLM generates a corrective plan
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from langchain_llmos.client import AsyncLLMOSClient
from langchain_llmos.providers.base import (
    AgentLLMProvider,
    LLMTurn,
    ToolCall,
    ToolDefinition,
    ToolResult,
)
from langchain_llmos.safeguards import SafeguardConfig


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
    """Final result of an agent run."""

    success: bool
    output: str
    steps: list[StepRecord] = field(default_factory=list)
    total_duration_ms: float = 0.0


@dataclass
class PlanStep:
    """A single step in a multi-action plan."""

    id: str
    action: str  # "module__action_name"
    params: dict[str, Any] = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)
    description: str = ""


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Maximum characters for a tool result text block sent to the LLM.
_MAX_TOOL_RESULT_CHARS = 12_000

# Maximum screenshot dimension (longest side) before resize.
_MAX_SCREENSHOT_DIM = 1024

# Maximum screenshots kept in message history.
_MAX_SCREENSHOTS_IN_HISTORY = 2

# Maximum elements in a tool result.
_MAX_ELEMENTS_IN_RESULT = 50

# Planning prompt injected into the system prompt.
_PLANNING_INSTRUCTION = """
## Reactive Planning Mode

You operate in a Plan → Execute → Observe → Re-plan loop.

When given a task, you MUST respond in ONE of two ways:

### Option A: Multi-step plan (preferred for complex tasks)
Respond with a JSON plan wrapped in ```json fences:

```json
[
  {"id": "s1", "action": "computer_control__read_screen", "params": {"include_screenshot": true}, "description": "See current screen state"},
  {"id": "s2", "action": "gui__key_press", "params": {"keys": ["ctrl", "alt", "t"]}, "depends_on": ["s1"], "description": "Open terminal"},
  {"id": "s3", "action": "computer_control__wait_for_element", "params": {"target_description": "terminal window", "timeout": 5}, "depends_on": ["s2"], "description": "Wait for terminal to appear"}
]
```

Rules for plans:
- Use depends_on to express ordering. Independent actions can run in parallel.
- Keep plans SHORT: 3-8 steps max. You will get more chances to plan.
- The first step should usually be read_screen to see the current state.
- After execution, you will see which steps succeeded or failed. You can then re-plan.

### Option B: Single tool call (for simple tasks)
Use a regular tool call when only 1 action is needed.

### Option C: Task complete
When the task is done, respond with text only (no tool calls, no JSON plan).
Summarize what was accomplished.

### Window Tracking
At task start, use `window_tracker__start_tracking` to track the target application window. If the context switches (user opens another window), the system will detect it. Use `window_tracker__recover_focus` to get back to the target window.

### Screen Understanding
When you read the screen, you may receive a hierarchical scene graph showing the screen layout (toolbar, sidebar, content area, forms, etc.). Use this structure to precisely identify elements — e.g. "the Submit button inside the login form".

### Error Recovery
If a previous plan partially failed, you will see the results. Analyze what went wrong and create a corrective plan. Do NOT repeat the failed action with the same params — try a different approach.
"""


# ---------------------------------------------------------------------------
# Helpers (screenshot management — shared with agent.py)
# ---------------------------------------------------------------------------


def _resize_screenshot_b64(b64: str, max_dim: int = _MAX_SCREENSHOT_DIM) -> str:
    """Resize a base64 screenshot so longest side <= *max_dim*, convert to JPEG."""
    import base64 as b64mod
    import io

    try:
        from PIL import Image
    except ImportError:
        return b64

    raw = b64mod.b64decode(b64)
    img = Image.open(io.BytesIO(raw))
    w, h = img.size

    if max(w, h) > max_dim:
        ratio = max_dim / max(w, h)
        img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=70)
    return b64mod.b64encode(buf.getvalue()).decode("ascii")


def _strip_old_screenshots(
    messages: list[dict[str, Any]],
    keep_last: int = _MAX_SCREENSHOTS_IN_HISTORY,
) -> None:
    """Remove image blocks from older messages, keeping only *keep_last*."""
    img_locations: list[tuple[int, int, int]] = []
    for i, msg in enumerate(messages):
        content = msg.get("content")
        if isinstance(content, list):
            for j, block in enumerate(content):
                if isinstance(block, dict):
                    inner = block.get("content")
                    if isinstance(inner, list):
                        for k, inner_block in enumerate(inner):
                            if isinstance(inner_block, dict) and inner_block.get("type") == "image":
                                img_locations.append((i, j, k))
                    elif block.get("type") == "image":
                        img_locations.append((i, j, -1))

    if len(img_locations) <= keep_last:
        return

    for msg_idx, block_idx, inner_idx in img_locations[:-keep_last]:
        content = messages[msg_idx]["content"]
        placeholder = {"type": "text", "text": "[Previous screenshot removed]"}
        if inner_idx >= 0 and isinstance(content[block_idx].get("content"), list):
            content[block_idx]["content"][inner_idx] = placeholder
        elif content[block_idx].get("type") == "image":
            content[block_idx] = placeholder


def _compact_elements(
    result: dict[str, Any], max_elems: int = _MAX_ELEMENTS_IN_RESULT
) -> dict[str, Any]:
    """Reduce element list to the most useful entries."""
    elements = result.get("elements")
    if not elements or not isinstance(elements, list) or len(elements) <= max_elems:
        return result

    result = dict(result)
    interactable = [e for e in elements if e.get("interactable")]
    text_only = [e for e in elements if not e.get("interactable") and e.get("text")]
    others = [e for e in elements if e not in interactable and e not in text_only]

    kept = interactable[:max_elems]
    remaining = max_elems - len(kept)
    if remaining > 0:
        kept.extend(text_only[:remaining])
        remaining = max_elems - len(kept)
    if remaining > 0:
        kept.extend(others[:remaining])

    result["elements"] = kept
    result["_elements_truncated"] = len(elements) - len(kept)
    result["_elements_total"] = len(elements)
    return result


# ---------------------------------------------------------------------------
# ReactivePlanLoop
# ---------------------------------------------------------------------------


class ReactivePlanLoop:
    """Generic Plan → Execute → Observe → Re-plan loop.

    Works with any LLM provider and any set of daemon tools.
    Can be used for computer use, task automation, or any agent workflow.
    """

    def __init__(
        self,
        provider: AgentLLMProvider,
        daemon: AsyncLLMOSClient,
        *,
        max_replans: int = 3,
        max_steps_per_plan: int = 8,
        max_total_actions: int = 30,
        max_tokens: int = 4096,
        verbose: bool = False,
        safeguards: SafeguardConfig | None = None,
        approval_mode: str = "auto",
    ) -> None:
        self._provider = provider
        self._daemon = daemon
        self._max_replans = max_replans
        self._max_steps_per_plan = max_steps_per_plan
        self._max_total_actions = max_total_actions
        self._max_tokens = max_tokens
        self._verbose = verbose
        self._safeguards = safeguards or SafeguardConfig()
        self._approval_mode = approval_mode

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(
        self,
        task: str,
        system_prompt: str,
        tools: list[ToolDefinition],
    ) -> AgentResult:
        """Execute a task using the reactive plan loop."""
        t0 = time.monotonic()
        all_steps: list[StepRecord] = []
        total_actions = 0

        # Augment system prompt with planning instructions.
        augmented_prompt = system_prompt + _PLANNING_INSTRUCTION

        # Initial messages.
        messages: list[dict[str, Any]] = self._provider.build_user_message(task)

        for plan_iteration in range(self._max_replans + 1):
            if self._verbose:
                print(f"\n{'='*60}")
                print(f"Plan iteration {plan_iteration + 1}/{self._max_replans + 1}")
                print(f"{'='*60}")

            # Strip old screenshots before calling LLM.
            _strip_old_screenshots(messages)

            # Ask LLM for a plan (or single action, or completion).
            turn = await self._call_llm_with_retry(
                augmented_prompt, messages, tools
            )

            # Case 1: LLM returned text with a JSON plan.
            # IMPORTANT: Check for plans BEFORE is_done, because Anthropic
            # sets stop_reason="end_turn" (is_done=True) on text-only
            # responses even when the text contains a plan to execute.
            plan_steps = self._extract_plan_from_text(turn.text)
            if plan_steps:
                if self._verbose:
                    print(f"  Multi-step plan: {len(plan_steps)} actions")
                    for s in plan_steps:
                        print(f"    {s.id}: {s.action} — {s.description}")

                # Build a text-only assistant message for the plan.
                # IMPORTANT: Do NOT use build_assistant_message(turn) here
                # because the raw response may contain tool_use blocks.
                # Anthropic requires tool_result after tool_use, and since
                # we're using the JSON plan path (not tool calls), we must
                # strip tool_use blocks to avoid API errors.
                plan_assistant_msg = {
                    "role": "assistant",
                    "content": turn.text or "",
                }

                # Validate safeguards.
                warnings = self._safeguards.validate_plan_steps(
                    [{"id": s.id, "action": s.action, "params": s.params}
                     for s in plan_steps]
                )
                if warnings:
                    if self._verbose:
                        for w in warnings:
                            print(f"  SAFEGUARD: {w}")
                    # Remove blocked steps.
                    blocked_ids = set()
                    for w in warnings:
                        match = re.match(r"Step '([^']+)':", w)
                        if match:
                            blocked_ids.add(match.group(1))
                    plan_steps = [s for s in plan_steps if s.id not in blocked_ids]

                if not plan_steps:
                    # All steps blocked — tell LLM.
                    messages.append(plan_assistant_msg)
                    feedback = (
                        "All planned actions were blocked by safety rules. "
                        "Try a different approach that avoids dangerous hotkeys."
                    )
                    messages.extend(self._provider.build_user_message(feedback))
                    continue

                # Cap steps.
                plan_steps = plan_steps[:self._max_steps_per_plan]

                # Execute the multi-step plan.
                messages.append(plan_assistant_msg)
                plan_result, step_records = await self._execute_plan(plan_steps)
                all_steps.extend(step_records)
                total_actions += len(step_records)

                if total_actions >= self._max_total_actions:
                    return AgentResult(
                        success=False,
                        output=f"Task not completed within {total_actions} actions.",
                        steps=all_steps,
                        total_duration_ms=(time.monotonic() - t0) * 1000,
                    )

                # Build observation message for LLM (text + screenshots).
                observation_text, obs_screenshots = self._build_observation(
                    plan_result, plan_steps,
                    iteration=plan_iteration,
                    max_iterations=self._max_replans + 1,
                )
                if obs_screenshots:
                    # Build multimodal message: images first, then text.
                    content_blocks: list[dict[str, Any]] = []
                    for shot in obs_screenshots[-2:]:  # Keep max 2 screenshots
                        content_blocks.append({
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": shot,
                            },
                        })
                    content_blocks.append({
                        "type": "text",
                        "text": observation_text,
                    })
                    messages.append({"role": "user", "content": content_blocks})
                else:
                    messages.extend(
                        self._provider.build_user_message(observation_text)
                    )
                continue

            # Case 2: LLM says task is done (no tool calls, no plan).
            if turn.is_done and not turn.tool_calls:
                return AgentResult(
                    success=True,
                    output=turn.text or "",
                    steps=all_steps,
                    total_duration_ms=(time.monotonic() - t0) * 1000,
                )

            # Case 3: LLM returned tool calls (single-action fallback).
            if turn.tool_calls:
                if self._verbose:
                    print(f"  Single-action fallback: {len(turn.tool_calls)} tool calls")

                messages.append(self._provider.build_assistant_message(turn))
                tool_results: list[ToolResult] = []

                for tc in turn.tool_calls:
                    step_t0 = time.monotonic()
                    result = await self._execute_single_action(tc.name, tc.arguments)
                    step_ms = (time.monotonic() - step_t0) * 1000

                    all_steps.append(StepRecord(
                        tool_name=tc.name,
                        tool_input=tc.arguments,
                        tool_output=result,
                        duration_ms=step_ms,
                    ))
                    total_actions += 1

                    if self._verbose:
                        preview = json.dumps(result, default=str)[:200]
                        print(f"  [{tc.name}] ({step_ms:.0f}ms): {preview}")

                    tool_results.append(self._make_tool_result(tc.id, result))

                messages.extend(
                    self._provider.build_tool_results_message(tool_results)
                )

                if total_actions >= self._max_total_actions:
                    return AgentResult(
                        success=False,
                        output=f"Task not completed within {total_actions} actions.",
                        steps=all_steps,
                        total_duration_ms=(time.monotonic() - t0) * 1000,
                    )
                continue

            # Case 4: No tool calls and no plan but not marked done.
            # Treat text as final output.
            return AgentResult(
                success=True,
                output=turn.text or "",
                steps=all_steps,
                total_duration_ms=(time.monotonic() - t0) * 1000,
            )

        # Exhausted all plan iterations.
        return AgentResult(
            success=False,
            output=f"Task not completed after {self._max_replans + 1} plan iterations.",
            steps=all_steps,
            total_duration_ms=(time.monotonic() - t0) * 1000,
        )

    # ------------------------------------------------------------------
    # LLM interaction
    # ------------------------------------------------------------------

    async def _call_llm_with_retry(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[ToolDefinition],
    ) -> LLMTurn:
        """Call the LLM with exponential backoff on rate limits."""
        for attempt in range(4):
            try:
                return await self._provider.create_message(
                    system=system,
                    messages=messages,
                    tools=tools,
                    max_tokens=self._max_tokens,
                )
            except Exception as exc:
                if "rate_limit" in str(exc).lower() or "429" in str(exc):
                    wait = 15 * (attempt + 1)
                    if self._verbose:
                        print(f"  Rate limited, waiting {wait}s...")
                    await asyncio.sleep(wait)
                else:
                    raise
        # Should not reach here, but satisfy type checker.
        raise RuntimeError("Rate limit retries exhausted")

    # ------------------------------------------------------------------
    # Plan extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_plan_from_text(text: str | None) -> list[PlanStep] | None:
        """Extract a JSON plan array from the LLM's text response.

        Returns None if no valid plan is found (triggers single-action fallback).
        """
        if not text:
            return None

        # Look for ```json ... ``` fenced blocks first.
        fenced = re.findall(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
        candidates = fenced if fenced else [text]

        for candidate in candidates:
            candidate = candidate.strip()
            if not candidate.startswith("["):
                continue
            try:
                raw = json.loads(candidate)
            except json.JSONDecodeError:
                continue

            if not isinstance(raw, list) or not raw:
                continue

            steps: list[PlanStep] = []
            for item in raw:
                if not isinstance(item, dict):
                    continue
                action = item.get("action", "")
                if not action:
                    continue
                steps.append(PlanStep(
                    id=str(item.get("id", f"s{len(steps) + 1}")),
                    action=action,
                    params=item.get("params", {}),
                    depends_on=item.get("depends_on", []),
                    description=item.get("description", ""),
                ))

            if steps:
                return steps

        return None

    # ------------------------------------------------------------------
    # Plan execution (multi-action via daemon)
    # ------------------------------------------------------------------

    async def _execute_plan(
        self, steps: list[PlanStep]
    ) -> tuple[dict[str, Any], list[StepRecord]]:
        """Submit a multi-step plan to the daemon and wait for completion.

        Returns (plan_result_dict, step_records).
        """
        plan_id = str(uuid.uuid4())

        # Convert PlanSteps to IML actions.
        actions: list[dict[str, Any]] = []
        for step in steps:
            parts = step.action.split("__", 1)
            if len(parts) != 2:
                continue
            module_id, action_name = parts

            # For read_screen, inject include_screenshot if provider has vision.
            params = dict(step.params)
            if action_name == "read_screen" and self._provider.supports_vision:
                params.setdefault("include_screenshot", True)

            clean_params = {k: v for k, v in params.items() if v is not None}

            action_dict: dict[str, Any] = {
                "id": step.id,
                "action": action_name,
                "module": module_id,
                "params": clean_params,
            }
            if step.depends_on:
                action_dict["depends_on"] = step.depends_on
            actions.append(action_dict)

        if not actions:
            return {"error": "No valid actions in plan"}, []

        plan = {
            "plan_id": plan_id,
            "protocol_version": "2.0",
            "description": f"ReactivePlanLoop: {len(actions)} actions",
            "execution_mode": "sequential",
            "actions": actions,
        }

        t0 = time.monotonic()
        try:
            await self._daemon.submit_plan(plan, async_execution=True)
        except Exception as exc:
            return {"error": f"Plan submission failed: {exc}"}, []

        # Poll for completion.
        result = await self._poll_plan(plan_id)
        total_ms = (time.monotonic() - t0) * 1000

        # Build step records from results.
        step_records: list[StepRecord] = []
        plan_actions = result.get("actions", [])
        for pa in plan_actions:
            step_records.append(StepRecord(
                tool_name=f"{pa.get('module', '?')}__{pa.get('action', '?')}",
                tool_input=pa.get("params", {}),
                tool_output=pa.get("result") or {"error": pa.get("error", "unknown")},
                duration_ms=total_ms / max(len(plan_actions), 1),
            ))

        return result, step_records

    async def _poll_plan(
        self, plan_id: str, max_wait: float = 300.0
    ) -> dict[str, Any]:
        """Poll plan status until terminal, handling approvals."""
        start = time.monotonic()

        while (time.monotonic() - start) < max_wait:
            plan_state = await self._daemon.get_plan(plan_id)
            status = plan_state.get("status", "")

            if status in ("completed", "failed", "cancelled"):
                return plan_state

            # Handle pending approvals.
            for action_data in plan_state.get("actions", []):
                if action_data.get("status") == "awaiting_approval":
                    await self._handle_approval(plan_id, action_data)
                    break

            await asyncio.sleep(0.5)

        return {"error": f"Plan timed out after {max_wait}s", "status": "failed"}

    async def _handle_approval(
        self, plan_id: str, action_data: dict[str, Any]
    ) -> None:
        """Auto-approve or reject based on approval_mode."""
        action_id = action_data.get("action_id", action_data.get("id", "action"))

        if self._approval_mode in ("auto", "always_approve"):
            decision = "approve"
            reason = "Auto-approved by ReactivePlanLoop"
        else:
            decision = "reject"
            reason = "Rejected by ReactivePlanLoop policy"

        await self._daemon.approve_action(
            plan_id=plan_id,
            action_id=action_id,
            decision=decision,
            reason=reason,
            approved_by="reactive_plan_loop",
        )

    # ------------------------------------------------------------------
    # Single-action execution (fallback)
    # ------------------------------------------------------------------

    async def _execute_single_action(
        self, tool_name: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Execute a single action via the daemon (fallback path)."""
        parts = tool_name.split("__", 1)
        if len(parts) != 2:
            return {"error": f"Invalid tool name: {tool_name}"}

        module_id, action_name = parts

        if action_name == "read_screen":
            params = {**params, "include_screenshot": self._provider.supports_vision}

        clean_params = {k: v for k, v in params.items() if v is not None}

        plan = {
            "plan_id": str(uuid.uuid4()),
            "protocol_version": "2.0",
            "description": f"SingleAction: {module_id}.{action_name}",
            "actions": [{
                "id": "action",
                "action": action_name,
                "module": module_id,
                "params": clean_params,
            }],
        }

        try:
            result = await self._daemon.submit_plan(plan, async_execution=True)
        except Exception as exc:
            return {"error": f"Daemon submission failed: {exc}"}

        plan_id = result.get("plan_id", plan["plan_id"])

        try:
            plan_result = await self._poll_plan(plan_id)
            actions = plan_result.get("actions", [])
            if actions and len(actions) == 1:
                action = actions[0]
                if action.get("result") is not None:
                    return action["result"]
                if action.get("error"):
                    return {"error": str(action["error"])}
            return plan_result
        except Exception as exc:
            return {"error": f"Execution failed: {exc}"}

    # ------------------------------------------------------------------
    # Observation builder
    # ------------------------------------------------------------------

    def _build_observation(
        self,
        plan_result: dict[str, Any],
        plan_steps: list[PlanStep],
        *,
        iteration: int = 0,
        max_iterations: int = 9,
    ) -> tuple[str, list[str]]:
        """Build observation message + extracted screenshots from plan results.

        Returns (observation_text, screenshot_b64_list).
        Screenshots are extracted from read_screen results so the LLM can
        actually *see* what's on screen.
        """
        remaining = max_iterations - iteration - 1
        lines: list[str] = [
            f"## Plan Execution Results (iteration {iteration + 1}/{max_iterations},"
            f" {remaining} remaining)\n",
        ]
        screenshots: list[str] = []

        plan_status = plan_result.get("status", "unknown")
        lines.append(f"Overall status: **{plan_status}**\n")

        actions = plan_result.get("actions", [])
        step_map = {s.id: s for s in plan_steps}

        succeeded = 0
        failed_details: list[str] = []
        result_summaries: list[str] = []

        for a in actions:
            aid = a.get("action_id", a.get("id", "?"))
            status = a.get("status", "unknown")
            desc = step_map.get(aid, PlanStep(id=aid, action="?")).description

            if status == "completed":
                succeeded += 1
                result = a.get("result", {})
                if isinstance(result, dict):
                    # Extract screenshots for vision-capable providers.
                    screenshot_b64 = result.get("screenshot_b64")
                    if screenshot_b64 and self._provider.supports_vision:
                        screenshots.append(
                            _resize_screenshot_b64(screenshot_b64)
                        )

                    # Extract scene graph if present — replaces flat element list.
                    scene_graph = result.get("scene_graph")
                    if scene_graph:
                        result_summaries.append(
                            f"- **{aid}** ({desc}) — Screen Layout:\n{scene_graph}"
                        )

                    # Compact elements and strip b64 from text summary.
                    result = _compact_elements(
                        {k: v for k, v in result.items()
                         if not k.endswith("_b64") and k != "scene_graph"}
                    )
                summary = json.dumps(result, default=str)[:3000]
                result_summaries.append(f"- **{aid}** ({desc}): {summary}")
            elif status == "failed":
                error = a.get("error", "unknown error")
                failed_details.append(f"- **{aid}** ({desc}): FAILED — {error}")
            elif status == "skipped":
                failed_details.append(f"- **{aid}** ({desc}): SKIPPED")

        lines.append(f"Succeeded: {succeeded}/{len(actions)}\n")

        if result_summaries:
            lines.append("### Results")
            lines.extend(result_summaries)
            lines.append("")

        if failed_details:
            lines.append("### Failures")
            lines.extend(failed_details)
            lines.append("")
            lines.append(
                "Analyze the failures and create a corrective plan. "
                "Try a different approach — do NOT repeat the same failed params."
            )
        else:
            if remaining <= 2:
                lines.append(
                    "All steps succeeded. You have very few iterations left. "
                    "You MUST now summarize what you have observed and "
                    "conclude the task with text only (NO JSON plan). "
                    "Use the information you already gathered."
                )
            else:
                lines.append(
                    "All steps succeeded. Is the original task now complete? "
                    "If yes, summarize what was accomplished (text only, no JSON plan). "
                    "If not, create a new plan for the remaining steps."
                )

        return "\n".join(lines), screenshots

    # ------------------------------------------------------------------
    # Tool result formatting
    # ------------------------------------------------------------------

    def _make_tool_result(
        self, tool_call_id: str, result: dict[str, Any]
    ) -> ToolResult:
        """Build a provider-agnostic ToolResult from raw daemon output."""
        if isinstance(result, dict):
            result = dict(result)
        else:
            result = {"output": result}

        screenshot_b64 = result.pop("screenshot_b64", None)

        if not self._provider.supports_vision:
            screenshot_b64 = None

        if screenshot_b64:
            screenshot_b64 = _resize_screenshot_b64(screenshot_b64)

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
