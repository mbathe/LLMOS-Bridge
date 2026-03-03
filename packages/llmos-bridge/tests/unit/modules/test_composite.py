"""Tests for Module Spec v3 — Composition / Meta-Module pattern.

Tests the CompositeModule, PipelineStep, and declarative composition
of actions from multiple modules.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from llmos_bridge.modules.composite import (
    CompositeModule,
    PipelineStep,
)
from llmos_bridge.modules.manifest import ActionSpec


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_service_bus(results: dict[tuple[str, str], Any]) -> Any:
    """Create a mock ServiceBus that returns pre-configured results."""
    bus = MagicMock()

    async def mock_call(module: str, action: str, params: dict) -> Any:
        key = (module, action)
        if key in results:
            val = results[key]
            if callable(val):
                return val(params)
            return val
        raise RuntimeError(f"No mock result for {module}.{action}")

    bus.call = mock_call
    return bus


def _make_context(service_bus: Any) -> Any:
    ctx = MagicMock()
    ctx.service_bus = service_bus
    return ctx


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPipelineStep:
    def test_basic_step(self):
        step = PipelineStep(module="database", action="run_query")
        assert step.module == "database"
        assert step.action == "run_query"
        assert step.param_map == {}
        assert step.on_error == "abort"

    def test_step_with_param_map(self):
        step = PipelineStep(
            module="api_http",
            action="send_request",
            param_map={"url": "{{input.api_url}}", "body": "{{prev.result}}"},
        )
        assert "url" in step.param_map
        assert "body" in step.param_map

    def test_step_on_error_modes(self):
        assert PipelineStep(module="m", action="a", on_error="abort").on_error == "abort"
        assert PipelineStep(module="m", action="a", on_error="continue").on_error == "continue"
        assert PipelineStep(module="m", action="a", on_error="skip").on_error == "skip"


class TestCompositeModule:
    def test_build_creates_module(self):
        composite = CompositeModule.build(
            module_id="test_composite",
            version="1.0.0",
            description="Test composite",
            pipelines={
                "process": [
                    PipelineStep("mod_a", "action_1"),
                    PipelineStep("mod_b", "action_2"),
                ],
            },
        )
        assert composite.MODULE_ID == "test_composite"
        assert composite.VERSION == "1.0.0"

    def test_manifest_has_pipeline_actions(self):
        composite = CompositeModule.build(
            module_id="etl",
            description="ETL pipeline",
            pipelines={
                "extract": [PipelineStep("db", "query")],
                "load": [PipelineStep("db", "insert")],
            },
        )
        manifest = composite.get_manifest()
        names = manifest.action_names()
        assert "extract" in names
        assert "load" in names

    def test_dynamic_action_registered(self):
        composite = CompositeModule.build(
            module_id="test",
            pipelines={
                "my_action": [PipelineStep("mod", "act")],
            },
        )
        # Dynamic action should be accessible.
        handler = composite._get_handler("my_action")
        assert handler is not None

    @pytest.mark.asyncio
    async def test_execute_simple_pipeline(self):
        bus = _make_service_bus({
            ("db", "query"): {"rows": [1, 2, 3]},
            ("transform", "process"): lambda p: {"processed": True},
        })

        composite = CompositeModule.build(
            module_id="simple_pipe",
            pipelines={
                "run": [
                    PipelineStep("db", "query", param_map={"sql": "query_text"}),
                    PipelineStep("transform", "process", param_map={"data": "{{prev.result}}"}),
                ],
            },
        )
        composite.set_context(_make_context(bus))

        result = await composite.execute("run", {"query_text": "SELECT *"})
        assert result["success"] is True
        assert result["completed_steps"] == 2
        assert result["final_result"]["processed"] is True

    @pytest.mark.asyncio
    async def test_execute_pipeline_with_input_template(self):
        bus = _make_service_bus({
            ("api", "get"): lambda p: {"data": p.get("url", "")},
        })

        composite = CompositeModule.build(
            module_id="input_pipe",
            pipelines={
                "fetch": [
                    PipelineStep("api", "get", param_map={"url": "{{input.target_url}}"}),
                ],
            },
        )
        composite.set_context(_make_context(bus))

        result = await composite.execute("fetch", {"target_url": "https://example.com"})
        assert result["success"] is True
        assert result["final_result"]["data"] == "https://example.com"

    @pytest.mark.asyncio
    async def test_pipeline_abort_on_failure(self):
        bus = _make_service_bus({
            ("failing", "op"): RuntimeError("Step failed"),
        })

        # Override to make it raise
        original_call = bus.call

        async def failing_call(module, action, params):
            if module == "failing":
                raise RuntimeError("Step failed")
            return await original_call(module, action, params)

        bus.call = failing_call

        composite = CompositeModule.build(
            module_id="abort_pipe",
            pipelines={
                "run": [
                    PipelineStep("failing", "op", on_error="abort"),
                    PipelineStep("never", "reached"),
                ],
            },
        )
        composite.set_context(_make_context(bus))

        result = await composite.execute("run", {})
        assert result["success"] is False
        assert result["completed_steps"] == 0
        assert "Step failed" in result["error"]

    @pytest.mark.asyncio
    async def test_pipeline_continue_on_failure(self):
        call_count = 0

        async def mock_call(module, action, params):
            nonlocal call_count
            call_count += 1
            if module == "failing":
                raise RuntimeError("Expected error")
            return {"ok": True}

        bus = MagicMock()
        bus.call = mock_call

        composite = CompositeModule.build(
            module_id="continue_pipe",
            pipelines={
                "run": [
                    PipelineStep("failing", "op", on_error="continue"),
                    PipelineStep("success", "op"),
                ],
            },
        )
        composite.set_context(_make_context(bus))

        result = await composite.execute("run", {})
        assert result["success"] is True
        assert result["completed_steps"] == 2
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_no_context_raises(self):
        from llmos_bridge.exceptions import ActionExecutionError

        composite = CompositeModule.build(
            module_id="no_ctx",
            pipelines={
                "run": [PipelineStep("mod", "act")],
            },
        )
        # No context set → raises ActionExecutionError (wrapping RuntimeError).
        with pytest.raises(ActionExecutionError, match="requires ModuleContext"):
            await composite.execute("run", {})

    def test_resolve_prev_result(self):
        composite = CompositeModule.build(
            module_id="test", pipelines={}
        )
        step = PipelineStep("m", "a", param_map={"data": "{{prev.result}}"})
        resolved = composite._resolve_step_params(step, {}, {"key": "value"})
        assert resolved["data"] == {"key": "value"}

    def test_resolve_prev_result_field(self):
        composite = CompositeModule.build(
            module_id="test", pipelines={}
        )
        step = PipelineStep("m", "a", param_map={"name": "{{prev.result.name}}"})
        resolved = composite._resolve_step_params(step, {}, {"name": "Alice", "age": 30})
        assert resolved["name"] == "Alice"
