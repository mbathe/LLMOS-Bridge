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


def get_security_manager(request: Request) -> Any:
    """Return SecurityManager if configured, else None."""
    return getattr(request.app.state, "security_manager", None)


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
SecurityManagerDep = Annotated[Any, Depends(get_security_manager)]


def get_intent_verifier(request: Request) -> Any:
    """Return IntentVerifier if configured, else None."""
    return getattr(request.app.state, "intent_verifier", None)


IntentVerifierDep = Annotated[Any, Depends(get_intent_verifier)]


def get_scanner_pipeline(request: Request) -> Any:
    """Return SecurityPipeline if configured, else None."""
    return getattr(request.app.state, "scanner_pipeline", None)


ScannerPipelineDep = Annotated[Any, Depends(get_scanner_pipeline)]


def get_lifecycle_manager(request: Request) -> Any:
    """Return ModuleLifecycleManager if configured, else None."""
    return getattr(request.app.state, "lifecycle_manager", None)

LifecycleManagerDep = Annotated[Any, Depends(get_lifecycle_manager)]

def get_service_bus(request: Request) -> Any:
    """Return ServiceBus if configured, else None."""
    return getattr(request.app.state, "service_bus", None)

ServiceBusDep = Annotated[Any, Depends(get_service_bus)]

def get_module_installer(request: Request) -> Any:
    """Return ModuleInstaller if hub is enabled, else None."""
    return getattr(request.app.state, "module_installer", None)

ModuleInstallerDep = Annotated[Any, Depends(get_module_installer)]

def get_hub_client(request: Request) -> Any:
    """Return HubClient if hub is enabled, else None."""
    return getattr(request.app.state, "hub_client", None)

HubClientDep = Annotated[Any, Depends(get_hub_client)]


def get_identity_store(request: Request) -> Any:
    """Return IdentityStore if identity system is enabled, else None."""
    return getattr(request.app.state, "identity_store", None)

IdentityStoreDep = Annotated[Any, Depends(get_identity_store)]


def get_identity_resolver(request: Request) -> Any:
    """Return IdentityResolver."""
    return getattr(request.app.state, "identity_resolver", None)

IdentityResolverDep = Annotated[Any, Depends(get_identity_resolver)]


async def get_identity_context(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    x_llmos_app: Annotated[str | None, Header(alias="X-LLMOS-App")] = None,
    x_llmos_agent: Annotated[str | None, Header(alias="X-LLMOS-Agent")] = None,
    x_llmos_session: Annotated[str | None, Header(alias="X-LLMOS-Session")] = None,
) -> Any:
    """Resolve the current caller's identity from request headers.

    Returns an IdentityContext.  When the identity system is disabled,
    returns the default context (app_id="default", role=ADMIN).
    """
    resolver = getattr(request.app.state, "identity_resolver", None)
    if resolver is None:
        from llmos_bridge.identity.models import IdentityContext
        return IdentityContext()
    return await resolver.resolve(
        authorization=authorization,
        x_app=x_llmos_app,
        x_agent=x_llmos_agent,
        x_session=x_llmos_session,
    )

IdentityDep = Annotated[Any, Depends(get_identity_context)]


def get_node_registry(request: Request) -> Any:
    """Return NodeRegistry if available."""
    return getattr(request.app.state, "node_registry", None)

NodeRegistryDep = Annotated[Any, Depends(get_node_registry)]


def get_discovery(request: Request) -> Any:
    """Return NodeDiscoveryService if available (non-standalone mode)."""
    return getattr(request.app.state, "discovery", None)

DiscoveryDep = Annotated[Any, Depends(get_discovery)]


def get_node_health_monitor(request: Request) -> Any:
    """Return NodeHealthMonitor if available (non-standalone mode)."""
    return getattr(request.app.state, "node_health_monitor", None)

HealthMonitorDep = Annotated[Any, Depends(get_node_health_monitor)]


def get_load_tracker(request: Request) -> Any:
    """Return ActiveActionCounter if smart routing is active, else None."""
    return getattr(request.app.state, "load_tracker", None)

LoadTrackerDep = Annotated[Any, Depends(get_load_tracker)]


def get_quarantine(request: Request) -> Any:
    """Return NodeQuarantine if smart routing is active, else None."""
    return getattr(request.app.state, "quarantine", None)

QuarantineDep = Annotated[Any, Depends(get_quarantine)]


def get_authorization_guard(request: Request) -> Any:
    """Return AuthorizationGuard if configured, else a disabled stub."""
    guard = getattr(request.app.state, "authorization_guard", None)
    if guard is not None:
        return guard
    # Return a disabled guard so routes always get a non-None object.
    from llmos_bridge.identity.authorization import AuthorizationGuard
    return AuthorizationGuard(store=None, enabled=False)

AuthorizationGuardDep = Annotated[Any, Depends(get_authorization_guard)]
