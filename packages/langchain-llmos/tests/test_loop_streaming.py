"""Unit tests — ReactivePlanLoop streaming integration.

Tests the _stream_plan method, progress_log, and _build_observation
progress section.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from langchain_llmos.loop import PlanStep, ReactivePlanLoop


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_loop(**kwargs) -> ReactivePlanLoop:
    provider = MagicMock()
    provider.supports_vision = False
    daemon = AsyncMock()
    return ReactivePlanLoop(
        provider=provider,
        daemon=daemon,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProgressLog:
    def test_progress_log_initialised(self) -> None:
        loop = _make_loop()
        assert loop._progress_log == []


@pytest.mark.unit
class TestStreamPlanFallback:
    async def test_falls_back_when_no_httpx_sse(self) -> None:
        """Without httpx-sse, _stream_plan falls back to _poll_plan."""
        loop = _make_loop()
        loop._daemon.get_plan = AsyncMock(
            return_value={"status": "completed", "actions": []}
        )

        result = await loop._stream_plan("test-plan-id")

        assert result["status"] == "completed"
        # get_plan was called (polling fallback).
        loop._daemon.get_plan.assert_called()


@pytest.mark.unit
class TestBuildObservationProgress:
    def test_includes_progress_section(self) -> None:
        """Progress log entries appear in observation text."""
        loop = _make_loop()
        loop._progress_log = [
            {
                "event": "action_progress",
                "action_id": "a1",
                "percent": 50.0,
                "message": "halfway done",
            },
            {
                "event": "action_progress",
                "action_id": "a1",
                "percent": 100.0,
                "message": "complete",
            },
        ]

        plan_result = {
            "status": "completed",
            "actions": [
                {
                    "action_id": "a1",
                    "status": "completed",
                    "module": "api_http",
                    "action": "download",
                    "result": {"path": "/tmp/file.zip"},
                }
            ],
        }
        steps = [PlanStep(id="a1", action="api_http__download", description="Download file")]

        text, screenshots = loop._build_observation(plan_result, steps)

        assert "### Progress Updates" in text
        assert "50.0%" in text
        assert "halfway done" in text
        assert "100.0%" in text

    def test_no_progress_section_when_empty(self) -> None:
        """No progress section when _progress_log is empty."""
        loop = _make_loop()
        loop._progress_log = []

        plan_result = {
            "status": "completed",
            "actions": [
                {
                    "action_id": "a1",
                    "status": "completed",
                    "module": "filesystem",
                    "action": "read_file",
                    "result": {"content": "hello"},
                }
            ],
        }
        steps = [PlanStep(id="a1", action="filesystem__read_file", description="Read file")]

        text, _ = loop._build_observation(plan_result, steps)

        assert "### Progress Updates" not in text

    def test_limits_to_last_10_entries(self) -> None:
        """Only the last 10 progress entries are shown."""
        loop = _make_loop()
        loop._progress_log = [
            {
                "event": "action_progress",
                "action_id": f"a{i}",
                "percent": float(i),
                "message": f"step {i}",
            }
            for i in range(20)
        ]

        plan_result = {"status": "completed", "actions": []}
        steps: list[PlanStep] = []

        text, _ = loop._build_observation(plan_result, steps)

        # Should contain entries 10-19 but not 0-9.
        assert "step 19" in text
        assert "step 10" in text
        # Earlier entries should NOT appear.
        assert "step 0" not in text
        assert "step 9" not in text
