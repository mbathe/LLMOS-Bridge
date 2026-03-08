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

import asyncio
import json as _json
import uuid as _uuid

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from llmos_bridge.api.dependencies import AuthDep
from llmos_bridge.apps.daemon_executor import set_identity_context, _current_scope, _ExecutionScope

router = APIRouter(prefix="/apps", tags=["apps"])


# ─── Request / Response schemas ──────────────────────────────────────


class RegisterAppRequest(BaseModel):
    """Register an app from YAML text or file path."""
    yaml_text: str | None = Field(None, description="Raw YAML content of the .app.yaml file")
    file_path: str | None = Field(None, description="Absolute path to the .app.yaml file on disk")
    application_id: str | None = Field(
        None,
        description=(
            "Dashboard Application ID to link this app to. "
            "If omitted, a new Application is auto-created. "
            "When provided, the app inherits the Application's security constraints."
        ),
    )


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
    conversation_history: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Previous conversation messages to inject (enables multi-turn in interactive mode)",
    )


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
    application_id: str = ""
    prepared: bool = False
    # CLI trigger fields (populated when app_def is available)
    cli_greeting: str = ""
    cli_prompt: str = "> "
    cli_mode: str = "conversation"  # conversation | one_shot


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


def _extract_cli_trigger(app_def) -> dict:
    """Extract CLI trigger fields from an AppDefinition (greeting, prompt, mode)."""
    for trigger in (app_def.triggers or []):
        if trigger.type.value == "cli":
            return {
                "cli_greeting": trigger.greeting or "",
                "cli_prompt": trigger.prompt or "> ",
                "cli_mode": getattr(trigger, "mode", None) and trigger.mode.value or "conversation",
            }
    return {"cli_greeting": "", "cli_prompt": "> ", "cli_mode": "conversation"}


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

    # ── Determine app_id ─────────────────────────────────────────────
    # If linking to an existing Application identity, use its ID so the
    # YAML app and Identity share the same ID.  Otherwise generate one.
    if body.application_id:
        app_id = body.application_id
    else:
        app_id = hashlib.sha256(
            f"{app_def.app.name}:{app_def.app.version}".encode()
        ).hexdigest()[:16]

    # ── Extract security info from YAML definition ─────────────────
    yaml_module_ids = sorted(app_def.get_all_module_ids())
    yaml_allowed_actions: dict[str, list[str]] = {}
    for grant in app_def.capabilities.grant:
        if grant.actions:
            yaml_allowed_actions[grant.module] = list(grant.actions)
    yaml_description = app_def.app.description or f"YAML App: {app_def.app.name} v{app_def.app.version}"
    yaml_tags = {"yaml_app": "true", "version": app_def.app.version}

    # ── Sync with Application identity ────────────────────────────
    identity_store = getattr(request.app.state, "identity_store", None)
    application_id = ""

    if identity_store is not None:
        # Check if an Identity already exists (by generated ID or by app name)
        existing_identity = await identity_store.get_application(app_id)
        if existing_identity is None:
            existing_identity = await identity_store.get_application_by_name(app_def.app.name)

        if existing_identity is not None:
            # UPDATE existing Identity with fresh YAML content
            await identity_store.update_application(
                existing_identity.app_id,
                description=yaml_description,
                enabled=True,
                allowed_modules=yaml_module_ids,
                allowed_actions=yaml_allowed_actions,
                tags={**(getattr(existing_identity, "tags", {}) or {}), **yaml_tags},
            )
            application_id = existing_identity.app_id
        else:
            # CREATE a new Application identity from YAML content
            try:
                new_identity_app = await identity_store.create_application(
                    name=app_def.app.name,
                    description=yaml_description,
                    app_id=app_id,
                    allowed_modules=yaml_module_ids,
                    allowed_actions=yaml_allowed_actions,
                    tags=yaml_tags,
                )
                application_id = new_identity_app.app_id
            except Exception:
                pass  # Identity system may be partially available

    record = await store.register(
        app_id=app_id,
        name=app_def.app.name,
        version=app_def.app.version,
        file_path=file_path,
        description=app_def.app.description,
        author=app_def.app.author,
        tags=app_def.app.tags,
        config_json=body.yaml_text or "",
        application_id=application_id,
    )

    # Start background triggers (schedule, watch, event) if any
    _start_app_triggers(request, app_id, app_def)

    return AppResponse(**record.to_dict(), **_extract_cli_trigger(app_def))


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

    # Try to extract CLI trigger info from stored YAML
    cli_fields: dict = {}
    runtime = getattr(request.app.state, "app_runtime", None)
    if runtime is not None:
        try:
            if record.file_path:
                app_def = runtime.load(record.file_path)
            elif record.config_json and record.config_json not in ("{}", ""):
                app_def = runtime.load_string(record.config_json)
            else:
                app_def = None
            if app_def is not None:
                cli_fields = _extract_cli_trigger(app_def)
        except Exception:
            pass

    return AppResponse(**record.to_dict(), **cli_fields)


