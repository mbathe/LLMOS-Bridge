"""API routes — LLMOS App Language management.

REST API for registering, listing, running, and managing .app.yaml applications.

Endpoints:
    POST   /apps/register         Register (upload) an app from YAML text or file path
    GET    /apps                   List all registered apps
    GET    /apps/{app_id}          Get app details
    DELETE /apps/{app_id}          Unregister an app
    POST   /apps/{app_id}/run      Run an app with input text
    POST   /apps/{app_id}/validate Re-validate an app
    PUT    /apps/{app_id}/status   Update app status (start/stop triggers)
"""

from __future__ import annotations

import hashlib
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from llmos_bridge.api.dependencies import AuthDep
from llmos_bridge.apps.daemon_executor import set_identity_context, _current_scope, _ExecutionScope

router = APIRouter(prefix="/apps", tags=["apps"])


# ─── Request / Response schemas ──────────────────────────────────────


class RegisterAppRequest(BaseModel):
    """Register an app from YAML text or file path."""
    yaml_text: str | None = Field(None, description="Raw YAML content of the .app.yaml file")
    file_path: str | None = Field(None, description="Absolute path to the .app.yaml file on disk")


class ExecuteToolRequest(BaseModel):
    """Execute a tool call through the app's DaemonToolExecutor."""
    module_id: str = Field(..., description="Module to call")
    action: str = Field(..., description="Action to execute")
    params: dict[str, Any] = Field(default_factory=dict, description="Action parameters")
    app_id: str | None = Field(None, description="Optional app ID for app-level security context")


class RunAppRequest(BaseModel):
    """Run an app with input text."""
    input: str = Field(..., description="Input text for the app")
    variables: dict[str, Any] = Field(default_factory=dict, description="Override variables")
    stream: bool = Field(False, description="Whether to stream the response (SSE)")
    session_id: str | None = Field(None, description="Optional session ID for RBAC binding")


class UpdateStatusRequest(BaseModel):
    """Update app status."""
    status: str = Field(..., description="New status: 'running' or 'stopped'")


class AppResponse(BaseModel):
    """App record response."""
    id: str
    name: str
    version: str
    description: str
    author: str
    file_path: str
    status: str
    tags: list[str]
    created_at: float
    updated_at: float
    last_run_at: float
    run_count: int
    error_message: str


class RunAppResponse(BaseModel):
    """Result of running an app."""
    success: bool
    output: str
    error: str | None = None
    duration_ms: float = 0
    total_turns: int = 0
    stop_reason: str = ""


class ValidateAppResponse(BaseModel):
    """Validation result."""
    valid: bool
    errors: list[str] = Field(default_factory=list)


# ─── Helpers ─────────────────────────────────────────────────────────


def _get_app_store(request: Request):
    """Get the AppStore from app state."""
    store = getattr(request.app.state, "app_store", None)
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="App store not available. Enable app language support in config.",
        )
    return store


def _get_app_runtime(request: Request):
    """Get the AppRuntime from app state."""
    runtime = getattr(request.app.state, "app_runtime", None)
    if runtime is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="App runtime not available.",
        )
    return runtime


# ─── Endpoints ───────────────────────────────────────────────────────


@router.post("/register", response_model=AppResponse, status_code=status.HTTP_201_CREATED)
async def register_app(
    body: RegisterAppRequest,
    request: Request,
    _auth: AuthDep,
):
    """Register a new app from YAML text or file path."""
    store = _get_app_store(request)
    runtime = _get_app_runtime(request)

    if not body.yaml_text and not body.file_path:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide either yaml_text or file_path.",
        )

    try:
        if body.yaml_text:
            app_def = runtime.load_string(body.yaml_text)
            file_path = ""
        else:
            app_def = runtime.load(body.file_path)
            file_path = body.file_path
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Compilation error: {e}",
        )

    # Generate deterministic ID from name + version
    app_id = hashlib.sha256(
        f"{app_def.app.name}:{app_def.app.version}".encode()
    ).hexdigest()[:16]

    record = await store.register(
        app_id=app_id,
        name=app_def.app.name,
        version=app_def.app.version,
        file_path=file_path,
        description=app_def.app.description,
        author=app_def.app.author,
        tags=app_def.app.tags,
        config_json=body.yaml_text or "",
    )

    # Auto-create a matching Application identity so dashboard security
    # settings (allowed_modules, allowed_actions, sessions) apply to this app.
    identity_store = getattr(request.app.state, "identity_store", None)
    if identity_store is not None:
        try:
            # Extract module IDs from the YAML app definition
            module_ids = sorted(app_def.get_all_module_ids())
            # Extract per-module action whitelist from capabilities.grant
            allowed_actions: dict[str, list[str]] = {}
            for grant in app_def.capabilities.grant:
                if grant.actions:
                    allowed_actions[grant.module] = list(grant.actions)

            await identity_store.create_application(
                name=app_def.app.name,
                description=app_def.app.description or f"YAML App: {app_def.app.name} v{app_def.app.version}",
                app_id=app_id,
                allowed_modules=module_ids,
                allowed_actions=allowed_actions,
                tags={"yaml_app": "true", "version": app_def.app.version},
            )
        except Exception:
            pass  # Identity system may be disabled — don't block registration

    # Start background triggers (schedule, watch, event) if any
    _start_app_triggers(request, app_id, app_def)

    return AppResponse(**record.to_dict())


