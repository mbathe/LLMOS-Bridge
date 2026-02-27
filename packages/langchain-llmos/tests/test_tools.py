"""Tests for LLMOSActionTool and _json_schema_to_pydantic."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from langchain_llmos.tools import (
    LLMOSActionTool,
    _extract_action_result,
    _format_security_rejection,
    _json_schema_to_pydantic,
)


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


# ---------------------------------------------------------------------------
# _format_security_rejection
# ---------------------------------------------------------------------------


class TestFormatSecurityRejection:
    def test_scanner_pipeline_rejection(self) -> None:
        rejection = {
            "source": "scanner_pipeline",
            "verdict": "reject",
            "risk_score": 0.9,
            "threat_types": ["prompt_injection", "shell_injection"],
            "matched_patterns": ["pi_ignore_instructions", "shell_rm_rf"],
        }
        result = _format_security_rejection(rejection)
        assert result["status"] == "security_rejected"
        assert result["source"] == "scanner_pipeline"
        assert result["verdict"] == "reject"
        assert result["risk_score"] == 0.9
        assert "prompt_injection" in result["threat_summary"]
        assert "guidance" in result

    def test_intent_verifier_rejection(self) -> None:
        rejection = {
            "source": "intent_verifier",
            "verdict": "reject",
            "risk_level": "high",
            "reasoning": "Plan attempts privilege escalation via sudoers.",
            "threats": [
                {"type": "privilege_escalation", "severity": "high", "description": "Writes to /etc/sudoers"},
            ],
            "recommendations": ["Remove the sudoers write action."],
        }
        result = _format_security_rejection(rejection)
        assert result["status"] == "security_rejected"
        assert result["source"] == "intent_verifier"
        assert result["risk_level"] == "high"
        assert "privilege escalation" in result["threat_summary"]
        assert result["recommendations"] == ["Remove the sudoers write action."]

    def test_clarification_needed(self) -> None:
        rejection = {
            "source": "intent_verifier",
            "verdict": "clarify",
            "clarification_needed": "Does the user intend to delete system files?",
            "recommendations": [],
        }
        result = _format_security_rejection(rejection)
        assert result["status"] == "security_rejected"
        assert result["clarification_needed"] == "Does the user intend to delete system files?"
        assert "clarify" in result["guidance"].lower() or "clarif" in result["guidance"].lower()

    def test_many_patterns_truncated_in_summary(self) -> None:
        rejection = {
            "source": "scanner_pipeline",
            "verdict": "reject",
            "risk_score": 0.95,
            "threat_types": [],
            "matched_patterns": [f"pattern_{i}" for i in range(10)],
        }
        result = _format_security_rejection(rejection)
        assert "+5 more" in result["threat_summary"]

    def test_unknown_source(self) -> None:
        rejection = {"source": "custom_scanner", "verdict": "reject"}
        result = _format_security_rejection(rejection)
        assert result["status"] == "security_rejected"
        assert "security layer" in result["threat_summary"].lower()


# ---------------------------------------------------------------------------
# _extract_action_result — security rejection handling
# ---------------------------------------------------------------------------


class TestExtractActionResultSecurityRejection:
    def test_scanner_pipeline_rejection(self) -> None:
        plan_result = {
            "plan_id": "p1",
            "status": "failed",
            "message": "Plan finished with status: failed",
            "actions": [],
            "rejection_details": {
                "source": "scanner_pipeline",
                "verdict": "reject",
                "risk_score": 0.9,
                "threat_types": ["prompt_injection"],
                "matched_patterns": ["pi_ignore_instructions"],
                "recommendations": ["Review the plan."],
            },
        }
        result = _extract_action_result(plan_result)
        parsed = json.loads(result)
        assert parsed["status"] == "security_rejected"
        assert parsed["source"] == "scanner_pipeline"
        assert parsed["risk_score"] == 0.9
        assert len(parsed["recommendations"]) >= 1

    def test_intent_verifier_rejection(self) -> None:
        plan_result = {
            "plan_id": "p1",
            "status": "failed",
            "message": "Plan finished with status: failed",
            "actions": [],
            "rejection_details": {
                "source": "intent_verifier",
                "verdict": "reject",
                "risk_level": "critical",
                "reasoning": "Data exfiltration detected.",
                "threats": [],
                "recommendations": ["Do not send data to external URLs."],
            },
        }
        result = _extract_action_result(plan_result)
        parsed = json.loads(result)
        assert parsed["status"] == "security_rejected"
        assert parsed["source"] == "intent_verifier"
        assert parsed["risk_level"] == "critical"

    def test_failed_no_rejection_no_actions(self) -> None:
        plan_result = {
            "plan_id": "p1",
            "status": "failed",
            "message": "Module compatibility check failed.",
            "actions": [],
        }
        result = _extract_action_result(plan_result)
        parsed = json.loads(result)
        assert parsed["status"] == "failed"
        assert "Module compatibility" in parsed["error"]

    def test_rejection_takes_precedence_over_actions(self) -> None:
        """Even if actions exist, rejection_details should take priority."""
        plan_result = {
            "plan_id": "p1",
            "status": "failed",
            "actions": [{"action_id": "a1", "result": None, "error": "skipped"}],
            "rejection_details": {
                "source": "scanner_pipeline",
                "verdict": "reject",
                "risk_score": 0.8,
                "threat_types": ["encoding_attack"],
                "matched_patterns": ["enc_base64_long"],
            },
        }
        result = _extract_action_result(plan_result)
        parsed = json.loads(result)
        assert parsed["status"] == "security_rejected"