@router.delete("/{app_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_app(
    app_id: str,
    request: Request,
    _auth: AuthDep,
):
    """Unregister an app and its linked Application identity."""
    store = _get_app_store(request)

    # Get the record first to find the linked Application identity
    record = await store.get(app_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="App not found")

    # Delete the YAML app
    await store.delete(app_id)

    # Cascade-delete the linked Application identity (if auto-created)
    if record.application_id:
        identity_store = getattr(request.app.state, "identity_store", None)
        if identity_store is not None:
            try:
                linked_app = await identity_store.get_application(record.application_id)
                if linked_app and getattr(linked_app, "tags", {}).get("yaml_app") == "true":
                    await identity_store.delete_application(record.application_id)
            except Exception:
                pass  # Best-effort cleanup


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

    if not record.prepared:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="App is not prepared. Call POST /apps/{app_id}/prepare first to validate modules and pre-load resources.",
        )

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

    # Create an isolated execution scope for this request (concurrency-safe).
    # run_id links approval requests to this specific run so the SSE stream
    # can surface approval_request events without mixing up concurrent runs.
    run_id = _uuid.uuid4().hex
    resolved_identity = None
    if identity_resolver:
        try:
            resolved_identity = await identity_resolver.resolve(request)
        except Exception:
            pass
    if resolved_identity is None:
        from llmos_bridge.identity.models import IdentityContext
        resolved_identity = IdentityContext(app_id=app_id)
    elif not resolved_identity.app_id or resolved_identity.app_id == "default":
        # Generic identity (no API key auth) — bind it to this specific app
        resolved_identity = resolved_identity.model_copy(update={"app_id": app_id})
    if body.session_id and resolved_identity:
        resolved_identity.session_id = body.session_id
    scope_token = _current_scope.set(_ExecutionScope(identity=resolved_identity, run_id=run_id))

    # ── Load secrets and inject into variables + environment ──────
    app_secrets: dict[str, str] = {}
    identity_store = getattr(request.app.state, "identity_store", None)
    if identity_store is not None:
        # Try the app store ID first, then the linked identity application_id,
        # then search by app name (handles version changes that alter the hash ID)
        _secret_ids = [app_id]
        if record.application_id and record.application_id != app_id:
            _secret_ids.append(record.application_id)
        for _sid in _secret_ids:
            try:
                app_secrets = await identity_store.get_secrets(_sid)
                if app_secrets:
                    break
            except Exception:
                pass
        if not app_secrets:
            # Fallback: find identity application by name
            try:
                all_apps = await identity_store.list_applications(include_disabled=True)
                for _ia in all_apps:
                    if _ia.name == app_def.app.name and _ia.app_id != app_id:
                        app_secrets = await identity_store.get_secrets(_ia.app_id)
                        if app_secrets:
                            break
            except Exception:
                pass
    if app_secrets:
        # Inject into variables so {{secret.KEY}} resolves
        merged_vars = dict(body.variables or {})
        merged_vars["_secrets"] = app_secrets
        body_variables = merged_vars
        # Also set in os.environ for LLM SDK clients (e.g. ANTHROPIC_API_KEY)
        import os
        _env_backup: dict[str, str | None] = {}
        for k, v in app_secrets.items():
            _env_backup[k] = os.environ.get(k)
            os.environ[k] = v
    else:
        body_variables = body.variables
        _env_backup = {}

    def _restore_env() -> None:
        import os
        for k, original in _env_backup.items():
            if original is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = original

    # ── SSE streaming mode ──────────────────────────────────────────
    if body.stream:
        gate = getattr(request.app.state, "approval_gate", None)

        async def _event_stream():
            event_queue: asyncio.Queue = asyncio.Queue()
            stop_evt = asyncio.Event()

            async def _run_agent():
                try:
                    _stream_fn = (
                        runtime._stream_with_history(
                            app_def, body.input, body.conversation_history,
                            variables=body_variables, secrets=app_secrets,
                        )
                        if body.conversation_history
                        else runtime.stream(
                            app_def, body.input, variables=body_variables, secrets=app_secrets,
                        )
                    )
                    async for ev in _stream_fn:
                        await event_queue.put({"type": ev.type, "data": ev.data})
                    await store.record_run(app_id)
                except Exception as exc:
                    from llmos_bridge.apps.app_store import AppStatus
                    await store.update_status(app_id, AppStatus.error, str(exc))
                    await event_queue.put({"type": "error", "data": {"error": str(exc)}})
                finally:
                    stop_evt.set()
                    await event_queue.put(None)  # sentinel

            async def _poll_approvals():
                """Emit approval_request events while agent is blocked waiting for user decision."""
                if gate is None:
                    return
                seen: set[str] = set()
                while not stop_evt.is_set():
                    try:
                        for req in gate.get_pending(plan_id=run_id):
                            if req.action_id not in seen:
                                seen.add(req.action_id)
                                await event_queue.put({"type": "approval_request", "data": req.to_dict()})
                    except Exception:
                        pass
                    await asyncio.sleep(0.15)

            agent_task = asyncio.create_task(_run_agent())
            approval_task = asyncio.create_task(_poll_approvals())

            try:
                # First event: run context so client can track approvals
                yield f"data: {_json.dumps({'type': 'run_start', 'data': {'run_id': run_id}})}\n\n"
                while True:
                    try:
                        event = await asyncio.wait_for(event_queue.get(), timeout=30.0)
                        if event is None:
                            break
                        yield f"data: {_json.dumps(event)}\n\n"
                        if event["type"] == "error":
                            break
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
            finally:
                stop_evt.set()
                approval_task.cancel()
                agent_task.cancel()
                try:
                    _restore_env()
                except Exception:
                    pass
                try:
                    _current_scope.reset(scope_token)
                except Exception:
                    pass

        return StreamingResponse(
            _event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ── Synchronous mode ──────────────────────────────────────────
    try:
        if app_def.is_multi_agent():
            result = await runtime.run_multi_agent(app_def, body.input, variables=body_variables, secrets=app_secrets)
            await store.record_run(app_id)
            return RunAppResponse(
                success=result.success,
                output=result.output,
                error=result.error,
            )
        else:
            result = await runtime.run(app_def, body.input, variables=body_variables, secrets=app_secrets)
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
        _restore_env()
        _current_scope.reset(scope_token)


class PrepareAppResponse(BaseModel):
    """Result of preparing an app for execution."""
    app_name: str
    modules_checked: int
    modules_missing: list[str] = Field(default_factory=list)
    tools_resolved: int
    llm_warmed: bool
    memory_ready: bool
    capabilities_applied: bool
    duration_ms: float
    ready: bool


@router.post("/{app_id}/prepare", response_model=PrepareAppResponse)
async def prepare_app(
    app_id: str,
    request: Request,
    _auth: AuthDep,
):
    """Prepare an app for execution — pre-load all resources.

    Must be called after registration and before the first run.
    The daemon pre-loads:
    - All required modules (validates availability)
    - LLM provider connections (pre-warms connection pool)
    - Memory backends (health check)
    - Security capabilities and constraints

    This ensures the app runs at maximum speed when launched.
    """
    store = _get_app_store(request)
    runtime = _get_app_runtime(request)

    record = await store.get(app_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="App not found")

    # Load the app definition
    try:
        if record.file_path:
            app_def = runtime.load(record.file_path)
        elif record.config_json and record.config_json != "{}":
            app_def = runtime.load_string(record.config_json)
        else:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="App has no file path and no stored YAML.",
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Failed to load app: {e}",
        )

    # Run prepare (pre-load all resources)
    try:
        result = await runtime.prepare(app_def)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Prepare failed: {e}",
        )

    ready = len(result.get("modules_missing", [])) == 0
    if ready:
        await store.mark_prepared(app_id)

    return PrepareAppResponse(
        **result,
        ready=ready,
    )


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


