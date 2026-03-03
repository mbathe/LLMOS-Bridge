"""Identity layer — Pydantic models for the multi-tenant hierarchy.

Hierarchy:
    Cluster (1 orchestrator)
      └─ Applications (logical isolation boundary, like k8s namespaces)
          └─ Sessions (temporal grouping within an application)
              └─ Agents (machine or human identities that submit plans)

All fields use sensible defaults so that the identity system is a strict
no-op when ``identity.enabled=False`` (standalone mode).
"""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Role-Based Access Control
# ---------------------------------------------------------------------------


class Role(str, Enum):
    """RBAC roles for the identity system.

    Ordered from most to least privileged.
    """

    ADMIN = "admin"
    """Full access to all applications and system configuration."""

    APP_ADMIN = "app_admin"
    """Full access within one application."""

    OPERATOR = "operator"
    """Submit plans, view state, approve actions within one application."""

    VIEWER = "viewer"
    """Read-only access to plans and module state."""

    AGENT = "agent"
    """Machine identity for LLM callers (SDK, langchain, CLI)."""


# ---------------------------------------------------------------------------
# Cluster
# ---------------------------------------------------------------------------


class ClusterInfo(BaseModel):
    """Describes the cluster this node belongs to."""

    cluster_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str = Field(default="default")
    created_at: float = Field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------


class Application(BaseModel):
    """Logical isolation boundary (like a Kubernetes namespace).

    All plans, permissions, and sessions are scoped to an application.
    When the identity system is disabled, everything uses the implicit
    ``"default"`` application.
    """

    app_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    description: str = ""
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    enabled: bool = True
    max_concurrent_plans: int = Field(default=10, ge=1, le=100)
    max_actions_per_plan: int = Field(default=50, ge=1, le=500)
    allowed_modules: list[str] = Field(
        default_factory=list,
        description="Empty = all modules allowed. Non-empty = whitelist.",
    )
    allowed_actions: dict[str, list[str]] = Field(
        default_factory=dict,
        description=(
            "Per-module action whitelist. Empty dict = all actions allowed for allowed modules. "
            "Format: {'module_id': ['action1', 'action2']}. "
            "Actions not listed are denied when the module has an entry."
        ),
    )
    tags: dict[str, str] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------


class Session(BaseModel):
    """Temporal grouping of plans within an application.

    Authorization layers (most restrictive wins):
      1. Application constraints (allowed_modules, allowed_actions, OS grants)
      2. Session constraints (subset of app's allowed_modules, session-scoped OS grants/denials)

    A session can only *restrict* the application's access — it cannot expand it.
    """

    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    app_id: str
    agent_id: str | None = None
    created_at: float = Field(default_factory=time.time)
    last_active: float = Field(default_factory=time.time)
    expires_at: float | None = Field(
        default=None,
        description=(
            "UTC timestamp after which this session is considered expired and rejected. "
            "None = no expiry."
        ),
    )
    idle_timeout_seconds: int | None = Field(
        default=None,
        description=(
            "Seconds of inactivity before the session auto-expires. "
            "Checked against last_active. None = no idle timeout."
        ),
    )
    allowed_modules: list[str] = Field(
        default_factory=list,
        description=(
            "Session-level module whitelist (subset of app's allowed_modules). "
            "Empty = inherit all of the application's allowed modules."
        ),
    )
    permission_grants: list[str] = Field(
        default_factory=list,
        description=(
            "OS permission strings temporarily granted for this session only. "
            "Cleared automatically when the session ends. "
            "Must be a subset of the application's OS permission grants."
        ),
    )
    permission_denials: list[str] = Field(
        default_factory=list,
        description=(
            "OS permission strings explicitly blocked for this session. "
            "Take precedence over both app-level and session-level grants."
        ),
    )
    metadata: dict[str, Any] = Field(default_factory=dict)

    def is_expired(self) -> bool:
        """Return True if the session has expired (absolute or idle timeout)."""
        now = time.time()
        if self.expires_at is not None and now > self.expires_at:
            return True
        if (
            self.idle_timeout_seconds is not None
            and now - self.last_active > self.idle_timeout_seconds
        ):
            return True
        return False


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class Agent(BaseModel):
    """Machine or human identity that submits plans.

    Agents are scoped to a single application and carry a role for RBAC.
    """

    agent_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    app_id: str
    role: Role = Role.AGENT
    created_at: float = Field(default_factory=time.time)
    enabled: bool = True
    metadata: dict[str, str] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# API Key
# ---------------------------------------------------------------------------


class ApiKey(BaseModel):
    """An API key bound to an agent.

    Keys are stored as bcrypt hashes.  The cleartext key is only returned
    once at creation time (via ``ApiKeyResponse.api_key``).
    """

    key_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    agent_id: str
    app_id: str
    prefix: str = ""
    key_hash: str = ""
    created_at: float = Field(default_factory=time.time)
    expires_at: float | None = None
    revoked: bool = False


# ---------------------------------------------------------------------------
# Runtime identity context (resolved per-request)
# ---------------------------------------------------------------------------


class IdentityContext(BaseModel):
    """Resolved caller identity for a single API request.

    Populated by ``IdentityResolver`` from the request headers.
    When ``identity.enabled=False`` this always returns the default
    context: ``app_id="default", agent_id=None, role=ADMIN``.
    """

    app_id: str = "default"
    agent_id: str | None = None
    session_id: str | None = None
    role: Role = Role.ADMIN
