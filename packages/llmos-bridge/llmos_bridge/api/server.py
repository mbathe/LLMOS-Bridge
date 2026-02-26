"""API layer — FastAPI application factory.

``create_app()`` is the single entry point for building the FastAPI app.
All dependencies are wired here so that tests can override them by
calling ``create_app()`` with custom objects.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from llmos_bridge import __version__
from llmos_bridge.api.middleware import (
    AccessLogMiddleware,
    RequestIDMiddleware,
    build_error_handler,
)
from llmos_bridge.api.routes import health, modules, plans, websocket, triggers as triggers_router, recordings as recordings_router, context as context_router
from llmos_bridge.api.routes.websocket import WebSocketEventBus, manager as ws_manager
from llmos_bridge.config import Settings, get_settings
from llmos_bridge.events.bus import FanoutEventBus, LogEventBus
from llmos_bridge.exceptions import LLMOSError
from llmos_bridge.logging import configure_logging, get_logger
from llmos_bridge.memory.store import KeyValueStore
from llmos_bridge.modules.api_http import ApiHttpModule
from llmos_bridge.modules.excel import ExcelModule
from llmos_bridge.modules.filesystem import FilesystemModule
from llmos_bridge.modules.os_exec import OSExecModule
from llmos_bridge.modules.powerpoint import PowerPointModule
from llmos_bridge.modules.registry import ModuleRegistry
from llmos_bridge.modules.recording import RecordingModule
from llmos_bridge.modules.triggers import TriggerModule
from llmos_bridge.modules.word import WordModule
from llmos_bridge.orchestration.executor import PlanExecutor
from llmos_bridge.orchestration.state import PlanStateStore
from llmos_bridge.security.audit import AuditLogger
from llmos_bridge.security.guard import PermissionGuard
from llmos_bridge.security.profiles import get_profile_config
from llmos_bridge.security.sanitizer import OutputSanitizer

log = get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        settings: Optional settings override (used in tests).

    Returns:
        A fully configured FastAPI application instance.
    """
    if settings is None:
        settings = get_settings()

    configure_logging(
        level=settings.logging.level,
        format=settings.logging.format,
        log_file=str(settings.logging.file) if settings.logging.file else None,
    )

    app = FastAPI(
        title="LLMOS Bridge",
        description="Local daemon bridging LLMs to OS, applications, and devices via IML v2.",
        version=__version__,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    # Middleware (order matters — outermost applied last)
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost", "http://127.0.0.1"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Exception handlers
    app.add_exception_handler(LLMOSError, build_error_handler())  # type: ignore[arg-type]

    # Routers
    app.include_router(health.router)
    app.include_router(plans.router)
    app.include_router(modules.router)
    app.include_router(websocket.router)
    app.include_router(triggers_router.router)
    app.include_router(recordings_router.router)
    app.include_router(context_router.router)

    # Startup / shutdown lifecycle
    @app.on_event("startup")
    async def startup() -> None:
        log.info("daemon_starting", version=__version__)

        # Build the module registry.
        registry = ModuleRegistry()
        _register_builtin_modules(registry, settings)

        # Initialise state store.
        state_store = PlanStateStore(settings.memory.state_db_path)
        await state_store.init()

        # Initialise key-value memory store.
        kv_store = KeyValueStore(settings.memory.state_db_path)
        await kv_store.init()

        # Build security stack.
        profile_config = get_profile_config(settings.security.permission_profile)
        guard = PermissionGuard(
            profile=profile_config,
            require_approval_for=settings.security.require_approval_for,
            sandbox_paths=settings.security.sandbox_paths,
        )

        # Build event bus — fanout to NDJSON file + live WebSocket clients.
        ws_bus = WebSocketEventBus(ws_manager)
        if settings.logging.audit_file:
            event_bus = FanoutEventBus([LogEventBus(settings.logging.audit_file), ws_bus])
        else:
            event_bus = ws_bus

        audit_logger = AuditLogger(bus=event_bus)
        sanitizer = OutputSanitizer()

        # Build perception pipeline (optional — requires mss/pytesseract).
        perception_pipeline = None
        if settings.perception.enabled:
            try:
                from llmos_bridge.perception.pipeline import PerceptionPipeline
                from llmos_bridge.perception.ocr import OCREngine
                from llmos_bridge.perception.screen import ScreenCapture

                capture = ScreenCapture()
                ocr = OCREngine() if settings.perception.ocr_enabled else None
                perception_pipeline = PerceptionPipeline(
                    capture=capture,
                    ocr=ocr,
                    save_screenshots=True,
                    save_dir=str(settings.memory.state_db_path.parent / "screenshots"),
                )
                log.info("perception_pipeline_started", ocr_enabled=settings.perception.ocr_enabled)
            except ImportError as exc:
                log.warning(
                    "perception_unavailable",
                    error=str(exc),
                    hint="Install mss and pytesseract for perception support.",
                )

        # Build approval gate.
        from llmos_bridge.orchestration.approval import ApprovalGate

        approval_gate = ApprovalGate(
            default_timeout=float(settings.security.approval_timeout_seconds),
            default_timeout_behavior=settings.security.approval_timeout_behavior,
        )

        executor = PlanExecutor(
            module_registry=registry,
            guard=guard,
            state_store=state_store,
            audit_logger=audit_logger,
            sanitizer=sanitizer,
            kv_store=kv_store,
            perception_pipeline=perception_pipeline,
            approval_gate=approval_gate,
        )

        # Initialise TriggerDaemon (optional subsystem — disabled by default).
        trigger_daemon = None
        if settings.triggers.enabled:
            from llmos_bridge.triggers.daemon import TriggerDaemon
            from llmos_bridge.triggers.store import TriggerStore
            from llmos_bridge.events.session import SessionContextPropagator

            trigger_store = TriggerStore(settings.triggers.db_path)
            await trigger_store.init()
            session_propagator = SessionContextPropagator()
            trigger_daemon = TriggerDaemon(
                store=trigger_store,
                event_bus=event_bus,
                executor=executor,
                session_propagator=session_propagator,
                max_concurrent_plans=settings.triggers.max_concurrent_plans,
            )
            await trigger_daemon.start()

            # Wire TriggerDaemon into TriggerModule (if registered)
            trigger_module = registry.get("triggers") if hasattr(registry, "get") else None
            if trigger_module is not None and hasattr(trigger_module, "set_daemon"):
                trigger_module.set_daemon(trigger_daemon)

            log.info("trigger_daemon_started", enabled_types=settings.triggers.enabled_types)

        # Initialise WorkflowRecorder (Shadow Recorder — optional subsystem).
        workflow_recorder = None
        if settings.recording.enabled:
            from llmos_bridge.recording.recorder import WorkflowRecorder
            from llmos_bridge.recording.store import RecordingStore

            recording_store = RecordingStore(settings.recording.db_path)
            await recording_store.init()
            workflow_recorder = WorkflowRecorder(store=recording_store)

            # Wire WorkflowRecorder into RecordingModule (if registered).
            recording_module = registry.get("recording") if hasattr(registry, "get") else None
            if recording_module is not None and hasattr(recording_module, "set_recorder"):
                recording_module.set_recorder(workflow_recorder)

            log.info("workflow_recorder_started", db_path=str(settings.recording.db_path))

        # Attach to app state for dependency injection.
        app.state.settings = settings
        app.state.module_registry = registry
        app.state.state_store = state_store
        app.state.kv_store = kv_store
        app.state.permission_guard = guard
        app.state.audit_logger = audit_logger
        app.state.plan_executor = executor
        app.state.approval_gate = approval_gate
        app.state.trigger_daemon = trigger_daemon  # None if triggers.enabled=False
        app.state.workflow_recorder = workflow_recorder  # None if recording.enabled=False

        log.info(
            "daemon_ready",
            host=settings.server.host,
            port=settings.server.port,
            modules=registry.list_available(),
            profile=settings.security.permission_profile,
        )

    @app.on_event("shutdown")
    async def shutdown() -> None:
        log.info("daemon_stopping")
        if hasattr(app.state, "trigger_daemon") and app.state.trigger_daemon is not None:
            await app.state.trigger_daemon.stop()
        if hasattr(app.state, "workflow_recorder") and app.state.workflow_recorder is not None:
            await app.state.workflow_recorder._store.close()
        if hasattr(app.state, "state_store"):
            await app.state.state_store.close()
        if hasattr(app.state, "kv_store"):
            await app.state.kv_store.close()

    return app


def _register_builtin_modules(registry: ModuleRegistry, settings: Settings) -> None:
    """Register built-in modules according to the active configuration."""
    builtin_map = {
        "filesystem": FilesystemModule,
        "os_exec": OSExecModule,
        "excel": ExcelModule,
        "word": WordModule,
        "powerpoint": PowerPointModule,
        "api_http": ApiHttpModule,
        "triggers": TriggerModule,
        "recording": RecordingModule,
    }
    active = settings.active_modules()
    for module_id, module_class in builtin_map.items():
        if module_id in active:
            try:
                registry.register(module_class)
            except Exception as exc:
                log.warning("builtin_module_register_failed", module_id=module_id, error=str(exc))