# ─── Secrets management ──────────────────────────────────────────────


class SecretSetRequest(BaseModel):
    value: str = Field(..., description="Secret value (stored encrypted)")


@router.put("/{app_id}/secrets/{key}", status_code=status.HTTP_204_NO_CONTENT)
async def set_app_secret(
    app_id: str,
    key: str,
    body: SecretSetRequest,
    request: Request,
    _auth: AuthDep,
):
    """Store an encrypted secret for an app (e.g. ANTHROPIC_API_KEY)."""
    identity_store = getattr(request.app.state, "identity_store", None)
    if identity_store is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Identity store not available.")
    store = _get_app_store(request)
    if not await store.get(app_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="App not found")
    await identity_store.set_secret(app_id, key, body.value)


@router.get("/{app_id}/secrets")
async def list_app_secrets(
    app_id: str,
    request: Request,
    _auth: AuthDep,
):
    """List secret keys stored for an app (values are never exposed)."""
    identity_store = getattr(request.app.state, "identity_store", None)
    if identity_store is None:
        return []
    store = _get_app_store(request)
    if not await store.get(app_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="App not found")
    keys = await identity_store.list_secret_keys(app_id)
    return [k["key"] for k in keys]


@router.delete("/{app_id}/secrets/{key}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_app_secret(
    app_id: str,
    key: str,
    request: Request,
    _auth: AuthDep,
):
    """Delete a secret for an app."""
    identity_store = getattr(request.app.state, "identity_store", None)
    if identity_store is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Identity store not available.")
    store = _get_app_store(request)
    if not await store.get(app_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="App not found")
    await identity_store.delete_secret(app_id, key)


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