@router.get("", response_model=list[AppResponse])
async def list_apps(
    request: Request,
    _auth: AuthDep,
    status_filter: str | None = None,
    tag: str | None = None,
):
    """List all registered apps."""
    store = _get_app_store(request)

    from llmos_bridge.apps.app_store import AppStatus
    app_status = None
    if status_filter:
        try:
            app_status = AppStatus(status_filter)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid status: {status_filter}",
            )

    records = await store.list_apps(status=app_status, tag=tag)
    return [AppResponse(**r.to_dict()) for r in records]


@router.get("/{app_id}", response_model=AppResponse)
async def get_app(
    app_id: str,
    request: Request,
    _auth: AuthDep,
):
    """Get app details by ID."""
    store = _get_app_store(request)
    record = await store.get(app_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="App not found")
    return AppResponse(**record.to_dict())


@router.delete("/{app_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_app(
    app_id: str,
    request: Request,
    _auth: AuthDep,
):
    """Unregister an app."""
    store = _get_app_store(request)
    deleted = await store.delete(app_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="App not found")


@router.post("/{app_id}/run", response_model=RunAppResponse)
async def run_app(
    app_id: str,
    body: RunAppRequest,
    request: Request,
    _auth: AuthDep,
):
    """Run a registered app with input text."""
    store = _get_app_store(request)
    runtime = _get_app_runtime(request)

    record = await store.get(app_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="App not found")

    # Load the app definition
    try:
        if record.file_path:
            app_def = runtime.load(record.file_path)
        else:
            # App was registered from yaml_text — config_json contains the raw YAML
            yaml_text = record.config_json
            if not yaml_text or yaml_text == "{}":
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="App has no file path and no stored YAML.",
                )
            app_def = runtime.load_string(yaml_text)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Failed to load app: {e}",
        )

    # Check authorization if the identity system is enabled
    authorization_guard = getattr(request.app.state, "authorization_guard", None)
    identity_resolver = getattr(request.app.state, "identity_resolver", None)
    if authorization_guard and identity_resolver:
        try:
            identity = await identity_resolver.resolve(request)
            # Validate session if provided
            if body.session_id and identity:
                identity.session_id = body.session_id
                await authorization_guard.validate_session(identity)
            # Check that all modules used by the app are allowed for this identity
            if identity and identity.app_id:
                for module_id in app_def.get_all_module_ids():
                    authorization_guard.check_action_allowed(
                        app=await authorization_guard._store.get_application(identity.app_id),
                        module_id=module_id,
                        action_name="*",
                    ) if hasattr(authorization_guard, '_store') else None
        except Exception as auth_err:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Authorization failed: {auth_err}",
            )

    # Create an isolated execution scope for this request (concurrency-safe)
    resolved_identity = None
    if identity_resolver:
        try:
            resolved_identity = await identity_resolver.resolve(request)
        except Exception:
            pass
    if resolved_identity is None:
        from llmos_bridge.identity.models import IdentityContext
        resolved_identity = IdentityContext(app_id=app_id)
    if body.session_id and resolved_identity:
        resolved_identity.session_id = body.session_id
    scope_token = _current_scope.set(_ExecutionScope(identity=resolved_identity))

    # Run the app
    try:
        if app_def.is_multi_agent():
            result = await runtime.run_multi_agent(app_def, body.input, variables=body.variables)
            await store.record_run(app_id)
            return RunAppResponse(
                success=result.success,
                output=result.output,
                error=result.error,
            )
        else:
            result = await runtime.run(app_def, body.input, variables=body.variables)
            await store.record_run(app_id)
            return RunAppResponse(
                success=result.success,
                output=result.output,
                error=result.error,
                duration_ms=result.duration_ms,
                total_turns=result.total_turns,
                stop_reason=result.stop_reason or "",
            )
    except Exception as e:
        from llmos_bridge.apps.app_store import AppStatus
        await store.update_status(app_id, AppStatus.error, str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"App execution failed: {e}",
        )
    finally:
        _current_scope.reset(scope_token)


