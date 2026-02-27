"""Tests — SDK _extract_action_result permission and rate-limit error handling."""
from __future__ import annotations

import json

import pytest

from langchain_llmos.tools import _extract_action_result


class TestPermissionErrorHandling:
    def test_permission_error_returns_structured_recovery(self):
        plan_result = {
            "plan_id": "p1",
            "actions": [
                {
                    "action_id": "a1",
                    "error": "PermissionNotGrantedError: fs.write not granted",
                }
            ],
        }
        raw = _extract_action_result(plan_result)
        result = json.loads(raw)
        assert result["status"] == "permission_denied"
        assert "recovery" in result
        assert result["recovery"]["module"] == "security"

    def test_permission_keyword_in_error_also_detected(self):
        plan_result = {
            "actions": [
                {"error": "Action denied: insufficient permission for this resource"}
            ],
        }
        raw = _extract_action_result(plan_result)
        result = json.loads(raw)
        assert result["status"] == "permission_denied"
        assert "recovery" in result

    def test_rate_limit_error_returns_recovery_guidance(self):
        plan_result = {
            "actions": [
                {"error": "RateLimitExceededError: 60 calls/min exceeded"}
            ],
        }
        raw = _extract_action_result(plan_result)
        result = json.loads(raw)
        assert result["status"] == "rate_limited"
        assert "recovery" in result
        assert "wait" in result["recovery"]["guidance"].lower()

    def test_generic_error_has_no_recovery(self):
        plan_result = {
            "actions": [
                {"error": "FileNotFoundError: /tmp/missing.txt"}
            ],
        }
        raw = _extract_action_result(plan_result)
        result = json.loads(raw)
        assert "error" in result
        assert "recovery" not in result

    def test_awaiting_approval_returns_status(self):
        plan_result = {
            "plan_id": "p1",
            "actions": [
                {
                    "action_id": "a1",
                    "status": "awaiting_approval",
                    "clarification_options": ["approve", "reject"],
                }
            ],
        }
        raw = _extract_action_result(plan_result)
        result = json.loads(raw)
        assert result["status"] == "awaiting_approval"
        assert result["plan_id"] == "p1"
        assert result["clarification_options"] == ["approve", "reject"]

    def test_alternatives_included_in_error_response(self):
        plan_result = {
            "actions": [
                {
                    "error": "FileNotFoundError: missing",
                    "alternatives": [
                        {"action": "list_files", "module": "filesystem"}
                    ],
                }
            ],
        }
        raw = _extract_action_result(plan_result)
        result = json.loads(raw)
        assert len(result["alternatives"]) == 1
        assert result["alternatives"][0]["action"] == "list_files"