# ─── YAML parsed config ──────────────────────────────────────────────


def _parse_yaml_to_structured(config_json: str) -> dict:
    """Parse raw YAML text into structured fields for the dashboard."""
    import yaml as _yaml

    try:
        data = _yaml.safe_load(config_json) or {}
    except Exception:
        return {}

    # capabilities
    caps = data.get("capabilities", {}) or {}
    grants = caps.get("grant", []) or []
    denies = caps.get("deny", []) or []
    approvals = caps.get("approval_required", []) or []

    yaml_modules: list[str] = []
    yaml_allowed_actions: dict[str, list[str]] = {}
    for g in grants:
        mid = g.get("module") if isinstance(g, dict) else None
        if not mid:
            continue
        yaml_modules.append(mid)
        actions = g.get("actions") or []
        if actions:
            yaml_allowed_actions[mid] = list(actions)

    # security
    security = data.get("security", {}) or {}
    profile = security.get("profile", "")
    sandbox = security.get("sandbox", {}) or {}
    sandbox_paths = sandbox.get("allowed_paths", []) or [] if isinstance(sandbox, dict) else []

    # agent brain
    agent_data = data.get("agent", {}) or {}
    brain = agent_data.get("brain", {}) or {} if agent_data else {}
    yaml_agent = (
        {
            "provider": brain.get("provider", ""),
            "model": brain.get("model", ""),
            "temperature": brain.get("temperature"),
            "max_tokens": brain.get("max_tokens"),
        }
        if brain
        else None
    )

    # triggers
    triggers_raw = data.get("triggers", []) or []
    yaml_triggers = [
        {"id": t.get("id", ""), "type": t.get("type", "")}
        for t in triggers_raw
        if isinstance(t, dict)
    ]

    # variables
    variables = data.get("variables", {}) or {}

    return {
        "yaml_modules": yaml_modules,
        "yaml_allowed_actions": yaml_allowed_actions,
        "yaml_deny": denies,
        "yaml_approval_required": approvals,
        "yaml_security_profile": profile,
        "yaml_sandbox_paths": sandbox_paths,
        "yaml_agent": yaml_agent,
        "yaml_triggers": yaml_triggers,
        "yaml_variables": variables,
    }


