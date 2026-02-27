"""API layer — Request and response schemas.

These are the external API contracts.  They are intentionally separate from
the internal IML protocol models to allow API versioning without coupling.
"""

from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, Field

from llmos_bridge.protocol.models import ActionStatus, PlanStatus


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


class SubmitPlanRequest(BaseModel):
    """POST /plans — Submit an IML plan for execution."""

    plan: dict[str, Any] = Field(description="The IML plan payload.")
    async_execution: bool = Field(
        default=True,
        description=(
            "If True, return immediately with plan_id. "
            "If False, block until plan completes (max 300s)."
        ),
    )


class ApprovePlanActionRequest(BaseModel):
    """POST /plans/{plan_id}/actions/{action_id}/approve

    Supports rich approval decisions beyond simple approve/reject.
    """

    decision: str = Field(
        default="approve",
        description=(
            "Approval decision: 'approve', 'reject', 'skip', 'modify', 'approve_always'. "
            "Legacy: set 'approved=true/false' as a shorthand for approve/reject."
        ),
    )
    approved: bool | None = Field(
        default=None,
        description="Legacy field — mapped to decision='approve'/'reject'. Use 'decision' instead.",
    )
    reason: str | None = None
    modified_params: dict[str, Any] | None = Field(
        default=None,
        description="Modified params — only used when decision='modify'.",
    )
    approved_by: str | None = Field(
        default=None,
        description="Identifier of the user who made the decision.",
    )


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class ActionResponse(BaseModel):
    action_id: str
    module: str
    action: str
    status: ActionStatus
    started_at: float | None = None
    finished_at: float | None = None
    result: Any = None
    error: str | None = None
    alternatives: list[str] = Field(
        default_factory=list,
        description="Actionable alternatives suggested on failure (Negotiation Protocol).",
    )
    attempt: int = 0
    approval_metadata: dict[str, Any] | None = None


class PlanResponse(BaseModel):
    plan_id: str
    status: PlanStatus
    description: str | None = None
    created_at: float
    updated_at: float
    actions: list[ActionResponse] = Field(default_factory=list)
    rejection_details: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Structured details when a plan was rejected by the security scanner "
            "pipeline or intent verifier. Includes threat types, risk scores, "
            "and recommendations."
        ),
    )


class SubmitPlanResponse(BaseModel):
    plan_id: str
    status: PlanStatus
    message: str
    actions: list[ActionResponse] = Field(
        default_factory=list,
        description=(
            "Action results — populated when async_execution=false and the "
            "plan completes. Empty for async submissions."
        ),
    )
    rejection_details: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Structured details when a plan was rejected by the security scanner "
            "pipeline or intent verifier. Includes threat types, risk scores, "
            "and recommendations."
        ),
    )


class ModuleActionSchema(BaseModel):
    name: str
    description: str
    params_schema: dict[str, Any]
    returns: str
    permission_required: str
    platforms: list[str]
    examples: list[dict[str, Any]] = Field(default_factory=list)


class ModuleManifestResponse(BaseModel):
    module_id: str
    version: str
    description: str
    platforms: list[str]
    actions: list[ModuleActionSchema]
    tags: list[str] = Field(default_factory=list)


class ModuleStatusDetail(BaseModel):
    available: list[str] = Field(default_factory=list)
    failed: dict[str, str] = Field(default_factory=dict)
    platform_excluded: dict[str, str] = Field(default_factory=dict)


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str
    protocol_version: str
    uptime_seconds: float
    modules_loaded: int
    modules_failed: int
    modules: ModuleStatusDetail | None = Field(
        default=None,
        description="Per-module status breakdown (available, failed, excluded).",
    )
    active_plans: int = Field(default=0, description="Number of plans currently running.")
    scanner_pipeline: dict[str, str] | None = Field(
        default=None,
        description="Per-scanner status (scanner_id -> enabled/disabled).",
    )
    timestamp: float = Field(default_factory=time.time)


class ErrorResponse(BaseModel):
    error: str
    code: str
    detail: Any | None = None
    request_id: str | None = None


class PlanListResponse(BaseModel):
    plans: list[dict[str, Any]]
    total: int
    page: int
    per_page: int


class SessionResponse(BaseModel):
    session_id: str
    created_at: float
    plan_count: int


# ---------------------------------------------------------------------------
# Approval schemas
# ---------------------------------------------------------------------------


class ApprovalRequestResponse(BaseModel):
    """Describes a pending approval request for UI/SDK consumption."""

    plan_id: str
    action_id: str
    module: str
    action: str
    params: dict[str, Any]
    risk_level: str
    description: str
    requires_approval_reason: str
    clarification_options: list[str] = Field(
        default_factory=list,
        description="Structured choices for intent clarification (if non-empty).",
    )
    requested_at: float


class ApprovalDecisionResponse(BaseModel):
    """Response after submitting an approval decision."""

    plan_id: str
    action_id: str
    decision: str
    applied: bool


# ---------------------------------------------------------------------------
# WebSocket message schemas
# ---------------------------------------------------------------------------


class WSMessage(BaseModel):
    type: str  # "plan_status" | "action_status" | "approval_request" | "error"
    payload: dict[str, Any]
    timestamp: float = Field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Plan Group schemas
# ---------------------------------------------------------------------------


class SubmitPlanGroupRequest(BaseModel):
    """POST /plan-groups — Submit multiple plans for parallel execution."""

    group_id: str | None = Field(
        default=None, description="Optional group ID (generated if omitted)."
    )
    plans: list[dict[str, Any]] = Field(
        min_length=1, max_length=50, description="List of IML plan payloads."
    )
    max_concurrent: int = Field(default=10, ge=1, le=50)
    timeout: int = Field(
        default=300, ge=10, le=3600, description="Total timeout for the group (seconds)."
    )


class PlanGroupResponse(BaseModel):
    """Response from plan group execution."""

    group_id: str
    status: str  # "completed" | "partial_failure" | "failed"
    summary: dict[str, int]  # {total, completed, failed}
    results: dict[str, Any]
    errors: dict[str, str]
    duration: float
