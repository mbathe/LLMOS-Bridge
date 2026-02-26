"""Tests for LLMOSActionTool and _json_schema_to_pydantic."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from langchain_llmos.tools import LLMOSActionTool, _extract_action_result, _json_schema_to_pydantic


# ---------------------------------------------------------------------------
# _json_schema_to_pydantic
# ---------------------------------------------------------------------------


class TestJsonSchemaToPydantic:
    def test_string_field_required(self) -> None:
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Name"}},
            "required": ["name"],
        }
        Model = _json_schema_to_pydantic(schema, "TestModel")
        assert "name" in Model.model_fields
        instance = Model(name="test")
        assert instance.name == "test"

    def test_optional_field_with_default(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "encoding": {"type": "string", "description": "Enc", "default": "utf-8"},
            },
            "required": [],
        }
        Model = _json_schema_to_pydantic(schema, "TestModel2")
        instance = Model()
        assert instance.encoding == "utf-8"

    def test_multiple_types(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "count": {"type": "integer"},
                "ratio": {"type": "number"},
                "active": {"type": "boolean"},
                "data": {"type": "object"},
                "items": {"type": "array"},
            },
            "required": ["name", "count", "ratio", "active", "data", "items"],
        }
        Model = _json_schema_to_pydantic(schema, "MultiType")
        instance = Model(
            name="x", count=1, ratio=0.5, active=True, data={}, items=[]
        )
        assert instance.count == 1

    def test_empty_schema(self) -> None:
        Model = _json_schema_to_pydantic({}, "Empty")
        instance = Model()
        assert instance is not None

    def test_optional_field_none_default(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "tag": {"type": "string", "description": "Optional tag."},
            },
        }
        Model = _json_schema_to_pydantic(schema, "OptModel")
        instance = Model()
        assert instance.tag is None


# ---------------------------------------------------------------------------
# _extract_action_result
# ---------------------------------------------------------------------------


class TestExtractActionResult:
    def test_extracts_single_action_result(self) -> None:
        plan_result = {
            "plan_id": "p1",
            "status": "completed",
            "actions": [
                {"action_id": "a1", "result": {"content": "hello"}, "error": None}
            ],
        }
        result = _extract_action_result(plan_result)
        parsed = json.loads(result)
        assert parsed == {"content": "hello"}

    def test_extracts_error(self) -> None:
        plan_result = {
            "plan_id": "p1",
            "status": "failed",
            "actions": [
                {"action_id": "a1", "result": None, "error": "file not found"}
            ],
        }
        result = _extract_action_result(plan_result)
        parsed = json.loads(result)
        assert parsed == {"error": "file not found"}

    def test_returns_full_plan_when_no_actions(self) -> None:
        plan_result = {"plan_id": "p1", "status": "completed", "actions": []}
        result = _extract_action_result(plan_result)
        parsed = json.loads(result)
        assert parsed["plan_id"] == "p1"

    def test_returns_full_plan_for_multi_action(self) -> None:
        plan_result = {
            "plan_id": "p1",
            "status": "completed",
            "actions": [
                {"action_id": "a1", "result": "r1", "error": None},
                {"action_id": "a2", "result": "r2", "error": None},
            ],
        }
        result = _extract_action_result(plan_result)
        parsed = json.loads(result)
        assert parsed["plan_id"] == "p1"


# ---------------------------------------------------------------------------
# LLMOSActionTool
# ---------------------------------------------------------------------------


class TestLLMOSActionTool:
    def test_build_plan(self) -> None:
        tool = LLMOSActionTool(
            name="filesystem__read_file",
            description="Read file",
            module_id="filesystem",
            action_name="read_file",
            client=MagicMock(),
        )
        plan = tool._build_plan({"path": "/tmp/test.txt"})
        assert plan["protocol_version"] == "2.0"
        assert len(plan["actions"]) == 1
        assert plan["actions"][0]["module"] == "filesystem"
        assert plan["actions"][0]["action"] == "read_file"
        assert plan["actions"][0]["params"]["path"] == "/tmp/test.txt"

    def test_run_calls_submit_plan(self) -> None:
        mock_client = MagicMock()
        mock_client.submit_plan.return_value = {
            "plan_id": "p1",
            "status": "completed",
            "actions": [{"action_id": "a1", "result": {"content": "data"}, "error": None}],
        }
        tool = LLMOSActionTool(
            name="filesystem__read_file",
            description="Read file",
            module_id="filesystem",
            action_name="read_file",
            client=mock_client,
        )
        result = tool._run(path="/tmp/test.txt")
        mock_client.submit_plan.assert_called_once()
        call_args = mock_client.submit_plan.call_args
        assert call_args[1]["async_execution"] is False
        parsed = json.loads(result)
        assert parsed == {"content": "data"}

    def test_plan_has_unique_id(self) -> None:
        tool = LLMOSActionTool(
            name="test",
            description="test",
            module_id="test",
            action_name="do",
            client=MagicMock(),
        )
        plan1 = tool._build_plan({})
        plan2 = tool._build_plan({})
        assert plan1["plan_id"] != plan2["plan_id"]