def _patch_yaml_capabilities(
    yaml_text: str,
    allowed_modules: list[str],
    allowed_actions: dict[str, list[str]],
) -> str:
    """Patch capabilities.grant in a YAML text to match new allowed_modules/actions.

    Preserves all other YAML fields (deny, approval_required, security, agent, etc.).
    Returns the patched YAML as text.
    """
    import yaml as _yaml

    try:
        data = _yaml.safe_load(yaml_text) or {}
    except Exception:
        return yaml_text  # cannot parse, leave unchanged

    if not isinstance(data, dict):
        return yaml_text

    # Build new grant list: union of allowed_modules and allowed_actions keys
    all_module_ids = sorted(set(allowed_modules) | set(allowed_actions.keys()))
    new_grants = []
    for mid in all_module_ids:
        entry: dict = {"module": mid}
        actions = allowed_actions.get(mid) or []
        if actions:
            entry["actions"] = list(actions)
        new_grants.append(entry)

    if "capabilities" not in data or not isinstance(data.get("capabilities"), dict):
        data["capabilities"] = {}
    data["capabilities"]["grant"] = new_grants

    return _yaml.dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False)


class YamlParsedResponse(BaseModel):
    """Structured view of a registered YAML app's configuration."""
    yaml_modules: list[str] = Field(default_factory=list)
    yaml_allowed_actions: dict[str, list[str]] = Field(default_factory=dict)
    yaml_deny: list[dict] = Field(default_factory=list)
    yaml_approval_required: list[dict] = Field(default_factory=list)
    yaml_security_profile: str = ""
    yaml_sandbox_paths: list[str] = Field(default_factory=list)
    yaml_agent: dict | None = None
    yaml_triggers: list[dict] = Field(default_factory=list)
    yaml_variables: dict = Field(default_factory=dict)
    identity_modules: list[str] = Field(default_factory=list)
    identity_actions: dict[str, list[str]] = Field(default_factory=dict)
    in_sync: bool = True


