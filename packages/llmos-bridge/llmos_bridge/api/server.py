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
    RateLimitMiddleware,
    RequestIDMiddleware,
    build_error_handler,
)
from llmos_bridge.api.routes import health, modules, plans, plan_groups, websocket, triggers as triggers_router, recordings as recordings_router, context as context_router, intent_verifier as intent_verifier_router, scanners as scanners_router
from llmos_bridge.api.routes.websocket import WebSocketEventBus, manager as ws_manager
from llmos_bridge.config import Settings, get_settings
from llmos_bridge.events.bus import FanoutEventBus, LogEventBus
from llmos_bridge.exceptions import LLMOSError
from llmos_bridge.logging import configure_logging, get_logger
from llmos_bridge.memory.store import KeyValueStore
from llmos_bridge.modules.api_http import ApiHttpModule
from llmos_bridge.modules.browser import BrowserModule
from llmos_bridge.modules.database import DatabaseModule
from llmos_bridge.modules.database_gateway import DatabaseGatewayModule
from llmos_bridge.modules.excel import ExcelModule
from llmos_bridge.modules.gui import GUIModule
from llmos_bridge.modules.filesystem import FilesystemModule
from llmos_bridge.modules.os_exec import OSExecModule
from llmos_bridge.modules.powerpoint import PowerPointModule
from llmos_bridge.modules.registry import ModuleRegistry
from llmos_bridge.modules.recording import RecordingModule
from llmos_bridge.modules.security import SecurityModule
from llmos_bridge.modules.triggers import TriggerModule
from llmos_bridge.modules.word import WordModule
from llmos_bridge.orchestration.executor import PlanExecutor
from llmos_bridge.orchestration.resource_manager import ResourceManager
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
    app.add_middleware(RateLimitMiddleware, max_per_minute=settings.server.rate_limit_per_minute)
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
    app.include_router(plan_groups.router)
    app.include_router(modules.router)
    app.include_router(websocket.router)
    app.include_router(triggers_router.router)
    app.include_router(recordings_router.router)
    app.include_router(context_router.router)
    app.include_router(intent_verifier_router.router)
    app.include_router(scanners_router.router)

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

        # Build OS-level permission system (SecurityManager).
        from llmos_bridge.security.manager import SecurityManager
        from llmos_bridge.security.permission_store import PermissionStore
        from llmos_bridge.security.permissions import PermissionManager
        from llmos_bridge.security.rate_limiter import ActionRateLimiter

        sec_cfg = settings.security_advanced
        permission_store = PermissionStore(sec_cfg.permissions_db_path)
        await permission_store.init()
        permission_manager = PermissionManager(
            store=permission_store,
            audit=audit_logger,
            auto_grant_low_risk=sec_cfg.auto_grant_low_risk,
        )
        rate_limiter = ActionRateLimiter()

        # Build IntentVerifier (LLM-based security layer — Couche 1).
        intent_verifier = _build_intent_verifier(settings.intent_verifier, audit_logger)

        security_manager = SecurityManager(
            permission_manager=permission_manager,
            rate_limiter=rate_limiter,
            audit=audit_logger,
            intent_verifier=intent_verifier,
        )

        # Build scanner pipeline (fast heuristic + optional ML scanners).
        scanner_pipeline = _build_scanner_pipeline(settings, audit_logger)

        # Inject SecurityManager into all registered modules.
        if sec_cfg.enable_decorators:
            for mod in registry.list_available():
                module = registry.get(mod)
                if module is not None and hasattr(module, "set_security"):
                    module.set_security(security_manager)

            # Register SecurityModule (only useful when decorators are enforced).
            security_module = SecurityModule()
            security_module.set_security_manager(security_manager)
            registry.register_instance(security_module)

        # Startup security audit — warn about any action method that has no
        # security decorator.  This makes decorator gaps visible in production
        # logs without blocking startup.
        if sec_cfg.enable_decorators:
            from llmos_bridge.security.decorators import collect_security_metadata

            for mod_id in registry.list_available():
                mod_instance = registry.get(mod_id)
                if mod_instance is None:
                    continue
                for attr_name in dir(mod_instance):
                    if not attr_name.startswith("_action_"):
                        continue
                    handler = getattr(mod_instance, attr_name, None)
                    if handler is None or not callable(handler):
                        continue
                    meta = collect_security_metadata(handler)
                    if not meta:
                        log.warning(
                            "undecorated_action",
                            module=mod_id,
                            action=attr_name.removeprefix("_action_"),
                        )

        log.info("security_manager_started", auto_grant_low_risk=sec_cfg.auto_grant_low_risk)

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

        # Build resource manager for per-module concurrency limits.
        resource_manager = ResourceManager(
            limits=settings.resources.module_limits,
            default_limit=settings.resources.default_concurrency,
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
            resource_manager=resource_manager,
            fallback_chains=settings.modules.fallbacks,
            max_result_size=settings.server.max_result_size,
            intent_verifier=intent_verifier,
            scanner_pipeline=scanner_pipeline,
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

        # Start auto-purge background task.
        import asyncio as _aio

        async def _auto_purge_loop() -> None:
            """Periodically purge old completed/failed plans."""
            retention_secs = settings.server.plan_retention_hours * 3600
            interval = 3600  # Run every hour.
            while True:
                await _aio.sleep(interval)
                try:
                    purged = await state_store.purge_old_plans(retention_secs)
                    if purged > 0:
                        log.info("auto_purge_completed", plans_purged=purged)
                except Exception as exc:
                    log.warning("auto_purge_failed", error=str(exc))

        purge_task = _aio.create_task(_auto_purge_loop())

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
        app.state.security_manager = security_manager
        app.state.permission_store = permission_store
        app.state.intent_verifier = intent_verifier  # None if not configured
        app.state.scanner_pipeline = scanner_pipeline  # None if disabled
        app.state.purge_task = purge_task  # Keep reference to prevent GC

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
        if hasattr(app.state, "purge_task"):
            app.state.purge_task.cancel()
        if hasattr(app.state, "trigger_daemon") and app.state.trigger_daemon is not None:
            await app.state.trigger_daemon.stop()
        if hasattr(app.state, "workflow_recorder") and app.state.workflow_recorder is not None:
            await app.state.workflow_recorder._store.close()
        if hasattr(app.state, "permission_store") and app.state.permission_store is not None:
            await app.state.permission_store.close()
        if hasattr(app.state, "scanner_pipeline") and app.state.scanner_pipeline is not None:
            await app.state.scanner_pipeline.registry.close_all()
        if hasattr(app.state, "intent_verifier") and app.state.intent_verifier is not None:
            await app.state.intent_verifier.close()
        if hasattr(app.state, "state_store"):
            await app.state.state_store.close()
        if hasattr(app.state, "kv_store"):
            await app.state.kv_store.close()

    return app


def _build_intent_verifier(cfg: Any, audit_logger: AuditLogger) -> Any:
    """Build an IntentVerifier with registry, composer, and real LLM provider.

    Returns None if intent verification is disabled.
    Supports custom verifier classes via ``custom_verifier_class`` config.
    """
    if not cfg.enabled:
        return None

    from llmos_bridge.security.intent_verifier import IntentVerifier, ThreatType
    from llmos_bridge.security.prompt_composer import PromptComposer
    from llmos_bridge.security.providers import build_provider
    from llmos_bridge.security.threat_categories import (
        ThreatCategory,
        ThreatCategoryRegistry,
    )

    # Build threat category registry (built-in + custom from config).
    registry = ThreatCategoryRegistry()
    registry.register_builtins()

    # Apply disabled categories.
    for cat_id in cfg.disabled_threat_categories:
        registry.disable(cat_id)

    # Register custom categories from config.
    for custom in cfg.custom_threat_categories:
        try:
            threat_type = ThreatType(custom.threat_type)
        except ValueError:
            threat_type = ThreatType.CUSTOM
        registry.register(ThreatCategory(
            id=custom.id,
            name=custom.name,
            description=custom.description,
            threat_type=threat_type,
            enabled=True,
            builtin=False,
        ))

    # Build prompt composer.
    composer = PromptComposer(
        category_registry=registry,
        custom_suffix=cfg.custom_system_prompt_suffix,
    )

    # Build LLM client.
    llm_client = build_provider(cfg)

    # Support custom IntentVerifier subclass.
    verifier_cls = IntentVerifier
    if cfg.custom_verifier_class:
        import importlib

        module_path, _, class_name = cfg.custom_verifier_class.rpartition(".")
        if module_path:
            try:
                mod = importlib.import_module(module_path)
                verifier_cls = getattr(mod, class_name)
            except (ImportError, AttributeError) as exc:
                log.error(
                    "custom_verifier_class_load_failed",
                    class_path=cfg.custom_verifier_class,
                    error=str(exc),
                )

    verifier = verifier_cls(
        llm_client=llm_client,
        audit_logger=audit_logger,
        prompt_composer=composer,
        category_registry=registry,
        enabled=True,
        strict=cfg.strict,
        cache_size=cfg.cache_size,
        cache_ttl=cfg.cache_ttl_seconds,
        timeout=cfg.timeout_seconds,
        model=cfg.model,
    )

    log.info(
        "intent_verifier_started",
        provider=cfg.provider,
        model=cfg.model,
        strict=cfg.strict,
        categories_enabled=len(registry.list_enabled()),
        custom_categories=len(cfg.custom_threat_categories),
    )

    return verifier


def _register_builtin_modules(registry: ModuleRegistry, settings: Settings) -> None:
    """Register built-in modules according to the active configuration."""
    builtin_map = {
        "filesystem": FilesystemModule,
        "os_exec": OSExecModule,
        "excel": ExcelModule,
        "word": WordModule,
        "powerpoint": PowerPointModule,
        "api_http": ApiHttpModule,
        "database": DatabaseModule,
        "db_gateway": DatabaseGatewayModule,
        "browser": BrowserModule,
        "gui": GUIModule,
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


def _build_scanner_pipeline(
    settings: Settings, audit_logger: AuditLogger
) -> Any:
    """Build a SecurityPipeline from config.  Returns None if disabled."""
    cfg = settings.scanner_pipeline
    if not cfg.enabled:
        log.info("scanner_pipeline_disabled")
        return None

    from llmos_bridge.security.scanners import (
        HeuristicScanner,
        ScannerRegistry,
        SecurityPipeline,
    )

    registry = ScannerRegistry()

    # Always register the built-in heuristic scanner.
    if cfg.heuristic_enabled:
        heuristic = HeuristicScanner()

        # Disable specific patterns from config.
        for pattern_id in cfg.heuristic_disabled_patterns:
            heuristic.disable_pattern(pattern_id)

        # Add extra user-defined patterns.
        from llmos_bridge.security.scanners.heuristic import PatternRule
        import re

        for extra in cfg.heuristic_extra_patterns:
            try:
                rule = PatternRule(
                    id=extra.id,
                    category=extra.category,
                    pattern=re.compile(extra.pattern, re.IGNORECASE),
                    severity=extra.severity,
                    description=extra.description,
                )
                heuristic.add_pattern(rule)
            except Exception as exc:
                log.warning(
                    "heuristic_extra_pattern_failed",
                    pattern_id=extra.id,
                    error=str(exc),
                )

        registry.register(heuristic)

    # Optional: LLM Guard adapter.
    if cfg.llm_guard_enabled:
        try:
            from llmos_bridge.security.scanners.adapters.llm_guard import (
                LLMGuardScanner,
            )

            scanner = LLMGuardScanner(scanners=cfg.llm_guard_scanners or None)
            registry.register(scanner)
        except Exception as exc:
            log.warning("llm_guard_scanner_init_failed", error=str(exc))

    # Optional: Meta Prompt Guard adapter.
    if cfg.prompt_guard_enabled:
        try:
            from llmos_bridge.security.scanners.adapters.prompt_guard import (
                PromptGuardScanner,
            )

            kwargs: dict[str, Any] = {}
            if cfg.prompt_guard_model:
                kwargs["model_name"] = cfg.prompt_guard_model
            scanner = PromptGuardScanner(**kwargs)
            registry.register(scanner)
        except Exception as exc:
            log.warning("prompt_guard_scanner_init_failed", error=str(exc))

    pipeline = SecurityPipeline(
        registry=registry,
        audit_logger=audit_logger,
        fail_fast=cfg.fail_fast,
        reject_threshold=cfg.reject_threshold,
        warn_threshold=cfg.warn_threshold,
        enabled=True,
    )

    log.info(
        "scanner_pipeline_started",
        scanner_count=len(registry.list_all()),
        scanners=[s.scanner_id for s in registry.list_all()],
        fail_fast=cfg.fail_fast,
        reject_threshold=cfg.reject_threshold,
    )

    return pipeline
