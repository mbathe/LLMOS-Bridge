"""Unit tests — ExecutionState and PlanStateStore."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from llmos_bridge.orchestration.state import (
    ActionState,
    ExecutionState,
    PlanStateStore,
)
from llmos_bridge.protocol.models import ActionStatus, PlanStatus


# ---------------------------------------------------------------------------
# ExecutionState — rejection_details
# ---------------------------------------------------------------------------


class TestExecutionStateRejectionDetails:
    def test_default_none(self) -> None:
        state = ExecutionState(plan_id="p1")
        assert state.rejection_details is None

    def test_set_rejection_details(self) -> None:
        details = {
            "source": "scanner_pipeline",
            "verdict": "reject",
            "risk_score": 0.95,
            "threat_types": ["prompt_injection"],
        }
        state = ExecutionState(plan_id="p1", rejection_details=details)
        assert state.rejection_details == details

    def test_to_dict_excludes_none_rejection(self) -> None:
        state = ExecutionState(plan_id="p1")
        d = state.to_dict()
        assert "rejection_details" not in d

    def test_to_dict_includes_rejection(self) -> None:
        details = {
            "source": "intent_verifier",
            "verdict": "reject",
            "risk_level": "high",
        }
        state = ExecutionState(plan_id="p1", rejection_details=details)
        d = state.to_dict()
        assert d["rejection_details"] == details


# ---------------------------------------------------------------------------
# PlanStateStore — rejection_details persistence
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def store(tmp_path: Path) -> PlanStateStore:
    s = PlanStateStore(tmp_path / "test_state.db")
    await s.init()
    yield s
    await s.close()


@pytest.mark.asyncio
class TestPlanStateStoreRejection:
    async def test_create_without_rejection(self, store: PlanStateStore) -> None:
        state = ExecutionState(plan_id="p1")
        await store.create(state)
        loaded = await store.get("p1")
        assert loaded is not None
        assert loaded.rejection_details is None

    async def test_create_with_rejection(self, store: PlanStateStore) -> None:
        details = {
            "source": "scanner_pipeline",
            "verdict": "reject",
            "risk_score": 0.9,
            "threat_types": ["shell_injection"],
            "matched_patterns": ["shell_rm_rf"],
        }
        state = ExecutionState(plan_id="p2", rejection_details=details)
        await store.create(state)
        loaded = await store.get("p2")
        assert loaded is not None
        assert loaded.rejection_details == details

    async def test_update_status_with_rejection(self, store: PlanStateStore) -> None:
        state = ExecutionState(plan_id="p3")
        await store.create(state)

        details = {
            "source": "intent_verifier",
            "verdict": "reject",
            "risk_level": "critical",
            "reasoning": "Detected privilege escalation attempt.",
            "threats": [{"type": "privilege_escalation", "severity": "high"}],
            "recommendations": ["Remove the sudoers write action."],
        }
        await store.update_plan_status(
            "p3", PlanStatus.FAILED, rejection_details=details
        )

        loaded = await store.get("p3")
        assert loaded is not None
        assert loaded.plan_status == PlanStatus.FAILED
        assert loaded.rejection_details == details

    async def test_update_status_without_rejection(self, store: PlanStateStore) -> None:
        state = ExecutionState(plan_id="p4")
        await store.create(state)
        await store.update_plan_status("p4", PlanStatus.COMPLETED)

        loaded = await store.get("p4")
        assert loaded is not None
        assert loaded.plan_status == PlanStatus.COMPLETED
        assert loaded.rejection_details is None

    async def test_rejection_roundtrip_complex(self, store: PlanStateStore) -> None:
        """Complex rejection_details survives JSON serialization round-trip."""
        details = {
            "source": "scanner_pipeline",
            "verdict": "reject",
            "risk_score": 0.85,
            "threat_types": ["prompt_injection", "shell_injection"],
            "matched_patterns": ["pi_ignore_instructions", "shell_rm_rf"],
            "scanner_details": [
                {
                    "scanner_id": "heuristic",
                    "verdict": "reject",
                    "risk_score": 0.85,
                    "threat_types": ["prompt_injection", "shell_injection"],
                    "matched_patterns": ["pi_ignore_instructions", "shell_rm_rf"],
                    "details": "Matched 2 pattern(s)",
                }
            ],
            "recommendations": [
                "Review the plan description.",
                "Remove flagged elements.",
            ],
        }
        state = ExecutionState(plan_id="p5")
        await store.create(state)
        await store.update_plan_status(
            "p5", PlanStatus.FAILED, rejection_details=details
        )

        loaded = await store.get("p5")
        assert loaded is not None
        assert loaded.rejection_details == details
        assert loaded.rejection_details["scanner_details"][0]["scanner_id"] == "heuristic"
