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
from llmos_bridge.api.routes import stream as stream_router
from llmos_bridge.api.routes import (
    admin_modules as admin_modules_router,
    admin_hub as admin_hub_router,
    admin_security as admin_security_router,
    admin_system as admin_system_router,
)
from llmos_bridge.api.routes import applications as applications_router
from llmos_bridge.api.routes import cluster as cluster_router
from llmos_bridge.api.routes.websocket import WebSocketEventBus, manager as ws_manager
from llmos_bridge.config import Settings, get_settings
from llmos_bridge.events.bus import FanoutEventBus, LogEventBus
from llmos_bridge.exceptions import LLMOSError, ModuleLoadError
from llmos_bridge.logging import configure_logging, get_logger
from llmos_bridge.memory.store import KeyValueStore
from llmos_bridge.modules.api_http import ApiHttpModule
from llmos_bridge.modules.browser import BrowserModule
from llmos_bridge.modules.database import DatabaseModule
from llmos_bridge.modules.database_gateway import DatabaseGatewayModule
from llmos_bridge.modules.excel import ExcelModule
from llmos_bridge.modules.gui import GUIModule
from llmos_bridge.modules.iot import IoTModule
from llmos_bridge.modules.filesystem import FilesystemModule
from llmos_bridge.modules.os_exec import OSExecModule
from llmos_bridge.modules.powerpoint import PowerPointModule
from llmos_bridge.modules.registry import ModuleRegistry
from llmos_bridge.modules.recording import RecordingModule
from llmos_bridge.modules.security import SecurityModule
from llmos_bridge.modules.triggers import TriggerModule
from llmos_bridge.modules.computer_control import ComputerControlModule
from llmos_bridge.modules.perception_vision import OmniParserModule
from llmos_bridge.modules.window_tracker import WindowTrackerModule
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
        allow_origins=["http://localhost", "http://127.0.0.1", "http://localhost:3000"],
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
    app.include_router(admin_modules_router.router)
    app.include_router(admin_hub_router.router)
    app.include_router(admin_security_router.router)
    app.include_router(admin_system_router.router)
    app.include_router(stream_router.router)
    app.include_router(applications_router.router)
    app.include_router(cluster_router.router)

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

        # Build event bus — two-bus architecture for distributed mode.
        #
        # local_bus  = FanoutEventBus([LogEventBus, WebSocketEventBus])
        #   → consumed by dashboard, audit file, SSE, listeners
        #
        # event_bus  = FanoutEventBus([local_bus, RedisStreamsBus])   (if redis enabled)
        #            = local_bus                                       (standalone)
        #   → all producers emit here
        #
        # The EventRebroadcaster reads from Redis and writes to local_bus ONLY,
        # preventing infinite loops (local→Redis→local→Redis→...).
        from llmos_bridge.events.bus import EventBus as _EventBusABC

        ws_bus = WebSocketEventBus(ws_manager)
        log_bus = LogEventBus(settings.logging.audit_file) if settings.logging.audit_file else None
        local_backends: list[_EventBusABC] = [b for b in [log_bus, ws_bus] if b is not None]
        local_bus: _EventBusABC = (
            FanoutEventBus(local_backends) if len(local_backends) > 1 else local_backends[0]
        )

        # Add Redis Streams backend if enabled (fully optional).
        redis_bus = None
        rebroadcaster = None
        if settings.redis.enabled:
            from llmos_bridge.events.redis_bus import RedisStreamsBus

            node_name = settings.redis.node_name or settings.node.node_id
            redis_bus = RedisStreamsBus(
                settings.redis.url, node_name, settings.redis.max_stream_length,
            )
            await redis_bus.connect()
            event_bus: _EventBusABC = FanoutEventBus([local_bus, redis_bus])

            from llmos_bridge.cluster.rebroadcaster import EventRebroadcaster

            rebroadcaster = EventRebroadcaster(
                redis_url=settings.redis.url,
                local_bus=local_bus,  # ← writes to local_bus, NOT full event_bus
                node_name=node_name,
                consumer_group=settings.redis.consumer_group,
            )
            await rebroadcaster.start()
            log.info("redis_event_bus_enabled", node_name=node_name)
        else:
            event_bus = local_bus

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

        # Build PermissionProxy for remote nodes (mode="node").
        permission_proxy = None
        if settings.node.mode == "node" and settings.redis.enabled and settings.node.peers:
            from llmos_bridge.cluster.permission_proxy import PermissionProxy

            orch_url = settings.node.peers[0].url
            orch_token = settings.node.peers[0].api_token
            if orch_url:
                permission_proxy = PermissionProxy(
                    orchestrator_url=orch_url,
                    api_token=orch_token,
                )
                await permission_proxy.start()
                log.info("permission_proxy_started", orchestrator=orch_url)

        # Build Identity system (multi-tenant — disabled by default).
        identity_store = None
        identity_resolver = None
        if settings.identity.enabled:
            from llmos_bridge.identity.store import IdentityStore
            from llmos_bridge.identity.auth import IdentityResolver

            identity_store = IdentityStore(settings.identity.db_path)
            await identity_store.init()
            await identity_store.ensure_default_app(settings.identity.default_app_name)

            identity_resolver = IdentityResolver(
                store=identity_store,
                enabled=True,
                require_api_keys=settings.identity.require_api_keys,
            )
            log.info(
                "identity_system_started",
                require_api_keys=settings.identity.require_api_keys,
                default_app=settings.identity.default_app_name,
            )
        else:
            # Even when disabled, provide a resolver that always returns
            # the default context so routes can depend on IdentityDep
            # without conditional checks.
            from llmos_bridge.identity.auth import IdentityResolver

            identity_resolver = IdentityResolver(
                store=None,
                enabled=False,
            )

        # Build AuthorizationGuard (Phase 6 — identity-based authorization matrix).
        from llmos_bridge.identity.authorization import AuthorizationGuard

        authorization_guard = AuthorizationGuard(
            store=identity_store,
            enabled=settings.identity.enabled,
        )

        # Build NodeRegistry + node discovery (Phase 2 multi-node).
        from llmos_bridge.orchestration.nodes import LocalNode, NodeRegistry

        local_node = LocalNode(registry)
        node_registry = NodeRegistry(local_node)

        # Start node discovery and health monitoring (non-standalone only).
        discovery = None
        node_health_monitor = None
        if settings.node.mode != "standalone":
            from llmos_bridge.orchestration.discovery import NodeDiscoveryService
            from llmos_bridge.orchestration.node_health import NodeHealthMonitor

            discovery = NodeDiscoveryService(node_registry, event_bus, settings)
            await discovery.start()

            node_health_monitor = NodeHealthMonitor(
                node_registry,
                event_bus,
                interval=settings.node.heartbeat_interval,
                timeout=settings.node.heartbeat_timeout,
            )
            await node_health_monitor.start()

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

        # ---------------------------------------------------------------
        # Module Spec v2: ServiceBus + LifecycleManager + ModuleManager
        # ---------------------------------------------------------------
        from llmos_bridge.modules.service_bus import ServiceBus
        from llmos_bridge.modules.lifecycle import ModuleLifecycleManager
        from llmos_bridge.modules.context import ModuleContext
        from llmos_bridge.modules.types import ModuleType, SYSTEM_MODULE_IDS

        service_bus = ServiceBus()

        # Module Spec v3: Module state persistence for save/restore.
        from llmos_bridge.modules.state_store import ModuleStateStore

        module_state_store = ModuleStateStore(
            Path(settings.memory.state_db_path).parent / "module_state.db"
        )
        await module_state_store.init()

        lifecycle_manager = ModuleLifecycleManager(
            registry, event_bus, service_bus, state_store=module_state_store
        )
        registry.set_lifecycle_manager(lifecycle_manager)

        # Classify modules as SYSTEM or USER.
        # Tolerate load failures for modules with missing optional dependencies.
        for mod_id in list(registry.list_available()):
            try:
                mod_instance = registry.get(mod_id)
            except BaseException as exc:
                log.warning("module_skipped_at_startup", module_id=mod_id, reason=str(exc))
                continue
            if mod_id in SYSTEM_MODULE_IDS or getattr(mod_instance, "MODULE_TYPE", "user") == "system":
                lifecycle_manager.set_type(mod_id, ModuleType.SYSTEM)
            else:
                lifecycle_manager.set_type(mod_id, ModuleType.USER)

        # Register ModuleManagerModule (if enabled).
        if settings.module_manager.enabled:
            from llmos_bridge.modules.module_manager import ModuleManagerModule

            module_manager_mod = ModuleManagerModule()
            module_manager_mod.set_lifecycle_manager(lifecycle_manager)
            module_manager_mod.set_service_bus(service_bus)
            registry.register_instance(module_manager_mod)
            lifecycle_manager.set_type("module_manager", ModuleType.SYSTEM)

            # Inject security if decorators enabled.
            if sec_cfg.enable_decorators:
                module_manager_mod.set_security(security_manager)

        # Module Spec v3: Hub / Package Manager integration.
        # The installer is created when hub.enabled=True (full hub) OR
        # hub.local_install_enabled=True (local-only — no hub client needed).
        module_index = None
        module_installer = None
        hub_client = None
        _need_installer = settings.hub.enabled or settings.hub.local_install_enabled

        if _need_installer:
            from llmos_bridge.hub.index import ModuleIndex
            from llmos_bridge.hub.installer import ModuleInstaller
            from llmos_bridge.modules.signing import SignatureVerifier
            from llmos_bridge.isolation.venv_manager import VenvManager as _VenvMgr

            install_dir = Path(settings.hub.install_dir).expanduser()
            install_dir.mkdir(parents=True, exist_ok=True)

            module_index = ModuleIndex(install_dir / "modules.db")
            await module_index.init()

            # Build signature verifier + load trust store.
            sig_verifier = SignatureVerifier()
            trust_store = Path(settings.hub.trust_store_path).expanduser()
            if trust_store.exists():
                loaded = sig_verifier.load_trust_store(trust_store)
                log.info("trust_store_loaded", keys=loaded, path=str(trust_store))

            hub_venv_mgr = _VenvMgr(
                base_dir=install_dir / ".venvs",
                prefer_uv=settings.isolation.prefer_uv,
            )

            module_installer = ModuleInstaller(
                index=module_index,
                registry=registry,
                venv_manager=hub_venv_mgr,
                verifier=sig_verifier,
                require_signatures=settings.hub.require_signatures,
                install_dir=install_dir,
                lifecycle_manager=lifecycle_manager,
            )

            # Hub client — only when hub.enabled (requires registry_url).
            if settings.hub.enabled:
                from llmos_bridge.hub.client import HubClient
                hub_client = HubClient(settings.hub.registry_url)
                log.info(
                    "hub_integration_started",
                    registry_url=settings.hub.registry_url,
                    require_signatures=settings.hub.require_signatures,
                )
            else:
                log.info("local_install_enabled", install_dir=str(install_dir))

            # Reload previously installed community modules from the SQLite index.
            # This restores the registry after a daemon restart so that modules
            # installed in a previous session are still available.
            installed_modules = await module_index.list_enabled()
            for _im in installed_modules:
                install_path = Path(_im.install_path)
                if not install_path.exists():
                    log.warning(
                        "community_module_path_missing",
                        module_id=_im.module_id,
                        install_path=str(install_path),
                    )
                    await module_index.set_enabled(_im.module_id, False)
                    continue
                try:
                    registry.register_isolated(
                        module_id=_im.module_id,
                        module_class_path=_im.module_class_path,
                        venv_manager=hub_venv_mgr,
                        requirements=_im.requirements,
                        source_path=install_path,
                    )
                    log.info(
                        "community_module_restored",
                        module_id=_im.module_id,
                        version=_im.version,
                    )
                except Exception as _exc:
                    log.warning(
                        "community_module_restore_failed",
                        module_id=_im.module_id,
                        error=str(_exc),
                    )

            # Inject installer into ModuleManagerModule.
            if settings.module_manager.enabled:
                module_manager_mod.set_installer(module_installer)
                if hub_client is not None:
                    module_manager_mod.set_hub_client(hub_client)

        # Build ModuleContext for each module.
        for mod_id in registry.list_available():
            mod_instance = registry.get(mod_id)
            if hasattr(mod_instance, "set_context"):
                ctx = ModuleContext(
                    module_id=mod_id,
                    event_bus=event_bus,
                    service_bus=service_bus,
                    settings=settings,
                    security_manager=security_manager if sec_cfg.enable_decorators else None,
                )
                mod_instance.set_context(ctx)

        # Start all modules (calls on_start lifecycle hooks).
        await lifecycle_manager.start_all()

        log.info(
            "lifecycle_manager_started",
            modules=registry.list_available(),
        )

        # Build perception pipeline (optional — requires mss/pytesseract).
        perception_pipeline = None
        if settings.perception.enabled:
            try:
                from llmos_bridge.perception.pipeline import PerceptionPipeline
                from llmos_bridge.perception.ocr import OCREngine
                from llmos_bridge.perception.screen import ScreenCapture

                capture = ScreenCapture()
                ocr = OCREngine() if settings.perception.ocr_enabled else None

                # Try to attach vision module for enhanced perception.
                vision_module = None
                if registry.is_available("vision"):
                    try:
                        vision_module = registry.get("vision")
                        log.info("perception_vision_module_attached", module="vision")
                    except Exception:
                        pass

                perception_pipeline = PerceptionPipeline(
                    capture=capture,
                    ocr=ocr,
                    vision_module=vision_module,
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

        # Module Spec v3: Build policy enforcer.
        from llmos_bridge.modules.policy import PolicyEnforcer

        policy_enforcer = PolicyEnforcer(registry)

        # Module Spec v3: Build resource negotiator.
        from llmos_bridge.modules.resource_negotiator import ResourceNegotiator

        resource_negotiator = ResourceNegotiator(registry)

        # Phase 4: Smart routing — only active outside standalone mode.
        routing_config = None
        if settings.node.mode != "standalone":
            routing_config = settings.routing

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
            policy_enforcer=policy_enforcer,
            node_registry=node_registry,
            routing_config=routing_config,
            authorization=authorization_guard,
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
            try:
                trigger_module = registry.get("triggers") if hasattr(registry, "get") else None
            except Exception:
                trigger_module = None
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
            try:
                recording_module = registry.get("recording") if hasattr(registry, "get") else None
            except Exception:
                recording_module = None
            if recording_module is not None and hasattr(recording_module, "set_recorder"):
                recording_module.set_recorder(workflow_recorder)

            log.info("workflow_recorder_started", db_path=str(settings.recording.db_path))

        # Wire ComputerControlModule to the registry for dynamic module access.
        if registry.is_available("computer_control"):
            try:
                cc_module = registry.get("computer_control")
                if cc_module is not None and hasattr(cc_module, "set_registry"):
                    cc_module.set_registry(registry)
            except Exception:
                pass

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
        app.state.service_bus = service_bus
        app.state.lifecycle_manager = lifecycle_manager
        app.state.module_index = module_index  # None if hub.enabled=False
        app.state.module_installer = module_installer  # None if hub.enabled=False
        app.state.hub_client = hub_client  # None if hub.enabled=False
        app.state.module_state_store = module_state_store
        app.state.event_bus = event_bus
        app.state.resource_negotiator = resource_negotiator
        app.state.identity_store = identity_store  # None if identity.enabled=False
        app.state.identity_resolver = identity_resolver  # Always set (disabled = default context)
        app.state.authorization_guard = authorization_guard  # Always set (disabled = no-op)
        app.state.node_registry = node_registry
        app.state.discovery = discovery  # None if standalone
        app.state.node_health_monitor = node_health_monitor  # None if standalone
        app.state.redis_bus = redis_bus  # None if redis.enabled=False
        app.state.rebroadcaster = rebroadcaster  # None if redis.enabled=False
        app.state.permission_proxy = permission_proxy  # None unless mode="node" + redis
        app.state.load_tracker = executor._load_tracker  # None if standalone
        app.state.quarantine = executor._quarantine  # None if standalone

        # Start health monitor for isolated module workers.
        app.state.health_monitor = None
        if settings.isolation.enabled:
            from llmos_bridge.isolation.health import HealthMonitor
            from llmos_bridge.isolation.proxy import IsolatedModuleProxy

            health_monitor = HealthMonitor(
                check_interval=settings.isolation.health_check_interval,
            )
            for inst in registry._instances.values():
                if isinstance(inst, IsolatedModuleProxy):
                    health_monitor.register(inst)
            if health_monitor.monitored_count > 0:
                await health_monitor.start()
                app.state.health_monitor = health_monitor
                log.info("health_monitor_started", proxies=health_monitor.monitored_count)

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
        # Module Spec v2: stop all modules (calls on_stop lifecycle hooks).
        if hasattr(app.state, "lifecycle_manager") and app.state.lifecycle_manager is not None:
            await app.state.lifecycle_manager.stop_all()
        if hasattr(app.state, "health_monitor") and app.state.health_monitor is not None:
            await app.state.health_monitor.stop()
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
        if hasattr(app.state, "hub_client") and app.state.hub_client is not None:
            await app.state.hub_client.close()
        if hasattr(app.state, "module_index") and app.state.module_index is not None:
            await app.state.module_index.close()
        if hasattr(app.state, "module_state_store") and app.state.module_state_store is not None:
            await app.state.module_state_store.close()
        if hasattr(app.state, "node_health_monitor") and app.state.node_health_monitor is not None:
            await app.state.node_health_monitor.stop()
        if hasattr(app.state, "discovery") and app.state.discovery is not None:
            await app.state.discovery.stop()
        if hasattr(app.state, "rebroadcaster") and app.state.rebroadcaster is not None:
            await app.state.rebroadcaster.stop()
        if hasattr(app.state, "redis_bus") and app.state.redis_bus is not None:
            await app.state.redis_bus.close()
        if hasattr(app.state, "permission_proxy") and app.state.permission_proxy is not None:
            await app.state.permission_proxy.stop()
        if hasattr(app.state, "identity_store") and app.state.identity_store is not None:
            await app.state.identity_store.close()
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
    builtin_map: dict[str, type] = {
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
        "computer_control": ComputerControlModule,
        "window_tracker": WindowTrackerModule,
        "iot": IoTModule,
        "triggers": TriggerModule,
        "recording": RecordingModule,
    }

    # Vision module — supports custom backends via settings.vision.backend.
    active = settings.active_modules()
    if "vision" in active:
        if settings.vision.backend == "omniparser":
            _apply_vision_config(settings)
            builtin_map["vision"] = OmniParserModule
        elif settings.vision.backend == "ultra":
            _apply_ultra_vision_config(settings)
            from llmos_bridge.modules.perception_vision.ultra import UltraVisionModule  # noqa: PLC0415
            builtin_map["vision"] = UltraVisionModule
            log.info("vision_backend_ultra", model_dir=settings.vision.ultra_model_dir)
        else:
            _apply_vision_config(settings)
            custom_cls = _load_custom_vision_backend(settings.vision.backend)
            if custom_cls is not None:
                builtin_map["vision"] = custom_cls
            else:
                builtin_map["vision"] = OmniParserModule  # Fallback to default

    # --- Module isolation support ---
    # When isolation is enabled, modules declared as 'subprocess' are
    # registered via IsolatedModuleProxy instead of in-process.
    if settings.isolation.enabled:
        from pathlib import Path as _Path
        from llmos_bridge.isolation.venv_manager import VenvManager

        venv_mgr = VenvManager(
            base_dir=_Path(settings.isolation.venv_base_dir).expanduser(),
            prefer_uv=settings.isolation.prefer_uv,
        )

        for spec_key, spec in settings.isolation.modules.items():
            if spec.module_id not in active:
                continue
            if spec.isolation != "subprocess":
                continue

            # For vision modules, only register the one matching the active backend.
            if spec.module_id == "vision":
                backend = settings.vision.backend
                if backend == "omniparser" and "omniparser" not in spec_key:
                    continue
                if backend == "ultra" and "ultra" not in spec_key:
                    continue

            # Remove from in-process map — it will be registered as isolated.
            builtin_map.pop(spec.module_id, None)

            try:
                registry.register_isolated(
                    module_id=spec.module_id,
                    module_class_path=spec.module_class_path,
                    venv_manager=venv_mgr,
                    requirements=spec.requirements,
                    env_vars=spec.env_vars,
                    timeout=spec.timeout,
                    max_restarts=spec.max_restarts,
                )
                log.info("module_isolated", module_id=spec.module_id, spec_key=spec_key)
            except Exception as exc:
                log.warning("isolated_module_register_failed", spec_key=spec_key, error=str(exc))

    for module_id, module_class in builtin_map.items():
        if module_id in active:
            try:
                registry.register(module_class)
            except Exception as exc:
                log.warning("builtin_module_register_failed", module_id=module_id, error=str(exc))


def _apply_vision_config(settings: Settings) -> None:
    """Set environment variables from VisionConfig for OmniParser."""
    import os as _os

    # Note: LLMOS_OMNIPARSER_PATH no longer used (OmniParser is bundled).
    _os.environ.setdefault(
        "LLMOS_OMNIPARSER_MODEL_DIR",
        _os.path.expanduser(settings.vision.model_dir),
    )
    if settings.vision.device != "auto":
        _os.environ.setdefault("LLMOS_OMNIPARSER_DEVICE", settings.vision.device)
    _os.environ.setdefault("LLMOS_OMNIPARSER_BOX_THRESH", str(settings.vision.box_threshold))
    _os.environ.setdefault("LLMOS_OMNIPARSER_IOU_THRESH", str(settings.vision.iou_threshold))
    _os.environ.setdefault("LLMOS_OMNIPARSER_CAPTION_MODEL", settings.vision.caption_model_name)
    _os.environ.setdefault("LLMOS_OMNIPARSER_USE_PADDLEOCR", str(settings.vision.use_paddleocr).lower())
    _os.environ.setdefault("LLMOS_OMNIPARSER_AUTO_DOWNLOAD", str(settings.vision.auto_download_weights).lower())


def _apply_ultra_vision_config(settings: Settings) -> None:
    """Set environment variables from VisionConfig for UltraVision."""
    import os as _os

    _os.environ.setdefault(
        "LLMOS_ULTRA_VISION_MODEL_DIR",
        _os.path.expanduser(settings.vision.ultra_model_dir),
    )
    if settings.vision.ultra_device != "auto":
        _os.environ.setdefault("LLMOS_ULTRA_VISION_DEVICE", settings.vision.ultra_device)
    _os.environ.setdefault("LLMOS_ULTRA_VISION_BOX_THRESH", str(settings.vision.ultra_box_threshold))
    _os.environ.setdefault("LLMOS_ULTRA_VISION_OCR_ENGINE", settings.vision.ultra_ocr_engine)
    _os.environ.setdefault(
        "LLMOS_ULTRA_VISION_ENABLE_GROUNDING",
        str(settings.vision.ultra_enable_grounding).lower(),
    )
    _os.environ.setdefault(
        "LLMOS_ULTRA_VISION_GROUNDING_IDLE_TIMEOUT",
        str(settings.vision.ultra_grounding_idle_timeout),
    )
    _os.environ.setdefault("LLMOS_ULTRA_VISION_MAX_VRAM_MB", str(settings.vision.ultra_max_vram_mb))
    _os.environ.setdefault(
        "LLMOS_ULTRA_VISION_AUTO_DOWNLOAD",
        str(settings.vision.ultra_auto_download).lower(),
    )


def _load_custom_vision_backend(backend_path: str) -> type | None:
    """Load a custom vision backend class from a fully-qualified path.

    Args:
        backend_path: e.g. 'mypackage.vision.MyVisionModule'

    Returns:
        The class if loaded successfully, or None on failure.
    """
    import importlib

    module_path, _, class_name = backend_path.rpartition(".")
    if not module_path or not class_name:
        log.error("custom_vision_backend_invalid_path", backend=backend_path)
        return None
    try:
        mod = importlib.import_module(module_path)
        custom_cls = getattr(mod, class_name)
        log.info("custom_vision_backend_loaded", backend=backend_path)
        return custom_cls
    except Exception as exc:
        log.error("custom_vision_backend_failed", backend=backend_path, error=str(exc))
        return None


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
