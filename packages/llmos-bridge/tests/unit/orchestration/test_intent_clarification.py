"""Unit tests — Intent Clarification (clarification_options in approval flow)."""

from __future__ import annotations

import time
from typing import Any

import pytest

from llmos_bridge.orchestration.approval import ApprovalRequest
from llmos_bridge.protocol.models import ApprovalConfig


# ---------------------------------------------------------------------------
# ApprovalConfig.clarification_options
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestApprovalConfigClarification:
    """Test clarification_options field on ApprovalConfig."""

    def test_default_empty(self) -> None:
        cfg = ApprovalConfig()
        assert cfg.clarification_options == []

    def test_custom_options(self) -> None:
        cfg = ApprovalConfig(
            clarification_options=[
                "Delete file permanently",
                "Move to trash instead",
                "Cancel the operation",
            ]
        )
        assert len(cfg.clarification_options) == 3
        assert "Delete file permanently" in cfg.clarification_options

    def test_round_trips_through_model_dump(self) -> None:
        cfg = ApprovalConfig(
            message="Confirm deletion",
            risk_level="high",
            clarification_options=["Yes, delete", "No, keep"],
        )
        d = cfg.model_dump()
        assert d["clarification_options"] == ["Yes, delete", "No, keep"]
        restored = ApprovalConfig(**d)
        assert restored.clarification_options == cfg.clarification_options


# ---------------------------------------------------------------------------
# ApprovalRequest.clarification_options + to_dict
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestApprovalRequestClarification:
    """Test clarification_options field on ApprovalRequest."""

    def test_default_empty_list(self) -> None:
        req = ApprovalRequest(
            plan_id="p1",
            action_id="a1",
            module="filesystem",
            action_name="delete_file",
            params={"path": "/tmp/x"},
        )
        assert req.clarification_options == []

    def test_custom_options(self) -> None:
        req = ApprovalRequest(
            plan_id="p1",
            action_id="a1",
            module="filesystem",
            action_name="delete_file",
            params={"path": "/tmp/x"},
            clarification_options=["Delete permanently", "Move to trash"],
        )
        assert req.clarification_options == ["Delete permanently", "Move to trash"]

    def test_to_dict_excludes_when_empty(self) -> None:
        """clarification_options should NOT appear in dict when empty."""
        req = ApprovalRequest(
            plan_id="p1",
            action_id="a1",
            module="filesystem",
            action_name="read_file",
            params={},
        )
        d = req.to_dict()
        assert "clarification_options" not in d

    def test_to_dict_includes_when_populated(self) -> None:
        options = ["Option A", "Option B", "Option C"]
        req = ApprovalRequest(
            plan_id="p1",
            action_id="a1",
            module="os_exec",
            action_name="run_command",
            params={"command": ["ls"]},
            clarification_options=options,
        )
        d = req.to_dict()
        assert "clarification_options" in d
        assert d["clarification_options"] == options

    def test_to_dict_standard_fields_present(self) -> None:
        """Verify the standard fields are still present."""
        req = ApprovalRequest(
            plan_id="p1",
            action_id="a1",
            module="filesystem",
            action_name="write_file",
            params={"path": "/tmp/x", "content": "data"},
            risk_level="high",
            description="Write to sensitive path",
        )
        d = req.to_dict()
        assert d["plan_id"] == "p1"
        assert d["action_id"] == "a1"
        assert d["module"] == "filesystem"
        assert d["action"] == "write_file"
        assert d["risk_level"] == "high"
        assert d["description"] == "Write to sensitive path"
        assert "requested_at" in d


# ---------------------------------------------------------------------------
# IMLAction approval with clarification_options
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIMLActionClarification:
    """Test clarification_options flowing from IMLAction.approval config."""

    def test_action_with_clarification(self) -> None:
        from llmos_bridge.protocol.models import IMLAction

        action = IMLAction(
            id="a1",
            action="delete_file",
            module="filesystem",
            params={"path": "/tmp/x"},
            requires_approval=True,
            approval=ApprovalConfig(
                message="About to delete a file",
                risk_level="high",
                clarification_options=["Delete permanently", "Move to trash"],
            ),
        )
        assert action.approval is not None
        assert action.approval.clarification_options == [
            "Delete permanently",
            "Move to trash",
        ]

    def test_action_without_clarification(self) -> None:
        from llmos_bridge.protocol.models import IMLAction

        action = IMLAction(
            id="a1",
            action="read_file",
            module="filesystem",
            params={"path": "/tmp/x"},
            requires_approval=True,
            approval=ApprovalConfig(message="Read a file"),
        )
        assert action.approval.clarification_options == []

    def test_action_no_approval_config(self) -> None:
        from llmos_bridge.protocol.models import IMLAction

        action = IMLAction(
            id="a1",
            action="read_file",
            module="filesystem",
            params={"path": "/tmp/x"},
        )
        assert action.approval is None
