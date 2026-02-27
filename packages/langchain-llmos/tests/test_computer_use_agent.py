"""Unit tests — ComputerUseAgent (all Anthropic + daemon calls mocked)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Mock Anthropic response types
# ---------------------------------------------------------------------------


@dataclass
class _TextBlock:
    type: str = "text"
    text: str = ""


@dataclass
class _ToolUseBlock:
    type: str = "tool_use"
    id: str = "tu_123"
    name: str = ""
    input: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.input is None:
            self.input = {}


@dataclass
class _MockResponse:
    content: list[Any] = None  # type: ignore[assignment]
    stop_reason: str = "end_turn"

    def __post_init__(self) -> None:
        if self.content is None:
            self.content = [_TextBlock(text="Done!")]


# ---------------------------------------------------------------------------
# Import guard test
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestImportGuard:
    def test_raises_without_anthropic(self) -> None:
        """AnthropicProvider raises ImportError when anthropic is missing."""
        with patch.dict("sys.modules", {"anthropic": None}):
            import importlib

            import langchain_llmos.providers.anthropic_provider as ap_mod

            original = ap_mod._AVAILABLE
            ap_mod._AVAILABLE = False
            try:
                with pytest.raises(ImportError, match="anthropic"):
                    ap_mod.AnthropicProvider(api_key="test")
            finally:
                ap_mod._AVAILABLE = original


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_daemon() -> AsyncMock:
    """Mock AsyncLLMOSClient."""
    client = AsyncMock()
    client.list_modules.return_value = [
        {"module_id": "computer_control", "available": True},
        {"module_id": "gui", "available": True},
    ]
    client.get_module_manifest.side_effect = lambda mod_id: {
        "computer_control": {
            "module_id": "computer_control",
            "actions": [
                {
                    "name": "read_screen",
                    "description": "Read the screen.",
                    "params_schema": {
                        "type": "object",
                        "properties": {
                            "include_screenshot": {"type": "boolean", "default": False},
                        },
                    },
                    "permission_required": "power_user",
                },
                {
                    "name": "click_element",
                    "description": "Click a UI element.",
                    "params_schema": {
                        "type": "object",
                        "properties": {
                            "target_description": {"type": "string"},
                        },
                        "required": ["target_description"],
                    },
                    "permission_required": "power_user",
                },
            ],
        },
        "gui": {
            "module_id": "gui",
            "actions": [
                {
                    "name": "key_press",
                    "description": "Press keys.",
                    "params_schema": {
                        "type": "object",
                        "properties": {"keys": {"type": "array"}},
                    },
                    "permission_required": "power_user",
                },
            ],
        },
    }[mod_id]
    # submit_plan returns immediately for async polling.
    client.submit_plan.return_value = {"plan_id": "plan_1", "status": "pending"}
    # get_plan returns completed result.
    client.get_plan.return_value = {
        "status": "completed",
        "actions": [
            {
                "action_id": "action",
                "status": "completed",
                "result": {"elements": [], "element_count": 0, "text": "Hello"},
                "error": None,
            }
        ],
    }
    client.get_system_prompt.return_value = "You are an assistant."
    client.approve_action = AsyncMock(return_value={"applied": True})
    client.close = AsyncMock()
    return client


@pytest.fixture
def mock_anthropic() -> AsyncMock:
    """Mock Anthropic async client."""
    client = AsyncMock()
    client.messages.create = AsyncMock(
        return_value=_MockResponse(
            content=[_TextBlock(text="Task completed!")],
            stop_reason="end_turn",
        )
    )
    return client


@pytest.fixture
def agent(mock_daemon: AsyncMock, mock_anthropic: AsyncMock) -> Any:
    """Create a ComputerUseAgent with mocked dependencies."""
    from langchain_llmos.agent import ComputerUseAgent

    with patch("langchain_llmos.providers.anthropic_provider.anthropic") as mock_anth_mod:
        mock_anth_mod.AsyncAnthropic.return_value = mock_anthropic
        with patch("langchain_llmos.agent.AsyncLLMOSClient", return_value=mock_daemon):
            a = ComputerUseAgent(
                anthropic_api_key="test-key",
                daemon_url="http://localhost:40000",
                max_steps=5,
            )
    a._daemon = mock_daemon
    a._provider._client = mock_anthropic
    return a


# ---------------------------------------------------------------------------
# Data class tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDataClasses:
    def test_step_record(self) -> None:
        from langchain_llmos.agent import StepRecord

        step = StepRecord(
            tool_name="computer_control__read_screen",
            tool_input={"include_screenshot": True},
            tool_output={"elements": []},
            duration_ms=150.0,
        )
        assert step.tool_name == "computer_control__read_screen"
        assert step.duration_ms == 150.0

    def test_agent_result(self) -> None:
        from langchain_llmos.agent import AgentResult

        result = AgentResult(success=True, output="Done", total_duration_ms=1000.0)
        assert result.success is True
        assert result.steps == []


# ---------------------------------------------------------------------------
# _build_tools
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildTools:
    @pytest.mark.asyncio
    async def test_builds_tools_from_manifests(self, agent: Any, mock_daemon: AsyncMock) -> None:
        tools = await agent._build_tools()
        names = {t["name"] for t in tools}
        assert "computer_control__read_screen" in names
        assert "computer_control__click_element" in names
        assert "gui__key_press" in names
        assert len(tools) == 3

    @pytest.mark.asyncio
    async def test_tool_has_input_schema(self, agent: Any) -> None:
        tools = await agent._build_tools()
        read_screen = next(t for t in tools if t["name"] == "computer_control__read_screen")
        assert "input_schema" in read_screen
        assert read_screen["input_schema"]["type"] == "object"

    @pytest.mark.asyncio
    async def test_filters_by_allowed_modules(self, agent: Any, mock_daemon: AsyncMock) -> None:
        agent._allowed_modules = ["computer_control"]
        tools = await agent._build_tools()
        names = {t["name"] for t in tools}
        assert "gui__key_press" not in names
        assert "computer_control__read_screen" in names


# ---------------------------------------------------------------------------
# _format_tool_result (legacy compat)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFormatToolResult:
    def test_text_only_result(self, agent: Any) -> None:
        result = {"elements": [], "text": "Hello"}
        content = agent._format_tool_result(result)
        assert len(content) == 1
        assert content[0]["type"] == "text"
        parsed = json.loads(content[0]["text"])
        assert parsed["text"] == "Hello"

    def test_result_with_screenshot(self, agent: Any) -> None:
        result = {
            "elements": [],
            "text": "Hello",
            "screenshot_b64": "iVBORw0KGgoAAAANSUhEUg_FAKE",
        }
        content = agent._format_tool_result(result)
        assert len(content) == 2
        # First block is the image
        assert content[0]["type"] == "image"
        assert content[0]["source"]["type"] == "base64"
        assert content[0]["source"]["data"] == "iVBORw0KGgoAAAANSUhEUg_FAKE"
        # Second block is JSON without screenshot_b64
        text_data = json.loads(content[1]["text"])
        assert "screenshot_b64" not in text_data
        assert text_data["text"] == "Hello"

    def test_empty_result(self, agent: Any) -> None:
        content = agent._format_tool_result({})
        assert len(content) == 1
        assert content[0]["type"] == "text"


# ---------------------------------------------------------------------------
# _execute_tool
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExecuteTool:
    @pytest.mark.asyncio
    async def test_basic_execution(self, agent: Any, mock_daemon: AsyncMock) -> None:
        result = await agent._execute_tool(
            "computer_control__click_element",
            {"target_description": "Submit"},
        )
        # Should have called submit_plan
        mock_daemon.submit_plan.assert_called_once()
        plan = mock_daemon.submit_plan.call_args[0][0]
        assert plan["actions"][0]["module"] == "computer_control"
        assert plan["actions"][0]["action"] == "click_element"
        assert plan["actions"][0]["params"]["target_description"] == "Submit"

    @pytest.mark.asyncio
    async def test_read_screen_auto_injects_screenshot(self, agent: Any, mock_daemon: AsyncMock) -> None:
        """read_screen automatically sets include_screenshot=True (provider supports vision)."""
        await agent._execute_tool("computer_control__read_screen", {})
        plan = mock_daemon.submit_plan.call_args[0][0]
        assert plan["actions"][0]["params"]["include_screenshot"] is True

    @pytest.mark.asyncio
    async def test_invalid_tool_name(self, agent: Any) -> None:
        result = await agent._execute_tool("invalid_name", {})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_daemon_error(self, agent: Any, mock_daemon: AsyncMock) -> None:
        mock_daemon.submit_plan.side_effect = Exception("Connection refused")
        result = await agent._execute_tool("computer_control__read_screen", {})
        assert "error" in result
        assert "Connection refused" in result["error"]


# ---------------------------------------------------------------------------
# Agent loop (run)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAgentLoop:
    @pytest.mark.asyncio
    async def test_immediate_end_turn(self, agent: Any, mock_anthropic: AsyncMock) -> None:
        """Claude responds immediately without tool calls."""
        result = await agent.run("What do you see?")
        assert result.success is True
        assert "Task completed!" in result.output
        assert len(result.steps) == 0

    @pytest.mark.asyncio
    async def test_single_tool_call_then_end(self, agent: Any, mock_anthropic: AsyncMock) -> None:
        """Claude makes one tool call then finishes."""
        # First response: tool call
        tool_response = _MockResponse(
            content=[
                _TextBlock(text="Let me read the screen."),
                _ToolUseBlock(id="tu_1", name="computer_control__read_screen", input={}),
            ],
            stop_reason="tool_use",
        )
        # Second response: end turn
        final_response = _MockResponse(
            content=[_TextBlock(text="I can see a desktop with icons.")],
            stop_reason="end_turn",
        )
        mock_anthropic.messages.create = AsyncMock(
            side_effect=[tool_response, final_response]
        )

        result = await agent.run("Describe the screen")
        assert result.success is True
        assert len(result.steps) == 1
        assert result.steps[0].tool_name == "computer_control__read_screen"
        assert "desktop" in result.output

    @pytest.mark.asyncio
    async def test_multi_step_loop(self, agent: Any, mock_anthropic: AsyncMock) -> None:
        """Claude reads screen, clicks, reads again, then finishes."""
        responses = [
            _MockResponse(
                content=[_ToolUseBlock(id="tu_1", name="computer_control__read_screen")],
                stop_reason="tool_use",
            ),
            _MockResponse(
                content=[_ToolUseBlock(
                    id="tu_2",
                    name="computer_control__click_element",
                    input={"target_description": "Submit"},
                )],
                stop_reason="tool_use",
            ),
            _MockResponse(
                content=[_ToolUseBlock(id="tu_3", name="computer_control__read_screen")],
                stop_reason="tool_use",
            ),
            _MockResponse(
                content=[_TextBlock(text="Done clicking Submit.")],
                stop_reason="end_turn",
            ),
        ]
        mock_anthropic.messages.create = AsyncMock(side_effect=responses)

        result = await agent.run("Click the Submit button")
        assert result.success is True
        assert len(result.steps) == 3
        assert result.steps[1].tool_name == "computer_control__click_element"

    @pytest.mark.asyncio
    async def test_max_steps_exhausted(self, agent: Any, mock_anthropic: AsyncMock) -> None:
        """Agent stops after max_steps."""
        # Always return a tool call (never end_turn)
        infinite_tool_response = _MockResponse(
            content=[_ToolUseBlock(id="tu_1", name="computer_control__read_screen")],
            stop_reason="tool_use",
        )
        mock_anthropic.messages.create = AsyncMock(return_value=infinite_tool_response)

        result = await agent.run("Loop forever", max_steps=3)
        assert result.success is False
        assert len(result.steps) == 3
        assert "not completed" in result.output

    @pytest.mark.asyncio
    async def test_verbose_mode(self, agent: Any, mock_anthropic: AsyncMock, capsys: Any) -> None:
        """Verbose mode prints progress."""
        agent._verbose = True
        await agent.run("Test task")
        captured = capsys.readouterr()
        assert "Step 1" in captured.out


# ---------------------------------------------------------------------------
# _extract_action_result
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExtractActionResult:
    def test_extracts_result(self) -> None:
        from langchain_llmos.agent import ComputerUseAgent

        plan_result = {
            "actions": [{"result": {"clicked": True}, "error": None}]
        }
        assert ComputerUseAgent._extract_action_result(plan_result) == {"clicked": True}

    def test_extracts_error(self) -> None:
        from langchain_llmos.agent import ComputerUseAgent

        plan_result = {
            "actions": [{"result": None, "error": "Not found"}]
        }
        assert ComputerUseAgent._extract_action_result(plan_result) == {"error": "Not found"}

    def test_fallback_full_result(self) -> None:
        from langchain_llmos.agent import ComputerUseAgent

        plan_result = {"status": "failed", "message": "Oops"}
        assert ComputerUseAgent._extract_action_result(plan_result) == plan_result