@router.get("/{app_id}/parsed", response_model=YamlParsedResponse)
async def get_parsed_yaml(
    app_id: str,
    request: Request,
    _auth: AuthDep,
):
    """Return the structured YAML configuration and sync status with the Identity.

    Compares what the YAML declares (capabilities.grant) against what the
    Application identity has (allowed_modules / allowed_actions) and reports
    whether they are in sync.
    """
    store = _get_app_store(request)
    record = await store.get(app_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="App not found")

    yaml_text = record.config_json or ""
    parsed = _parse_yaml_to_structured(yaml_text)

    # Fetch identity settings
    identity_modules: list[str] = []
    identity_actions: dict[str, list[str]] = {}
    identity_store = getattr(request.app.state, "identity_store", None)
    if identity_store and record.application_id:
        try:
            identity_app = await identity_store.get_application(record.application_id)
            if identity_app:
                identity_modules = list(identity_app.allowed_modules or [])
                identity_actions = dict(identity_app.allowed_actions or {})
        except Exception:
            pass

    # Compute sync status
    yaml_module_set = set(parsed.get("yaml_modules", []))
    identity_module_set = set(identity_modules)
    yaml_actions_norm = {k: sorted(v) for k, v in parsed.get("yaml_allowed_actions", {}).items()}
    identity_actions_norm = {k: sorted(v) for k, v in identity_actions.items() if v}
    in_sync = yaml_module_set == identity_module_set and yaml_actions_norm == identity_actions_norm

    return YamlParsedResponse(
        **parsed,
        identity_modules=identity_modules,
        identity_actions=identity_actions,
        in_sync=in_sync,
    )


@router.post("/{app_id}/sync-from-yaml")
async def sync_from_yaml(
    app_id: str,
    request: Request,
    _auth: AuthDep,
):
    """Re-apply the YAML's capabilities.grant to the Identity (YAML → Identity sync).

    Use this when the Identity has drifted from the YAML definition and you
    want to restore the YAML as the source of truth.
    """
    store = _get_app_store(request)
    record = await store.get(app_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="App not found")

    yaml_text = record.config_json or ""
    if not yaml_text:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="App has no YAML content to sync from.",
        )

    parsed = _parse_yaml_to_structured(yaml_text)
    yaml_modules = parsed.get("yaml_modules", [])

    identity_store = getattr(request.app.state, "identity_store", None)
    if identity_store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Identity system is not available. Enable identity in your configuration.",
        )
    if not record.application_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="This app has no linked Application identity. Re-register the app to create one.",
        )

    try:
        result = await identity_store.update_application(
            record.application_id,
            enabled=True,
            allowed_modules=yaml_modules,
            allowed_actions=parsed.get("yaml_allowed_actions", {}),
        )
        if result is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Application identity '{record.application_id}' not found in identity store.",
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to sync identity: {e}",
        )

    return {
        "synced": True,
        "yaml_modules": yaml_modules,
        "yaml_allowed_actions": parsed.get("yaml_allowed_actions", {}),
    }


@router.post("/{app_id}/sync-to-yaml")
async def sync_to_yaml(
    app_id: str,
    request: Request,
    _auth: AuthDep,
):
    """Write the Identity's current allowed_modules/actions back into the YAML (Identity → YAML sync).

    Patches capabilities.grant in the stored YAML text while preserving all other
    sections (deny, approval_required, security, agent, triggers, variables).
    Called automatically after saving module access from the dashboard.
    """
    store = _get_app_store(request)
    record = await store.get(app_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="App not found")

    yaml_text = record.config_json or ""
    if not yaml_text or yaml_text == "{}":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="App has no YAML content to update.",
        )

    identity_store = getattr(request.app.state, "identity_store", None)
    if identity_store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Identity system is not available.",
        )

    linked_id = record.application_id
    if not linked_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="This app has no linked Application identity.",
        )

    identity_app = await identity_store.get_application(linked_id)
    if identity_app is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Application identity '{linked_id}' not found.",
        )

    allowed_modules = list(identity_app.allowed_modules or [])
    allowed_actions = dict(identity_app.allowed_actions or {})

    patched_yaml = _patch_yaml_capabilities(yaml_text, allowed_modules, allowed_actions)

    try:
        await store.update_yaml(app_id, patched_yaml)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save patched YAML: {e}",
        )

    return {
        "synced": True,
        "allowed_modules": allowed_modules,
        "allowed_actions": allowed_actions,
    }


