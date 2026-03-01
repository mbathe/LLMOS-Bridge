"""Unit tests — ReactivePlanLoop (all LLM + daemon calls mocked)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from langchain_llmos.loop import (
    AgentResult,
    PlanStep,
    ReactivePlanLoop,
    _compact_elements,
    _strip_old_screenshots,
)
from langchain_llmos.providers.base import (
    AgentLLMProvider,
    LLMTurn,
    ToolCall,
    ToolDefinition,
    ToolResult,
)
from langchain_llmos.safeguards import SafeguardConfig


# ---------------------------------------------------------------------------
# Mock provider
# ---------------------------------------------------------------------------


class MockProvider(AgentLLMProvider):
    """Simple mock provider that returns pre-configured turns."""

    def __init__(self, turns: list[LLMTurn]) -> None:
        self._turns = list(turns)
        self._call_count = 0

    async def create_message(self, *, system, messages, tools, max_tokens=4096):
        if self._call_count < len(self._turns):
            turn = self._turns[self._call_count]
            self._call_count += 1
            return turn
        # Default: task done.
        return LLMTurn(text="Done.", tool_calls=[], is_done=True, raw_response=None)

    def format_tool_definitions(self, tools):
        return [{"name": t.name, "description": t.description} for t in tools]

    def build_user_message(self, text):
        return [{"role": "user", "content": text}]

    def build_assistant_message(self, turn):
        return {"role": "assistant", "content": turn.text or ""}

    def build_tool_results_message(self, results):
        return [{"role": "user", "content": f"Tool result: {r.text}"} for r in results]

    @property
    def supports_vision(self):
        return False

    async def close(self):
        pass


# ---------------------------------------------------------------------------
# Mock daemon
# ---------------------------------------------------------------------------


def _make_daemon(plan_result=None) -> AsyncMock:
    """Create a mock AsyncLLMOSClient."""
    daemon = AsyncMock()
    daemon.submit_plan.return_value = {"plan_id": "test-plan", "status": "pending"}
    daemon.get_plan.return_value = plan_result or {
        "status": "completed",
        "actions": [
            {
                "id": "s1",
                "action_id": "s1",
                "module": "gui",
                "action": "key_press",
                "status": "completed",
                "result": {"pressed": True},
                "params": {"keys": ["ctrl", "alt", "t"]},
            }
        ],
    }
    daemon.approve_action = AsyncMock(return_value={"applied": True})
    return daemon


# ---------------------------------------------------------------------------
# Tests — Plan extraction
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPlanExtraction:
    def test_extracts_fenced_json(self) -> None:
        text = """Let me create a plan:

```json
[
  {"id": "s1", "action": "gui__key_press", "params": {"keys": ["ctrl", "alt", "t"]}, "description": "Open terminal"},
  {"id": "s2", "action": "gui__type_text", "params": {"text": "hello"}, "depends_on": ["s1"]}
]
```
"""
        steps = ReactivePlanLoop._extract_plan_from_text(text)
        assert steps is not None
        assert len(steps) == 2
        assert steps[0].id == "s1"
        assert steps[0].action == "gui__key_press"
        assert steps[1].depends_on == ["s1"]

    def test_extracts_unfenced_json(self) -> None:
        text = '[{"id": "s1", "action": "gui__key_press", "params": {"keys": ["enter"]}}]'
        steps = ReactivePlanLoop._extract_plan_from_text(text)
        assert steps is not None
        assert len(steps) == 1

    def test_returns_none_for_plain_text(self) -> None:
        text = "I have completed the task. The file was created."
        assert ReactivePlanLoop._extract_plan_from_text(text) is None

    def test_returns_none_for_empty(self) -> None:
        assert ReactivePlanLoop._extract_plan_from_text(None) is None
        assert ReactivePlanLoop._extract_plan_from_text("") is None

    def test_returns_none_for_invalid_json(self) -> None:
        text = "```json\n{not valid json}\n```"
        assert ReactivePlanLoop._extract_plan_from_text(text) is None

    def test_returns_none_for_empty_array(self) -> None:
        text = "```json\n[]\n```"
        assert ReactivePlanLoop._extract_plan_from_text(text) is None

    def test_skips_items_without_action(self) -> None:
        text = '[{"id": "s1"}, {"id": "s2", "action": "gui__key_press", "params": {}}]'
        steps = ReactivePlanLoop._extract_plan_from_text(text)
        assert steps is not None
        assert len(steps) == 1
        assert steps[0].id == "s2"

    def test_auto_generates_ids(self) -> None:
        text = '[{"action": "gui__key_press", "params": {"keys": ["enter"]}}]'
        steps = ReactivePlanLoop._extract_plan_from_text(text)
        assert steps is not None
        assert steps[0].id == "s1"


# ---------------------------------------------------------------------------
# Tests — Loop execution
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLoopExecution:
    @pytest.mark.asyncio
    async def test_task_done_immediately(self) -> None:
        """LLM says task is done on first turn."""
        provider = MockProvider([
            LLMTurn(text="Task done!", tool_calls=[], is_done=True, raw_response=None),
        ])
        daemon = _make_daemon()
        loop = ReactivePlanLoop(provider=provider, daemon=daemon)

        result = await loop.run("Do something", "System prompt", [])
        assert result.success is True
        assert result.output == "Task done!"
        assert len(result.steps) == 0

    @pytest.mark.asyncio
    async def test_single_action_fallback(self) -> None:
        """LLM returns a tool call (no JSON plan) → single-action fallback."""
        provider = MockProvider([
            # First turn: tool call.
            LLMTurn(
                text=None,
                tool_calls=[
                    ToolCall(id="tc1", name="gui__key_press", arguments={"keys": ["enter"]}),
                ],
                is_done=False,
                raw_response=MagicMock(content=[]),
            ),
            # Second turn: done.
            LLMTurn(text="Done!", tool_calls=[], is_done=True, raw_response=None),
        ])
        daemon = _make_daemon()
        loop = ReactivePlanLoop(provider=provider, daemon=daemon)

        result = await loop.run("Press enter", "System prompt", [])
        assert result.success is True
        assert len(result.steps) == 1
        assert result.steps[0].tool_name == "gui__key_press"

    @pytest.mark.asyncio
    async def test_multi_step_plan(self) -> None:
        """LLM returns a JSON plan → multi-action execution."""
        plan_json = json.dumps([
            {"id": "s1", "action": "gui__key_press", "params": {"keys": ["ctrl", "alt", "t"]}},
            {"id": "s2", "action": "gui__type_text", "params": {"text": "ls"}, "depends_on": ["s1"]},
        ])
        provider = MockProvider([
            # First turn: plan.
            LLMTurn(
                text=f"```json\n{plan_json}\n```",
                tool_calls=[],
                is_done=False,
                raw_response=None,
            ),
            # Second turn: done.
            LLMTurn(text="Task complete!", tool_calls=[], is_done=True, raw_response=None),
        ])
        daemon = _make_daemon({
            "status": "completed",
            "actions": [
                {"id": "s1", "action_id": "s1", "module": "gui", "action": "key_press",
                 "status": "completed", "result": {"pressed": True}, "params": {}},
                {"id": "s2", "action_id": "s2", "module": "gui", "action": "type_text",
                 "status": "completed", "result": {"typed": True}, "params": {}},
            ],
        })
        loop = ReactivePlanLoop(provider=provider, daemon=daemon)

        result = await loop.run("Open terminal and type ls", "System prompt", [])
        assert result.success is True
        assert len(result.steps) == 2

        # Verify daemon received a multi-action plan.
        call_args = daemon.submit_plan.call_args
        submitted_plan = call_args[0][0]
        assert len(submitted_plan["actions"]) == 2
        assert submitted_plan["actions"][1].get("depends_on") == ["s1"]

    @pytest.mark.asyncio
    async def test_max_replans_exceeded(self) -> None:
        """Loop fails after max re-plan iterations."""
        plan_json = json.dumps([
            {"id": "s1", "action": "gui__key_press", "params": {"keys": ["enter"]}},
        ])
        # Always return a plan, never say done.
        provider = MockProvider([
            LLMTurn(text=f"```json\n{plan_json}\n```", tool_calls=[], is_done=False, raw_response=None),
            LLMTurn(text=f"```json\n{plan_json}\n```", tool_calls=[], is_done=False, raw_response=None),
            LLMTurn(text=f"```json\n{plan_json}\n```", tool_calls=[], is_done=False, raw_response=None),
            LLMTurn(text=f"```json\n{plan_json}\n```", tool_calls=[], is_done=False, raw_response=None),
            LLMTurn(text=f"```json\n{plan_json}\n```", tool_calls=[], is_done=False, raw_response=None),
        ])
        daemon = _make_daemon()
        loop = ReactivePlanLoop(provider=provider, daemon=daemon, max_replans=2)

        result = await loop.run("Infinite task", "System prompt", [])
        assert result.success is False
        assert "3 plan iterations" in result.output

    @pytest.mark.asyncio
    async def test_observation_includes_failures(self) -> None:
        """When actions fail, observation includes error details."""
        plan_json = json.dumps([
            {"id": "s1", "action": "gui__key_press", "params": {"keys": ["enter"]}},
        ])
        provider = MockProvider([
            LLMTurn(text=f"```json\n{plan_json}\n```", tool_calls=[], is_done=False, raw_response=None),
            LLMTurn(text="Giving up.", tool_calls=[], is_done=True, raw_response=None),
        ])
        daemon = _make_daemon({
            "status": "failed",
            "actions": [
                {"id": "s1", "action_id": "s1", "module": "gui", "action": "key_press",
                 "status": "failed", "error": "Permission denied", "params": {}},
            ],
        })
        loop = ReactivePlanLoop(provider=provider, daemon=daemon)

        result = await loop.run("Press enter", "System prompt", [])
        assert result.success is True  # LLM said done (gave up)

        # The second LLM call should have received the failure observation.
        assert provider._call_count == 2


# ---------------------------------------------------------------------------
# Tests — Safeguards integration
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSafeguardsInLoop:
    @pytest.mark.asyncio
    async def test_blocked_hotkey_removed(self) -> None:
        """Plan steps with alt+F4 are removed."""
        plan_json = json.dumps([
            {"id": "s1", "action": "gui__key_press", "params": {"keys": ["alt", "f4"]}},
            {"id": "s2", "action": "gui__type_text", "params": {"text": "hello"}},
        ])
        provider = MockProvider([
            LLMTurn(text=f"```json\n{plan_json}\n```", tool_calls=[], is_done=False, raw_response=None),
            LLMTurn(text="Done!", tool_calls=[], is_done=True, raw_response=None),
        ])
        daemon = _make_daemon({
            "status": "completed",
            "actions": [
                {"id": "s2", "action_id": "s2", "module": "gui", "action": "type_text",
                 "status": "completed", "result": {"typed": True}, "params": {}},
            ],
        })
        loop = ReactivePlanLoop(provider=provider, daemon=daemon)

        result = await loop.run("Close and type", "System prompt", [])
        assert result.success is True

        # Only s2 should have been submitted.
        call_args = daemon.submit_plan.call_args
        submitted_plan = call_args[0][0]
        action_ids = [a["id"] for a in submitted_plan["actions"]]
        assert "s1" not in action_ids
        assert "s2" in action_ids


# ---------------------------------------------------------------------------
# Tests — Helpers
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHelpers:
    def test_compact_elements_below_limit(self) -> None:
        result = {"elements": [{"label": "a"}]}
        assert _compact_elements(result, max_elems=10) == result

    def test_compact_elements_truncates(self) -> None:
        elements = [{"label": f"e{i}", "interactable": i < 3} for i in range(20)]
        result = _compact_elements({"elements": elements}, max_elems=5)
        assert len(result["elements"]) == 5
        assert result["_elements_truncated"] == 15

    def test_strip_old_screenshots_noop(self) -> None:
        messages = [{"role": "user", "content": "hello"}]
        _strip_old_screenshots(messages, keep_last=2)
        assert messages[0]["content"] == "hello"

    def test_plan_step_defaults(self) -> None:
        step = PlanStep(id="s1", action="gui__key_press")
        assert step.params == {}
        assert step.depends_on == []
        assert step.description == ""
