"""Identity layer — Application, Agent, API Key, Session management endpoints.

All endpoints require the identity system to be enabled (``identity.enabled=True``).
When disabled, returns 503 with a descriptive message.

Phase 6: Full RBAC enforcement on all endpoints.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from llmos_bridge.api.dependencies import (
    AuthorizationGuardDep,
    ConfigDep,
    IdentityDep,
    IdentityStoreDep,
    SecurityManagerDep,
)
import time

from llmos_bridge.api.schemas import (
    AgentResponse,
    ApiKeyResponse,
    AppPermissionGrantRequest,
    AppPermissionRevokeRequest,
    ApplicationResponse,
    CreateAgentRequest,
    CreateApplicationRequest,
    CreateSessionRequest,
    SessionResponse,
    UpdateApplicationRequest,
)
from llmos_bridge.exceptions import AuthorizationError
from llmos_bridge.identity.models import Application, Role, Session
from llmos_bridge.security.models import PermissionGrant, PermissionScope

router = APIRouter(tags=["applications"])


def _require_identity(store: object) -> None:
    """Raise 503 if the identity system is not enabled."""
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Identity system is not enabled (set identity.enabled=true in config).",
        )


def _handle_authz_error(exc: AuthorizationError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=str(exc),
    )


def _session_response(s: Session) -> SessionResponse:
    return SessionResponse(
        session_id=s.session_id,
        app_id=s.app_id,
        agent_id=s.agent_id,
        created_at=s.created_at,
        last_active=s.last_active,
        expires_at=s.expires_at,
        idle_timeout_seconds=s.idle_timeout_seconds,
        allowed_modules=s.allowed_modules,
        permission_grants=s.permission_grants,
        permission_denials=s.permission_denials,
        expired=s.is_expired(),
    )


def _app_response(app: Application, stats: dict[str, int]) -> ApplicationResponse:
    """Build an ApplicationResponse from an Application model + stats."""
    return ApplicationResponse(
        app_id=app.app_id,
        name=app.name,
        description=app.description,
        created_at=app.created_at,
        updated_at=app.updated_at,
        enabled=app.enabled,
        max_concurrent_plans=app.max_concurrent_plans,
        max_actions_per_plan=app.max_actions_per_plan,
        allowed_modules=app.allowed_modules,
        allowed_actions=app.allowed_actions,
        tags=app.tags,
        **stats,
    )


# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------


@router.post("/applications", response_model=ApplicationResponse, status_code=201, summary="Create application")
async def create_application(
    body: CreateApplicationRequest,
    store: IdentityStoreDep,
    identity: IdentityDep,
    guard: AuthorizationGuardDep,
) -> ApplicationResponse:
    _require_identity(store)
    try:
        guard.require_role(identity, Role.ADMIN, resource="create_application")
    except AuthorizationError as exc:
        raise _handle_authz_error(exc)
    try:
        app = await store.create_application(
            name=body.name,
            description=body.description,
            max_concurrent_plans=body.max_concurrent_plans,
            max_actions_per_plan=body.max_actions_per_plan,
            allowed_modules=body.allowed_modules,
            allowed_actions=body.allowed_actions,
            tags=body.tags,
        )
    except Exception as exc:
        if "UNIQUE constraint" in str(exc):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Application name '{body.name}' already exists.",
            ) from exc
        raise
    stats = await store.app_stats(app.app_id)
    return _app_response(app, stats)


@router.get("/applications", response_model=list[ApplicationResponse], summary="List applications")
async def list_applications(
    store: IdentityStoreDep,
    identity: IdentityDep,
    guard: AuthorizationGuardDep,
    include_disabled: bool = False,
) -> list[ApplicationResponse]:
    _require_identity(store)
    try:
        guard.require_role(identity, Role.VIEWER, resource="list_applications")
    except AuthorizationError as exc:
        raise _handle_authz_error(exc)
    apps = await store.list_applications(include_disabled=include_disabled)
    # APP_ADMIN and below only see their own application.
    if identity.role not in (Role.ADMIN,):
        apps = [a for a in apps if a.app_id == identity.app_id]
    results = []
    for app in apps:
        stats = await store.app_stats(app.app_id)
        results.append(_app_response(app, stats))
    return results


@router.get("/applications/{app_id}", response_model=ApplicationResponse, summary="Get application")
async def get_application(
    app_id: str,
    store: IdentityStoreDep,
    identity: IdentityDep,
    guard: AuthorizationGuardDep,
) -> ApplicationResponse:
    _require_identity(store)
    try:
        guard.require_role(identity, Role.VIEWER, resource="get_application")
        guard.require_app_scope(identity, app_id)
    except AuthorizationError as exc:
        raise _handle_authz_error(exc)
    app = await store.get_application(app_id)
    if app is None:
        raise HTTPException(status_code=404, detail=f"Application '{app_id}' not found.")
    stats = await store.app_stats(app.app_id)
    return _app_response(app, stats)


@router.put("/applications/{app_id}", response_model=ApplicationResponse, summary="Update application")
async def update_application(
    app_id: str,
    body: UpdateApplicationRequest,
    store: IdentityStoreDep,
    identity: IdentityDep,
    guard: AuthorizationGuardDep,
) -> ApplicationResponse:
    _require_identity(store)
    try:
        guard.require_role(identity, Role.APP_ADMIN, resource="update_application")
        guard.require_app_scope(identity, app_id)
    except AuthorizationError as exc:
        raise _handle_authz_error(exc)
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update.")
    updated = await store.update_application(app_id, **updates)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Application '{app_id}' not found.")
    stats = await store.app_stats(updated.app_id)
    return _app_response(updated, stats)


@router.delete("/applications/{app_id}", summary="Delete application")
async def delete_application(
    app_id: str,
    store: IdentityStoreDep,
    identity: IdentityDep,
    guard: AuthorizationGuardDep,
    hard: bool = False,
) -> dict[str, str]:
    _require_identity(store)
    try:
        guard.require_role(identity, Role.ADMIN, resource="delete_application")
    except AuthorizationError as exc:
        raise _handle_authz_error(exc)
    if app_id == "default":
        raise HTTPException(status_code=400, detail="Cannot delete the default application.")
    deleted = await store.delete_application(app_id, hard=hard)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Application '{app_id}' not found.")
    action = "deleted" if hard else "disabled"
    return {"detail": f"Application '{app_id}' {action}."}


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------


@router.post(
    "/applications/{app_id}/agents",
    response_model=AgentResponse,
    status_code=201,
    summary="Create agent",
)
async def create_agent(
    app_id: str,
    body: CreateAgentRequest,
    store: IdentityStoreDep,
    identity: IdentityDep,
    guard: AuthorizationGuardDep,
) -> AgentResponse:
    _require_identity(store)
    try:
        guard.require_role(identity, Role.APP_ADMIN, resource="create_agent")
        guard.require_app_scope(identity, app_id)
    except AuthorizationError as exc:
        raise _handle_authz_error(exc)
    # Verify application exists.
    app = await store.get_application(app_id)
    if app is None:
        raise HTTPException(status_code=404, detail=f"Application '{app_id}' not found.")
    try:
        role = Role(body.role)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid role: '{body.role}'.")
    agent = await store.create_agent(name=body.name, app_id=app_id, role=role)
    return AgentResponse(
        agent_id=agent.agent_id,
        name=agent.name,
        app_id=agent.app_id,
        role=agent.role.value,
        created_at=agent.created_at,
        enabled=agent.enabled,
    )


@router.get(
    "/applications/{app_id}/agents",
    response_model=list[AgentResponse],
    summary="List agents",
)
async def list_agents(
    app_id: str,
    store: IdentityStoreDep,
    identity: IdentityDep,
    guard: AuthorizationGuardDep,
) -> list[AgentResponse]:
    _require_identity(store)
    try:
        guard.require_role(identity, Role.VIEWER, resource="list_agents")
        guard.require_app_scope(identity, app_id)
    except AuthorizationError as exc:
        raise _handle_authz_error(exc)
    agents = await store.list_agents(app_id)
    return [
        AgentResponse(
            agent_id=a.agent_id,
            name=a.name,
            app_id=a.app_id,
            role=a.role.value,
            created_at=a.created_at,
            enabled=a.enabled,
        )
        for a in agents
    ]


@router.delete(
    "/applications/{app_id}/agents/{agent_id}",
    summary="Delete agent",
)
async def delete_agent(
    app_id: str,
    agent_id: str,
    store: IdentityStoreDep,
    identity: IdentityDep,
    guard: AuthorizationGuardDep,
) -> dict[str, str]:
    _require_identity(store)
    try:
        guard.require_role(identity, Role.APP_ADMIN, resource="delete_agent")
        guard.require_app_scope(identity, app_id)
    except AuthorizationError as exc:
        raise _handle_authz_error(exc)
    deleted = await store.delete_agent(agent_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found.")
    return {"detail": f"Agent '{agent_id}' deleted."}


# ---------------------------------------------------------------------------
# API Keys
# ---------------------------------------------------------------------------


@router.post(
    "/applications/{app_id}/agents/{agent_id}/keys",
    response_model=ApiKeyResponse,
    status_code=201,
    summary="Generate API key",
)
async def generate_api_key(
    app_id: str,
    agent_id: str,
    store: IdentityStoreDep,
    identity: IdentityDep,
    guard: AuthorizationGuardDep,
) -> ApiKeyResponse:
    _require_identity(store)
    try:
        guard.require_role(identity, Role.APP_ADMIN, resource="generate_api_key")
        guard.require_app_scope(identity, app_id)
    except AuthorizationError as exc:
        raise _handle_authz_error(exc)
    agent = await store.get_agent(agent_id)
    if agent is None or agent.app_id != app_id:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found in app '{app_id}'.")
    api_key, cleartext = await store.create_api_key(agent_id=agent_id, app_id=app_id)
    return ApiKeyResponse(
        key_id=api_key.key_id,
        prefix=api_key.prefix,
        api_key=cleartext,  # Only returned once!
        created_at=api_key.created_at,
        expires_at=api_key.expires_at,
    )


@router.delete(
    "/applications/{app_id}/agents/{agent_id}/keys/{key_id}",
    summary="Revoke API key",
)
async def revoke_api_key(
    app_id: str,
    agent_id: str,
    key_id: str,
    store: IdentityStoreDep,
    identity: IdentityDep,
    guard: AuthorizationGuardDep,
) -> dict[str, str]:
    _require_identity(store)
    try:
        guard.require_role(identity, Role.APP_ADMIN, resource="revoke_api_key")
        guard.require_app_scope(identity, app_id)
    except AuthorizationError as exc:
        raise _handle_authz_error(exc)
    revoked = await store.revoke_api_key(key_id)
    if not revoked:
        raise HTTPException(status_code=404, detail=f"API key '{key_id}' not found.")
    return {"detail": f"API key '{key_id}' revoked."}


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


@router.post(
    "/applications/{app_id}/sessions",
    response_model=SessionResponse,
    status_code=201,
    summary="Create session",
)
async def create_session(
    app_id: str,
    body: CreateSessionRequest,
    store: IdentityStoreDep,
    identity: IdentityDep,
    guard: AuthorizationGuardDep,
) -> SessionResponse:
    """Create a new session with optional security constraints."""
    _require_identity(store)
    try:
        guard.require_role(identity, Role.OPERATOR, resource="create_session")
        guard.require_app_scope(identity, app_id)
    except AuthorizationError as exc:
        raise _handle_authz_error(exc)
    app = await store.get_application(app_id)
    if app is None:
        raise HTTPException(status_code=404, detail=f"Application '{app_id}' not found.")

    expires_at: float | None = None
    if body.expires_in_seconds is not None:
        expires_at = time.time() + body.expires_in_seconds

    session = await store.create_session(
        app_id=app_id,
        agent_id=body.agent_id,
        expires_at=expires_at,
        idle_timeout_seconds=body.idle_timeout_seconds,
        allowed_modules=body.allowed_modules,
        permission_grants=body.permission_grants,
        permission_denials=body.permission_denials,
    )
    return _session_response(session)


@router.get(
    "/applications/{app_id}/sessions",
    response_model=list[SessionResponse],
    summary="List sessions",
)
async def list_sessions(
    app_id: str,
    store: IdentityStoreDep,
    identity: IdentityDep,
    guard: AuthorizationGuardDep,
    limit: int = 100,
) -> list[SessionResponse]:
    _require_identity(store)
    try:
        guard.require_role(identity, Role.VIEWER, resource="list_sessions")
        guard.require_app_scope(identity, app_id)
    except AuthorizationError as exc:
        raise _handle_authz_error(exc)
    sessions = await store.list_sessions(app_id, limit=limit)
    return [_session_response(s) for s in sessions]


@router.get(
    "/applications/{app_id}/sessions/{session_id}",
    response_model=SessionResponse,
    summary="Get session",
)
async def get_session(
    app_id: str,
    session_id: str,
    store: IdentityStoreDep,
    identity: IdentityDep,
    guard: AuthorizationGuardDep,
) -> SessionResponse:
    _require_identity(store)
    try:
        guard.require_role(identity, Role.VIEWER, resource="get_session")
        guard.require_app_scope(identity, app_id)
    except AuthorizationError as exc:
        raise _handle_authz_error(exc)
    session = await store.get_session(session_id)
    if session is None or session.app_id != app_id:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")
    return _session_response(session)


@router.delete(
    "/applications/{app_id}/sessions/{session_id}",
    summary="Delete session",
)
async def delete_session(
    app_id: str,
    session_id: str,
    store: IdentityStoreDep,
    identity: IdentityDep,
    guard: AuthorizationGuardDep,
) -> dict[str, str]:
    _require_identity(store)
    try:
        guard.require_role(identity, Role.OPERATOR, resource="delete_session")
        guard.require_app_scope(identity, app_id)
    except AuthorizationError as exc:
        raise _handle_authz_error(exc)
    session = await store.get_session(session_id)
    if session is None or session.app_id != app_id:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")
    await store.delete_session(session_id)
    return {"detail": f"Session '{session_id}' deleted."}


# ---------------------------------------------------------------------------
# Application OS Permissions (app-scoped, identity-aware)
# ---------------------------------------------------------------------------


def _get_permission_store(sec_mgr: object):
    """Extract PermissionStore from SecurityManager, or raise 503."""
    if sec_mgr is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Security module not available (enable_decorators may be off).",
        )
    pm = getattr(sec_mgr, "permission_manager", None)
    if pm is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PermissionManager not initialised.",
        )
    return pm.store


@router.get(
    "/applications/{app_id}/permissions",
    summary="List OS permissions for an application",
)
async def list_app_permissions(
    app_id: str,
    store: IdentityStoreDep,
    identity: IdentityDep,
    guard: AuthorizationGuardDep,
    sec_mgr: SecurityManagerDep,
) -> dict:
    """Return all OS-level permission grants scoped to this application."""
    _require_identity(store)
    try:
        guard.require_role(identity, Role.VIEWER, resource="list_app_permissions")
        guard.require_app_scope(identity, app_id)
    except AuthorizationError as exc:
        raise _handle_authz_error(exc)
    # Verify the application exists.
    app = await store.get_application(app_id)
    if app is None:
        raise HTTPException(status_code=404, detail=f"Application '{app_id}' not found.")

    perm_store = _get_permission_store(sec_mgr)
    grants = await perm_store.get_for_app(app_id)
    return {"grants": [g.to_dict() for g in grants], "count": len(grants)}


@router.post(
    "/applications/{app_id}/permissions/grant",
    status_code=201,
    summary="Grant OS permission to a module for an application",
)
async def grant_app_permission(
    app_id: str,
    body: AppPermissionGrantRequest,
    store: IdentityStoreDep,
    identity: IdentityDep,
    guard: AuthorizationGuardDep,
    sec_mgr: SecurityManagerDep,
) -> dict:
    """Grant an OS-level permission to a module, scoped to this application.

    Only APP_ADMIN (or higher) for this application can grant permissions.
    """
    _require_identity(store)
    try:
        guard.require_role(identity, Role.APP_ADMIN, resource="grant_app_permission")
        guard.require_app_scope(identity, app_id)
    except AuthorizationError as exc:
        raise _handle_authz_error(exc)
    app = await store.get_application(app_id)
    if app is None:
        raise HTTPException(status_code=404, detail=f"Application '{app_id}' not found.")

    try:
        scope = PermissionScope(body.scope)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid scope '{body.scope}'. Use 'session' or 'permanent'.")

    perm_store = _get_permission_store(sec_mgr)
    grant = PermissionGrant(
        permission=body.permission,
        module_id=body.module_id,
        scope=scope,
        granted_by=getattr(identity, "agent_id", None) or "user",
        reason=body.reason,
        app_id=app_id,
    )
    await perm_store.grant(grant, app_id=app_id)
    return {"granted": True, "grant": grant.to_dict()}


@router.post(
    "/applications/{app_id}/permissions/revoke",
    summary="Revoke OS permission from a module for an application",
)
async def revoke_app_permission(
    app_id: str,
    body: AppPermissionRevokeRequest,
    store: IdentityStoreDep,
    identity: IdentityDep,
    guard: AuthorizationGuardDep,
    sec_mgr: SecurityManagerDep,
) -> dict:
    """Revoke an OS-level permission from a module for this application."""
    _require_identity(store)
    try:
        guard.require_role(identity, Role.APP_ADMIN, resource="revoke_app_permission")
        guard.require_app_scope(identity, app_id)
    except AuthorizationError as exc:
        raise _handle_authz_error(exc)
    app = await store.get_application(app_id)
    if app is None:
        raise HTTPException(status_code=404, detail=f"Application '{app_id}' not found.")

    perm_store = _get_permission_store(sec_mgr)
    removed = await perm_store.revoke(body.permission, body.module_id, app_id=app_id)
    if not removed:
        raise HTTPException(
            status_code=404,
            detail=f"Permission '{body.permission}' not granted to '{body.module_id}' for app '{app_id}'.",
        )
    return {"revoked": True, "permission": body.permission, "module_id": body.module_id, "app_id": app_id}