@router.get("/{app_id}/yaml")
async def download_yaml(
    app_id: str,
    request: Request,
    _auth: AuthDep,
):
    """Return the raw YAML content of a registered app as a downloadable file."""
    from fastapi.responses import Response as FastAPIResponse

    store = _get_app_store(request)
    record = await store.get(app_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="App not found")

    yaml_text = record.config_json or ""
    if not yaml_text:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This app has no YAML content stored.",
        )

    filename = f"{record.name.replace(' ', '_')}.app.yaml"
    return FastAPIResponse(
        content=yaml_text,
        media_type="text/yaml",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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
    decision: str = Field(..., description="approve, reject, skip, modify, approve_always, message")
    modified_params: dict[str, Any] | None = Field(None, description="Modified params (for 'modify' decision)")
    reason: str | None = Field(None, description="Reason for rejection, or feedback message (for 'message' decision)")
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
            detail=f"Invalid decision: {body.decision}. Valid: approve, reject, skip, modify, approve_always, message",
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


# ─── WebSocket — bidirectional app run session ────────────────────────────
#
# Protocol (JSON messages):
#
#  Client → Server:
#    {"type": "start",  "input": "...", "variables": {}}  ← begins execution
#    {"type": "decide", "action_id": "...", "decision": "approve|reject|skip|approve_always", "reason": ""}
#    {"type": "stop"}                                      ← request graceful stop
#    {"type": "ping"}
#
#  Server → Client:
#    {"type": "run_start",        "data": {"run_id": "..."}}
#    {"type": "thinking",         "data": {"text": "..."}}
#    {"type": "tool_call",        "data": {"name": "...", "arguments": {}}}
#    {"type": "tool_result",      "data": {"output": "...", "is_error": false}}
#    {"type": "text",             "data": {"text": "..."}}
#    {"type": "approval_request", "data": {approval request fields}}
#    {"type": "done",             "data": {"stop_reason": "..."}}
#    {"type": "error",            "data": {"error": "..."}}
#    {"type": "pong"}


@router.websocket("/{app_id}/ws")
async def ws_app_run(app_id: str, websocket: WebSocket):
    """Bidirectional WebSocket for real-time app execution.

    Connect, send a ``start`` message, then receive streaming events.
    Send ``decide`` messages to handle pending approvals.
    Send ``stop`` to interrupt execution.

    This is the primary integration point for developers building UIs
    or CLI tools on top of the LLMOS App Language runtime.
    """
    await websocket.accept()

    # Access app state through websocket.app (Starlette convention)
    app_state = websocket.app.state
    store = getattr(app_state, "app_store", None)
    runtime = getattr(app_state, "app_runtime", None)
    gate = getattr(app_state, "approval_gate", None)
    identity_resolver = getattr(app_state, "identity_resolver", None)

    if store is None or runtime is None:
        await websocket.send_json({"type": "error", "data": {"error": "App runtime not available."}})
        await websocket.close(code=1011)
        return

    record = await store.get(app_id)
    if not record:
        await websocket.send_json({"type": "error", "data": {"error": f"App '{app_id}' not found."}})
        await websocket.close(code=1008)
        return

    if not record.prepared:
        await websocket.send_json({"type": "error", "data": {"error": "App is not prepared. Call POST /apps/{app_id}/prepare first."}})
        await websocket.close(code=1008)
        return

    # Wait for the start message
    try:
        init = await asyncio.wait_for(websocket.receive_json(), timeout=30.0)
    except asyncio.TimeoutError:
        await websocket.send_json({"type": "error", "data": {"error": "Timeout waiting for start message."}})
        await websocket.close(code=1008)
        return
    except WebSocketDisconnect:
        return

    if init.get("type") != "start":
        await websocket.send_json({"type": "error", "data": {"error": "First message must be {\"type\": \"start\", \"input\": \"...\"}"}})
        await websocket.close(code=1008)
        return

    user_input = init.get("input", "")
    variables = init.get("variables", {})

    # Load app definition
    try:
        if record.file_path:
            app_def = runtime.load(record.file_path)
        elif record.config_json and record.config_json != "{}":
            app_def = runtime.load_string(record.config_json)
        else:
            await websocket.send_json({"type": "error", "data": {"error": "App has no YAML content."}})
            await websocket.close(code=1011)
            return
    except Exception as exc:
        await websocket.send_json({"type": "error", "data": {"error": f"Failed to load app: {exc}"}})
        await websocket.close(code=1011)
        return

    # Load secrets
    secrets: dict[str, str] = {}
    identity_store = getattr(app_state, "identity_store", None)
    if identity_store is not None:
        try:
            secrets = await identity_store.get_secrets(app_id)
        except Exception:
            pass

    # Set up execution context
    run_id = _uuid.uuid4().hex
    resolved_identity = None
    if identity_resolver:
        try:
            resolved_identity = await identity_resolver.resolve(
                authorization=None, x_app=app_id
            )
        except Exception:
            pass
    if resolved_identity is None:
        from llmos_bridge.identity.models import IdentityContext
        resolved_identity = IdentityContext(app_id=app_id)

    scope_token = _current_scope.set(_ExecutionScope(identity=resolved_identity, run_id=run_id))

    event_queue: asyncio.Queue = asyncio.Queue()
    stop_requested = asyncio.Event()
    stop_evt = asyncio.Event()

    async def _run_agent():
        try:
            async for ev in runtime.stream(app_def, user_input, variables=variables, secrets=secrets):
                if stop_requested.is_set():
                    break
                await event_queue.put({"type": ev.type, "data": ev.data})
            await store.record_run(app_id)
        except Exception as exc:
            await event_queue.put({"type": "error", "data": {"error": str(exc)}})
        finally:
            stop_evt.set()
            await event_queue.put(None)

    async def _poll_approvals():
        if gate is None:
            return
        seen: set[str] = set()
        while not stop_evt.is_set():
            try:
                for req in gate.get_pending(plan_id=run_id):
                    if req.action_id not in seen:
                        seen.add(req.action_id)
                        await event_queue.put({"type": "approval_request", "data": req.to_dict()})
            except Exception:
                pass
            await asyncio.sleep(0.15)

    async def _receive_commands():
        """Process incoming control messages from the client."""
        while not stop_evt.is_set():
            try:
                msg = await websocket.receive_json()
                msg_type = msg.get("type", "")

                if msg_type == "ping":
                    await websocket.send_json({"type": "pong"})

                elif msg_type == "stop":
                    stop_requested.set()

                elif msg_type == "decide" and gate is not None:
                    from llmos_bridge.orchestration.approval import ApprovalDecision, ApprovalResponse
                    action_id = msg.get("action_id", "")
                    try:
                        decision = ApprovalDecision(msg.get("decision", "reject"))
                    except ValueError:
                        decision = ApprovalDecision.REJECT
                    response = ApprovalResponse(
                        decision=decision,
                        reason=msg.get("reason", ""),
                        approved_by=msg.get("approved_by", "ws_client"),
                    )
                    gate.submit_decision(run_id, action_id, response)

            except WebSocketDisconnect:
                stop_requested.set()
                stop_evt.set()
                break
            except Exception:
                break

    agent_task = asyncio.create_task(_run_agent())
    approval_task = asyncio.create_task(_poll_approvals())
    recv_task = asyncio.create_task(_receive_commands())

    try:
        await websocket.send_json({"type": "run_start", "data": {"run_id": run_id}})

        while True:
            try:
                event = await asyncio.wait_for(event_queue.get(), timeout=30.0)
                if event is None:
                    break
                await websocket.send_json(event)
                if event["type"] in ("error",):
                    break
            except asyncio.TimeoutError:
                try:
                    await websocket.send_json({"type": "ping"})
                except Exception:
                    break
    except WebSocketDisconnect:
        pass
    finally:
        stop_requested.set()
        stop_evt.set()
        approval_task.cancel()
        agent_task.cancel()
        recv_task.cancel()
        _current_scope.reset(scope_token)
        try:
            await websocket.close()
        except Exception:
            pass
