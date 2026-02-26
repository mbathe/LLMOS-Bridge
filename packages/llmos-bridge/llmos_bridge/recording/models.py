"""Shadow Recorder — data models.

WorkflowRecording is the top-level container for a named recording session.
Each time a plan completes during a recording, a RecordedPlan is appended.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class RecordingStatus(str, Enum):
    ACTIVE = "active"
    STOPPED = "stopped"


@dataclass
class RecordedPlan:
    """One plan execution captured in a recording."""

    plan_id: str
    sequence: int        # 1-based position in the recording
    added_at: float
    plan_data: dict[str, Any]   # original IML plan dict
    final_status: str           # PlanStatus.value
    action_count: int


@dataclass
class WorkflowRecording:
    """A named recording session capturing a sequence of plan executions."""

    recording_id: str
    title: str
    description: str
    status: RecordingStatus
    created_at: float
    stopped_at: float | None
    plans: list[RecordedPlan] = field(default_factory=list)
    generated_plan: dict[str, Any] | None = None  # IMLPlan generated on stop

    @classmethod
    def create(cls, title: str, description: str = "") -> "WorkflowRecording":
        return cls(
            recording_id=f"rec-{uuid.uuid4().hex[:12]}",
            title=title,
            description=description,
            status=RecordingStatus.ACTIVE,
            created_at=time.time(),
            stopped_at=None,
        )

    def to_summary_dict(self) -> dict[str, Any]:
        return {
            "recording_id": self.recording_id,
            "title": self.title,
            "description": self.description,
            "status": self.status.value,
            "created_at": self.created_at,
            "stopped_at": self.stopped_at,
            "plan_count": len(self.plans),
        }

    def to_full_dict(self) -> dict[str, Any]:
        d = self.to_summary_dict()
        d["plans"] = [
            {
                "plan_id": p.plan_id,
                "sequence": p.sequence,
                "added_at": p.added_at,
                "final_status": p.final_status,
                "action_count": p.action_count,
            }
            for p in self.plans
        ]
        d["generated_plan"] = self.generated_plan
        return d
