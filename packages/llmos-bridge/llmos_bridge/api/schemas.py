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
    os_permissions: list[str] = Field(
        default_factory=list,
        description="OS-level permission strings required by this action (from @requires_permission).",
    )


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


# ---------------------------------------------------------------------------
# Admin schemas
# ---------------------------------------------------------------------------

class InstallModuleRequest(BaseModel):
    """POST /admin/hub/install — Install a module."""
    source: str = Field(default="hub", description="'hub' or 'local'.")
    module_id: str = Field(default="", description="Module ID (for hub install).")
    path: str = Field(default="", description="Local path (for local install).")
    version: str = Field(default="latest", description="Version constraint.")

class ConfigUpdateRequest(BaseModel):
    """PUT /admin/modules/{id}/config — Update module config."""
    config: dict[str, Any] = Field(description="Configuration dict to apply.")

class PermissionGrantRequest(BaseModel):
    """POST /admin/security/permissions/grant."""
    permission: str
    module_id: str
    reason: str = ""
    scope: str = Field(default="session", description="'session' or 'permanent'.")

class PermissionRevokeRequest(BaseModel):
    """DELETE /admin/security/permissions/revoke."""
    permission: str
    module_id: str

class AppPermissionGrantRequest(BaseModel):
    """POST /applications/{app_id}/permissions/grant — app-scoped permission grant."""
    permission: str = Field(description="Permission string, e.g. 'filesystem.write'.")
    module_id: str = Field(description="Module ID this permission applies to.")
    reason: str = Field(default="", description="Reason for the grant.")
    scope: str = Field(default="permanent", description="'session' or 'permanent'.")

class AppPermissionRevokeRequest(BaseModel):
    """POST /applications/{app_id}/permissions/revoke — app-scoped permission revoke."""
    permission: str = Field(description="Permission string to revoke.")
    module_id: str = Field(description="Module ID this permission applies to.")

class ActionToggleRequest(BaseModel):
    """POST /admin/modules/{id}/actions/{action}/disable."""
    reason: str = Field(default="", description="Reason for disabling.")

class UpgradeModuleRequest(BaseModel):
    """POST /admin/hub/modules/{id}/upgrade."""
    path: str = Field(description="Path to new version package directory.")


class IntentTestRequest(BaseModel):
    """POST /admin/security/intent-verifier/test."""
    text: str = Field(description="Plan text to test against the intent verifier.")


class PatternAddRequest(BaseModel):
    """POST /admin/security/scanners/patterns — add a custom heuristic pattern."""
    id: str = Field(description="Unique pattern identifier.")
    category: str = Field(description="Threat category (e.g. 'prompt_injection').")
    pattern: str = Field(description="Regex pattern string.")
    severity: float = Field(default=0.5, ge=0.0, le=1.0, description="Risk score 0.0-1.0.")
    description: str = Field(default="", description="Human-readable description.")


# ---------------------------------------------------------------------------
# Identity / Application schemas
# ---------------------------------------------------------------------------


class CreateApplicationRequest(BaseModel):
    """POST /applications — Create an application."""
    name: str = Field(description="Unique application name.")
    description: str = Field(default="", description="Application description.")
    max_concurrent_plans: int = Field(default=10, ge=1, le=100)
    max_actions_per_plan: int = Field(default=50, ge=1, le=500)
    allowed_modules: list[str] = Field(default_factory=list)
    allowed_actions: dict[str, list[str]] = Field(
        default_factory=dict,
        description=(
            "Per-module action whitelist. Empty = all actions allowed. "
            "Format: {'module_id': ['action1', 'action2']}."
        ),
    )
    tags: dict[str, str] = Field(default_factory=dict)


class UpdateApplicationRequest(BaseModel):
    """PUT /applications/{app_id} — Update an application."""
    name: str | None = None
    description: str | None = None
    enabled: bool | None = None
    max_concurrent_plans: int | None = Field(default=None, ge=1, le=100)
    max_actions_per_plan: int | None = Field(default=None, ge=1, le=500)
    allowed_modules: list[str] | None = None
    allowed_actions: dict[str, list[str]] | None = None
    tags: dict[str, str] | None = None


class ApplicationResponse(BaseModel):
    """Application detail response."""
    app_id: str
    name: str
    description: str
    created_at: float
    updated_at: float
    enabled: bool
    max_concurrent_plans: int
    max_actions_per_plan: int
    allowed_modules: list[str]
    allowed_actions: dict[str, list[str]] = Field(default_factory=dict)
    tags: dict[str, str]
    agent_count: int = 0
    session_count: int = 0


class CreateAgentRequest(BaseModel):
    """POST /applications/{app_id}/agents — Create an agent."""
    name: str = Field(description="Agent display name.")
    role: str = Field(default="agent", description="RBAC role: admin, app_admin, operator, viewer, agent.")


class AgentResponse(BaseModel):
    """Agent detail response."""
    agent_id: str
    name: str
    app_id: str
    role: str
    created_at: float
    enabled: bool


class ApiKeyResponse(BaseModel):
    """API key response — cleartext only available at creation."""
    key_id: str
    prefix: str
    api_key: str | None = None
    created_at: float
    expires_at: float | None = None


class SessionResponse(BaseModel):
    """Session detail response."""
    session_id: str
    app_id: str
    agent_id: str | None
    created_at: float
    last_active: float
    expires_at: float | None = None
    idle_timeout_seconds: int | None = None
    allowed_modules: list[str] = Field(default_factory=list)
    permission_grants: list[str] = Field(default_factory=list)
    permission_denials: list[str] = Field(default_factory=list)
    expired: bool = False


class CreateSessionRequest(BaseModel):
    """POST /applications/{app_id}/sessions — Create a session with optional constraints."""
    agent_id: str | None = None
    expires_in_seconds: float | None = Field(
        default=None,
        description="Seconds from now until session expires. None = no expiry.",
    )
    idle_timeout_seconds: int | None = Field(
        default=None,
        description="Seconds of inactivity before auto-expiry. None = no idle timeout.",
    )
    allowed_modules: list[str] = Field(
        default_factory=list,
        description="Session-level module whitelist (subset of app's allowed_modules). Empty = inherit all.",
    )
    permission_grants: list[str] = Field(
        default_factory=list,
        description="OS permissions temporarily granted for this session.",
    )
    permission_denials: list[str] = Field(
        default_factory=list,
        description="OS permissions explicitly blocked for this session.",
    )


class ClusterResponse(BaseModel):
    """GET /cluster — Cluster information."""
    cluster_id: str
    cluster_name: str
    node_id: str
    mode: str
    app_count: int = 0
    identity_enabled: bool = False


class NodeResponse(BaseModel):
    """Node detail response (Phase 2 + Phase 4 routing fields)."""
    node_id: str
    url: str | None = None
    location: str = ""
    available: bool = True
    last_heartbeat: float | None = None
    modules: list[str] = Field(default_factory=list)
    is_local: bool = False
    latency_ms: float | None = None
    active_actions: int = 0
    quarantined: bool = False


class NodeRegisterRequest(BaseModel):
    """POST /nodes — Register a remote node."""
    node_id: str = Field(description="Unique node identifier.")
    url: str = Field(description="Base URL of the remote daemon (e.g. 'http://192.168.1.50:40000').")
    api_token: str | None = Field(default=None, description="API token for the remote daemon.")
    location: str = Field(default="", description="Human-readable location.")


class ClusterHealthResponse(BaseModel):
    """GET /cluster/health — Cluster health overview."""
    total_nodes: int
    available_nodes: int
    unavailable_nodes: int
    nodes: list[NodeResponse] = Field(default_factory=list)