@router.post("/{app_id}/validate", response_model=ValidateAppResponse)
async def validate_app(
    app_id: str,
    request: Request,
    _auth: AuthDep,
):
    """Re-validate a registered app."""
    store = _get_app_store(request)
    runtime = _get_app_runtime(request)

    record = await store.get(app_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="App not found")

    if not record.file_path:
        return ValidateAppResponse(valid=True, errors=[])

    errors = runtime.validate(record.file_path)
    return ValidateAppResponse(valid=len(errors) == 0, errors=errors)


@router.put("/{app_id}/status")
async def update_app_status(
    app_id: str,
    body: UpdateStatusRequest,
    request: Request,
    _auth: AuthDep,
):
    """Update app status (e.g., start/stop triggers)."""
    store = _get_app_store(request)

    from llmos_bridge.apps.app_store import AppStatus
    try:
        new_status = AppStatus(body.status)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid status: {body.status}. Valid: {[s.value for s in AppStatus]}",
        )

    record = await store.get(app_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="App not found")

    await store.update_status(app_id, new_status)

    # Start/stop triggers based on new status
    if new_status.value == "running":
        runtime = _get_app_runtime(request)
        try:
            if record.file_path:
                app_def = runtime.load(record.file_path)
            elif record.config_json and record.config_json != "{}":
                app_def = runtime.load_string(record.config_json)
            else:
                app_def = None
            if app_def:
                _start_app_triggers(request, app_id, app_def)
        except Exception:
            pass
    elif new_status.value == "stopped":
        _stop_app_triggers(request, app_id)

    updated = await store.get(app_id)
    return AppResponse(**updated.to_dict())


# ─── Execute Tool (GAP 6 fix) ──────────────────────────────────────


@router.post("/execute-tool")
async def execute_tool(
    body: ExecuteToolRequest,
    request: Request,
    _auth: AuthDep,
):
    """Execute a single tool call through the DaemonToolExecutor.

    This endpoint is used by CLI daemon mode to route tool calls through
    the full security pipeline (permissions, capabilities, scanner, sanitizer,
    audit) instead of bypassing it via POST /plans.

    If app_id is provided, the app's YAML security/capabilities are applied first.
    """
    executor = getattr(request.app.state, "daemon_tool_executor", None)
    if executor is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="DaemonToolExecutor not available.",
        )

    # If an app context is specified, apply its security settings
    if body.app_id:
        store = _get_app_store(request)
        runtime = _get_app_runtime(request)
        record = await store.get(body.app_id)
        if record:
            try:
                if record.file_path:
                    app_def = runtime.load(record.file_path)
                elif record.config_json and record.config_json != "{}":
                    app_def = runtime.load_string(record.config_json)
                else:
                    app_def = None
                if app_def:
                    # Apply YAML security settings to the executor for this call
                    runtime._apply_capabilities(app_def)
                    runtime._apply_security(app_def)
            except Exception:
                pass

    # Create isolated execution scope for this request
    identity_resolver = getattr(request.app.state, "identity_resolver", None)
    resolved_identity = None
    if identity_resolver:
        try:
            resolved_identity = await identity_resolver.resolve(request)
        except Exception:
            pass
    if resolved_identity is None and body.app_id:
        from llmos_bridge.identity.models import IdentityContext
        resolved_identity = IdentityContext(app_id=body.app_id)
    scope_token = _current_scope.set(_ExecutionScope(identity=resolved_identity))

    try:
        result = await executor.execute(body.module_id, body.action, body.params)

        is_error = isinstance(result, dict) and "error" in result
        if is_error:
            return {"success": False, **result}
        return {"success": True, "result": result}
    finally:
        _current_scope.reset(scope_token)


# ─── Trigger management helpers ─────────────────────────────────────


