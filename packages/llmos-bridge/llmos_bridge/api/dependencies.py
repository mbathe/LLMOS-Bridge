"""API layer — FastAPI dependency injection.

All heavy objects (registry, store, guard, etc.) are created once at startup
and injected via FastAPI's dependency system.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, Header, HTTPException, Request, status

from llmos_bridge.config import Settings, get_settings
from llmos_bridge.modules.registry import ModuleRegistry
from llmos_bridge.orchestration.approval import ApprovalGate
from llmos_bridge.orchestration.executor import PlanExecutor
from llmos_bridge.orchestration.state import PlanStateStore
from llmos_bridge.protocol.constants import HEADER_API_TOKEN
from llmos_bridge.security.audit import AuditLogger
from llmos_bridge.security.guard import PermissionGuard


def get_module_registry(request: Request) -> ModuleRegistry:
    return request.app.state.module_registry  # type: ignore[no-any-return]


def get_state_store(request: Request) -> PlanStateStore:
    return request.app.state.state_store  # type: ignore[no-any-return]


def get_permission_guard(request: Request) -> PermissionGuard:
    return request.app.state.permission_guard  # type: ignore[no-any-return]


def get_audit_logger(request: Request) -> AuditLogger:
    return request.app.state.audit_logger  # type: ignore[no-any-return]


def get_plan_executor(request: Request) -> PlanExecutor:
    return request.app.state.plan_executor  # type: ignore[no-any-return]


def get_config(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]


def get_approval_gate(request: Request) -> ApprovalGate:
    """Return the ApprovalGate for signaling approval decisions."""
    return request.app.state.approval_gate  # type: ignore[no-any-return]


def get_recorder(request: Request) -> "WorkflowRecorder | None":
    """Return WorkflowRecorder if recording is enabled, else None (never raises)."""
    return getattr(request.app.state, "workflow_recorder", None)  # type: ignore[return-value]


async def verify_api_token(
    request: Request,
    x_llmos_token: Annotated[str | None, Header(alias=HEADER_API_TOKEN)] = None,
) -> None:
    """Verify the API token if one is configured."""
    settings: Settings = request.app.state.settings
    expected = settings.security.api_token

    if expected is None:
        return  # No auth configured — local-only mode.

    if x_llmos_token != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API token.",
            headers={"WWW-Authenticate": "Bearer"},
        )


# Shorthand type aliases for route signatures.
RegistryDep = Annotated[ModuleRegistry, Depends(get_module_registry)]
StateStoreDep = Annotated[PlanStateStore, Depends(get_state_store)]
GuardDep = Annotated[PermissionGuard, Depends(get_permission_guard)]
AuditDep = Annotated[AuditLogger, Depends(get_audit_logger)]
ExecutorDep = Annotated[PlanExecutor, Depends(get_plan_executor)]
ConfigDep = Annotated[Settings, Depends(get_config)]
AuthDep = Annotated[None, Depends(verify_api_token)]
ApprovalGateDep = Annotated[ApprovalGate, Depends(get_approval_gate)]
# Nullable — returns None when recording subsystem is disabled (does not raise 503).
# Note: Use Any instead of forward-ref string to avoid FastAPI treating it as a query param.
RecorderDep = Annotated[Any, Depends(get_recorder)]