def _start_app_triggers(request: Request, app_id: str, app_def) -> None:
    """Start background triggers (schedule, watch, event) for an app.

    When AppTriggerBridge is available (daemon has TriggerDaemon), triggers
    are registered with the daemon for real cron/inotify/priority scheduling.
    Falls back to standalone TriggerManager otherwise.
    """
    import asyncio

    bg_triggers = [
        t for t in app_def.triggers
        if t.type.value in ("schedule", "watch", "event")
    ]
    if not bg_triggers:
        return

    # Try daemon bridge first
    bridge = getattr(request.app.state, "app_trigger_bridge", None)
    if bridge is not None:
        runtime = _get_app_runtime(request)

        async def _run_callback(input_text: str, metadata: dict):
            try:
                await runtime.run(app_def, input_text)
            except Exception:
                import logging
                logging.getLogger(__name__).exception(
                    "App trigger run failed: %s", app_id
                )

        asyncio.create_task(
            bridge.register_app_triggers(app_id, app_def, _run_callback)
        )
        return

    # Fallback: standalone TriggerManager
    managers = getattr(request.app.state, "trigger_managers", {})
    if app_id in managers:
        return  # Already running

    try:
        from llmos_bridge.apps.trigger_manager import TriggerManager

        runtime = _get_app_runtime(request)
        event_bus = getattr(request.app.state, "event_bus", None)

        async def _on_trigger(event, _rt=runtime, _def=app_def):
            try:
                await _rt.run(_def, event.input_text)
            except Exception:
                import logging
                logging.getLogger(__name__).exception(
                    "Standalone trigger run failed: %s", app_id
                )

        mgr = TriggerManager(app_def, on_trigger=_on_trigger, event_bus=event_bus)
        asyncio.create_task(mgr.start())
        managers[app_id] = mgr
    except Exception:
        import logging
        logging.getLogger(__name__).exception(
            "Failed to start triggers for app: %s", app_id
        )


def _stop_app_triggers(request: Request, app_id: str) -> None:
    """Stop background triggers for an app."""
    import asyncio

    # Try daemon bridge first
    bridge = getattr(request.app.state, "app_trigger_bridge", None)
    if bridge is not None:
        asyncio.create_task(bridge.unregister_app_triggers(app_id))
        return

    # Fallback: standalone TriggerManager
    managers = getattr(request.app.state, "trigger_managers", {})
    mgr = managers.pop(app_id, None)
    if mgr is not None:
        asyncio.create_task(mgr.stop())


# ─── Approval endpoints (app-level) ────────────────────────────────


class ApprovalDecisionRequest(BaseModel):
    """Submit a decision for a pending approval."""
    decision: str = Field(..., description="approve, reject, skip, modify, approve_always")
    modified_params: dict[str, Any] | None = Field(None, description="Modified params (for 'modify' decision)")
    reason: str | None = Field(None, description="Optional reason for the decision")
    approved_by: str | None = Field(None, description="Who approved/rejected")


@router.get("/approvals/pending")
async def list_pending_approvals(
    request: Request,
    _auth: AuthDep,
    run_id: str | None = None,
):
    """List all pending approval requests for app executions."""
    gate = getattr(request.app.state, "approval_gate", None)
    if gate is None:
        return []
    pending = gate.get_pending(plan_id=run_id)
    return [req.to_dict() for req in pending]


@router.post("/approvals/{action_id}/decide")
async def decide_approval(
    action_id: str,
    body: ApprovalDecisionRequest,
    request: Request,
    _auth: AuthDep,
    run_id: str | None = None,
):
    """Submit a decision for a pending approval request.

    The action_id identifies the specific pending approval.
    If run_id is provided, it scopes the lookup to that app run.
    """
    gate = getattr(request.app.state, "approval_gate", None)
    if gate is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Approval system not available.",
        )

    from llmos_bridge.orchestration.approval import ApprovalDecision, ApprovalResponse

    try:
        decision = ApprovalDecision(body.decision)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid decision: {body.decision}. Valid: approve, reject, skip, modify, approve_always",
        )

    response = ApprovalResponse(
        decision=decision,
        modified_params=body.modified_params,
        reason=body.reason,
        approved_by=body.approved_by,
    )

    # Find the matching pending request — search all pending by action_id
    pending = gate.get_pending(plan_id=run_id)
    matched_plan_id = None
    for req in pending:
        if req.action_id == action_id:
            matched_plan_id = req.plan_id
            break

    if matched_plan_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No pending approval with action_id={action_id}",
        )

    success = gate.submit_decision(matched_plan_id, action_id, response)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Approval request expired or already decided.",
        )

    return {"success": True, "decision": decision.value}
