"""Host-side proxy for isolated module workers.

``IsolatedModuleProxy`` subclasses ``BaseModule`` so that callers
(executor, perception pipeline, computer_control) cannot tell whether
the module runs in-process or in a subprocess.  All calls are forwarded
via JSON-RPC 2.0 over the worker's stdin/stdout.

Lifecycle:
    1. Proxy is created and registered in the registry at startup.
    2. On first ``execute()`` call, the proxy:
       a. Creates a venv via VenvManager (if not cached).
       b. Spawns the worker subprocess.
       c. Completes the initialize handshake.
    3. Subsequent calls are forwarded via JSON-RPC.
    4. On shutdown, the proxy sends ``shutdown`` and terminates the worker.
"""

from __future__ import annotations

import asyncio
import base64
import json
import uuid
from pathlib import Path
from typing import Any

from llmos_bridge.exceptions import (
    ActionExecutionError,
    WorkerCommunicationError,
    WorkerCrashedError,
    WorkerStartError,
)
from llmos_bridge.isolation.protocol import (
    ACTION_EXECUTION_ERROR,
    ACTION_NOT_FOUND_ERROR,
    BINARY_SCREENSHOT_KEY,
    INTERNAL_ERROR,
    METHOD_EXECUTE,
    METHOD_GET_MANIFEST,
    METHOD_HEALTH_CHECK,
    METHOD_INITIALIZE,
    METHOD_SHUTDOWN,
    MODULE_LOAD_ERROR,
    NOTIFICATION_WORKER_LOG,
    NOTIFICATION_WORKER_READY,
    JsonRpcRequest,
)
from llmos_bridge.logging import get_logger
from llmos_bridge.modules.base import BaseModule, ExecutionContext, Platform
from llmos_bridge.modules.manifest import ModuleManifest

log = get_logger(__name__)


class IsolatedModuleProxy(BaseModule):
    """Host-side proxy that forwards calls to a subprocess worker.

    Implements the full ``BaseModule`` interface.  Security decorators
    run host-side on the proxy (via ``set_security``).
    """

    # Set dynamically from the isolation spec.
    MODULE_ID: str = ""
    VERSION: str = "0.0.0"
    SUPPORTED_PLATFORMS: list[Platform] = [Platform.ALL]

    def __init__(
        self,
        module_id: str,
        module_class_path: str,
        venv_manager: Any,  # VenvManager
        requirements: list[str] | None = None,
        env_vars: dict[str, str] | None = None,
        timeout: float = 30.0,
        max_restarts: int = 3,
        restart_delay: float = 1.0,
        source_path: Path | str | None = None,
    ) -> None:
        # Do NOT call super().__init__() — avoids _check_dependencies()
        # because deps live in the worker's venv, not the host's.
        self._security: Any | None = None
        self._ctx: Any | None = None
        self._dynamic_actions: dict[str, Any] = {}
        self._dynamic_specs: dict[str, Any] = {}

        self.MODULE_ID = module_id
        self._module_class_path = module_class_path
        self._source_path: Path | None = Path(source_path) if source_path else None
        self._venv_manager = venv_manager
        self._requirements = requirements or []
        self._env_vars = env_vars or {}
        self._timeout = timeout
        self._max_restarts = max_restarts
        self._restart_delay = restart_delay

        # Runtime state.
        self._process: asyncio.subprocess.Process | None = None
        self._manifest: ModuleManifest | None = None
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._reader_task: asyncio.Task[None] | None = None
        self._started = False
        self._restart_count = 0
        # Lock to prevent concurrent start() calls (e.g. two execute() calls racing
        # before the worker is ready — both see _started=False and both call start()).
        self._start_lock: asyncio.Lock | None = None

    # ------------------------------------------------------------------
    # BaseModule interface
    # ------------------------------------------------------------------

    async def execute(
        self, action: str, params: dict[str, Any], context: ExecutionContext | None = None
    ) -> Any:
        await self._ensure_started()

        rpc_params: dict[str, Any] = {"action": action, "params": dict(params)}
        if context is not None:
            rpc_params["context"] = {
                "plan_id": context.plan_id,
                "action_id": context.action_id,
                "session_id": context.session_id,
                "extra": context.extra,
            }

        return await self._rpc(METHOD_EXECUTE, rpc_params, timeout=self._timeout)

    def get_manifest(self) -> ModuleManifest:
        if self._manifest is not None:
            return self._manifest
        # Fallback: return a minimal manifest before handshake.
        return ModuleManifest(
            module_id=self.MODULE_ID,
            version=self.VERSION,
            description=f"Isolated module '{self.MODULE_ID}' (not yet started)",
        )

    def _check_dependencies(self) -> None:
        pass  # Deps are in the worker's venv.

    # ------------------------------------------------------------------
    # BaseVisionModule compatibility (PerceptionPipeline calls this)
    # ------------------------------------------------------------------

    async def parse_screen(
        self,
        screenshot_path: str | None = None,
        screenshot_bytes: bytes | None = None,
        *,
        width: int | None = None,
        height: int | None = None,
        **kwargs: Any,
    ) -> Any:
        """Forward parse_screen to the worker, encoding binary data."""
        params: dict[str, Any] = {}
        if screenshot_path is not None:
            params["screenshot_path"] = screenshot_path
        if screenshot_bytes is not None:
            params[BINARY_SCREENSHOT_KEY] = base64.b64encode(screenshot_bytes).decode()
        if width is not None:
            params["width"] = width
        if height is not None:
            params["height"] = height

        result = await self.execute("parse_screen", params)

        # Return a VisionParseResult if the base module is available.
        try:
            from llmos_bridge.modules.perception_vision.base import VisionParseResult
            if isinstance(result, dict):
                return VisionParseResult.model_validate(result)
        except ImportError:
            pass
        return result

    async def on_start(self) -> None:
        """Called by LifecycleManager — starts the worker subprocess."""
        await self.start()

    async def on_stop(self) -> None:
        """Called by LifecycleManager — stops the worker subprocess."""
        await self.stop()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Create venv (if needed), spawn worker, complete handshake."""
        if self._started and self._process and self._process.returncode is None:
            return  # Already running.

        # Step 1: Ensure venv exists.
        python_path: str | Path
        if self._requirements:
            python_path = await self._venv_manager.ensure_venv(
                self.MODULE_ID, self._requirements
            )
        else:
            import sys
            python_path = sys.executable

        # Step 2: Spawn worker subprocess.
        # Merge env_vars into the subprocess environment so that PYTHONPATH
        # (and other variables) are active from Python startup, before sys.path
        # is initialised by the interpreter.
        import os as _os
        import sys as _sys
        proc_env = dict(_os.environ)
        if self._env_vars:
            proc_env.update(self._env_vars)

        # When spawning a venv Python (different from the host interpreter),
        # the venv's sys.path does not include the host's site-packages, so
        # `llmos_bridge` and its dependencies (pydantic, etc.) would be missing.
        # We inject two sets of paths so the worker can import everything:
        #   1. llmos_bridge's source directory (handles editable pip installs
        #      where a .pth file points to the source, not a real package).
        #   2. The host interpreter's site-packages (pydantic, fastapi, etc.).
        # The venv's own site-packages (with the module's specific deps) remain
        # active because the venv Python adds them to sys.path at startup.
        if str(python_path) != _sys.executable:
            import site as _site
            import llmos_bridge as _lb
            _current_pp = proc_env.get("PYTHONPATH", "")
            _extra_paths: list[str] = []
            # llmos_bridge source dir (editable install resolves via .pth)
            _llmos_src = _os.path.dirname(_os.path.dirname(_lb.__file__))
            if _llmos_src not in _current_pp:
                _extra_paths.append(_llmos_src)
            # host site-packages (pydantic, fastapi, structlog, etc.)
            for _sp in _site.getsitepackages():
                if _sp not in _current_pp and _sp not in _extra_paths:
                    _extra_paths.append(_sp)
            if _extra_paths:
                _extra = _os.pathsep.join(_extra_paths)
                proc_env["PYTHONPATH"] = (
                    f"{_extra}{_os.pathsep}{_current_pp}"
                    if _current_pp
                    else _extra
                )

        try:
            self._process = await asyncio.create_subprocess_exec(
                str(python_path),
                "-m", "llmos_bridge.isolation.worker",
                "--module-class", self._module_class_path,
                "--module-id", self.MODULE_ID,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=proc_env,
            )
        except Exception as exc:
            raise WorkerStartError(self.MODULE_ID, f"Failed to spawn: {exc}") from exc

        # Step 3: Wait for worker.ready notification.
        # IMPORTANT: read this line BEFORE starting the reader loop to avoid
        # two coroutines calling readline() on the same StreamReader concurrently,
        # which asyncio forbids and raises "readuntil() called while another
        # coroutine is already waiting for incoming data".
        try:
            ready_line = await asyncio.wait_for(
                self._process.stdout.readline(), timeout=10.0  # type: ignore[union-attr]
            )
            ready_data = json.loads(ready_line.decode())
            if ready_data.get("method") != NOTIFICATION_WORKER_READY:
                raise WorkerStartError(self.MODULE_ID, f"Expected worker.ready, got: {ready_data}")
        except asyncio.TimeoutError:
            await self._kill()
            raise WorkerStartError(self.MODULE_ID, "Worker did not send ready notification in 10s")
        except Exception as exc:
            if not isinstance(exc, WorkerStartError):
                await self._kill()
                raise WorkerStartError(self.MODULE_ID, str(exc)) from exc
            raise

        # Step 4: Start reader loop (after worker.ready is consumed — safe now).
        self._reader_task = asyncio.create_task(self._reader_loop())

        # Step 5: Send initialize.
        init_result = await self._rpc(METHOD_INITIALIZE, {
            "env_vars": self._env_vars,
        }, timeout=60.0)

        # Step 6: Cache manifest.
        manifest_data = init_result.get("manifest", {})
        self._manifest = self._parse_manifest(manifest_data)
        self.VERSION = init_result.get("version", self.VERSION)

        self._started = True
        self._restart_count = 0
        log.info(
            "worker_started",
            module_id=self.MODULE_ID,
            pid=self._process.pid,
            version=self.VERSION,
        )

    async def stop(self) -> None:
        """Gracefully shut down the worker."""
        if self._process and self._process.returncode is None:
            try:
                await self._rpc(METHOD_SHUTDOWN, {}, timeout=5.0)
            except Exception:
                pass
        await self._kill()
        self._started = False

    async def restart(self) -> None:
        """Stop and restart the worker (crash recovery)."""
        await self._kill()
        self._started = False
        await asyncio.sleep(self._restart_delay)
        await self.start()
        self._restart_count += 1

    @property
    def is_alive(self) -> bool:
        return (
            self._process is not None
            and self._process.returncode is None
            and self._started
        )

    async def health_check(self) -> dict[str, Any]:
        """Send health_check RPC to the worker."""
        if not self.is_alive:
            return {"status": "dead", "module_id": self.MODULE_ID}
        try:
            return await self._rpc(METHOD_HEALTH_CHECK, {}, timeout=5.0)
        except Exception as exc:
            return {"status": "error", "module_id": self.MODULE_ID, "error": str(exc)}

    # ------------------------------------------------------------------
    # JSON-RPC IPC
    # ------------------------------------------------------------------

    async def _rpc(
        self, method: str, params: dict[str, Any], timeout: float | None = None
    ) -> Any:
        """Send a JSON-RPC request and await the response."""
        if self._process is None or self._process.stdin is None:
            raise WorkerCommunicationError(self.MODULE_ID, "Worker not running")

        request_id = uuid.uuid4().hex[:12]
        request = JsonRpcRequest(method=method, params=params, id=request_id)

        loop = asyncio.get_event_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[request_id] = future

        try:
            line = request.model_dump_json() + "\n"
            self._process.stdin.write(line.encode())
            await self._process.stdin.drain()
        except Exception as exc:
            self._pending.pop(request_id, None)
            raise WorkerCommunicationError(self.MODULE_ID, f"Write failed: {exc}") from exc

        try:
            response = await asyncio.wait_for(future, timeout=timeout or self._timeout)
        except asyncio.TimeoutError:
            self._pending.pop(request_id, None)
            raise WorkerCommunicationError(
                self.MODULE_ID, f"RPC '{method}' timed out after {timeout or self._timeout}s"
            )

        if response.get("error"):
            err = response["error"]
            code = err.get("code", INTERNAL_ERROR)
            message = err.get("message", "Unknown error")
            raise ActionExecutionError(
                module_id=self.MODULE_ID,
                action=method,
                cause=RuntimeError(f"[{code}] {message}"),
            )

        return response.get("result")

    async def _reader_loop(self) -> None:
        """Background task: read JSON-RPC responses from worker stdout."""
        if self._process is None or self._process.stdout is None:
            return

        try:
            while True:
                line = await self._process.stdout.readline()
                if not line:
                    break  # EOF — worker exited.

                try:
                    data = json.loads(line.decode())
                except json.JSONDecodeError:
                    continue

                # Response (has id) — resolve pending future.
                msg_id = data.get("id")
                if msg_id and msg_id in self._pending:
                    future = self._pending.pop(msg_id)
                    if not future.done():
                        future.set_result(data)
                    continue

                # Notification (no id) — handle.
                method = data.get("method", "")
                if method == NOTIFICATION_WORKER_LOG:
                    params = data.get("params", {})
                    log.info(
                        "worker_log",
                        module_id=self.MODULE_ID,
                        worker_level=params.get("level"),
                        worker_msg=params.get("message"),
                    )
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            log.error("reader_loop_error", module_id=self.MODULE_ID, error=str(exc))
        finally:
            # Worker died — fail all pending requests.
            for req_id, future in self._pending.items():
                if not future.done():
                    future.set_exception(
                        WorkerCrashedError(self.MODULE_ID, self._process.returncode or -1)
                    )
            self._pending.clear()

    async def _ensure_started(self) -> None:
        """Lazy start: create venv + spawn on first use.

        The asyncio.Lock ensures that if two coroutines race to start the
        worker (e.g. two concurrent execute() calls before the first start
        completes), only one start() runs.  The second waits for the lock
        and then sees _started=True and returns immediately.
        """
        # Fast path — no lock needed when already running.
        if self._started and self.is_alive:
            return

        # Lazy-init the lock inside the running event loop.
        if self._start_lock is None:
            self._start_lock = asyncio.Lock()

        async with self._start_lock:
            # Re-check inside the lock — a concurrent caller may have started.
            if self._started and self.is_alive:
                return

            if self._restart_count >= self._max_restarts and not self._started:
                raise WorkerStartError(
                    self.MODULE_ID,
                    f"Max restarts ({self._max_restarts}) exhausted",
                )

            await self.start()

    async def _kill(self) -> None:
        """Force-terminate the worker subprocess."""
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None

        if self._process and self._process.returncode is None:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self._process.kill()
                await self._process.wait()
            except ProcessLookupError:
                pass
        self._process = None

    def _parse_manifest(self, data: dict[str, Any]) -> ModuleManifest:
        """Parse a manifest dict from the worker into a ModuleManifest."""
        from llmos_bridge.modules.manifest import (
            ActionSpec,
            ModuleSignature,
            ParamSpec,
            ResourceLimits,
            ServiceDescriptor,
        )

        actions = []
        for a in data.get("actions", []):
            params = []
            schema = a.get("params_schema", {})
            properties = schema.get("properties", {})
            required = schema.get("required", [])
            for pname, pspec in properties.items():
                params.append(ParamSpec(
                    name=pname,
                    type=pspec.get("type", "string"),
                    description=pspec.get("description", ""),
                    required=pname in required,
                    default=pspec.get("default"),
                    enum=pspec.get("enum"),
                ))
            actions.append(ActionSpec(
                name=a.get("name", ""),
                description=a.get("description", ""),
                params=params,
                returns=a.get("returns", "object"),
                returns_description=a.get("returns_description", ""),
                permission_required=a.get("permission_required", "local_worker"),
                platforms=a.get("platforms", ["all"]),
                examples=a.get("examples", []),
                tags=a.get("tags", []),
                permissions=a.get("permissions", []),
                risk_level=a.get("risk_level", ""),
                irreversible=a.get("irreversible", False),
                data_classification=a.get("data_classification", ""),
                # v3 fields
                output_schema=a.get("output_schema"),
                side_effects=a.get("side_effects", []),
                execution_mode=a.get("execution_mode", "async"),
            ))

        # Parse v2 service descriptors.
        provides = []
        for s in data.get("provides_services", []):
            provides.append(ServiceDescriptor(
                name=s.get("name", ""),
                methods=s.get("methods", []),
                description=s.get("description", ""),
            ))

        # Parse v3 resource limits.
        resource_limits = None
        rl_data = data.get("resource_limits")
        if rl_data and isinstance(rl_data, dict):
            resource_limits = ResourceLimits(
                max_cpu_percent=rl_data.get("max_cpu_percent", 100.0),
                max_memory_mb=rl_data.get("max_memory_mb", 0),
                max_execution_seconds=rl_data.get("max_execution_seconds", 0.0),
                max_concurrent_actions=rl_data.get("max_concurrent_actions", 0),
            )

        # Parse v3 signing.
        signing = None
        sig_data = data.get("signing")
        if sig_data and isinstance(sig_data, dict):
            signing = ModuleSignature(
                public_key_fingerprint=sig_data.get("public_key_fingerprint", ""),
                signature_hex=sig_data.get("signature_hex", ""),
                signed_hash=sig_data.get("signed_hash", ""),
                signed_at=sig_data.get("signed_at", ""),
            )

        return ModuleManifest(
            module_id=data.get("module_id", self.MODULE_ID),
            version=data.get("version", self.VERSION),
            description=data.get("description", ""),
            author=data.get("author", ""),
            homepage=data.get("homepage", ""),
            platforms=data.get("platforms", ["all"]),
            actions=actions,
            dependencies=data.get("dependencies", []),
            tags=data.get("tags", []),
            declared_permissions=data.get("declared_permissions", []),
            # v2 fields
            module_type=data.get("module_type", "user"),
            provides_services=provides,
            consumes_services=data.get("consumes_services", []),
            emits_events=data.get("emits_events", []),
            subscribes_events=data.get("subscribes_events", []),
            config_schema=data.get("config_schema"),
            # v3 fields
            resource_limits=resource_limits,
            sandbox_level=data.get("sandbox_level", "none"),
            license=data.get("license", ""),
            optional_dependencies=data.get("optional_dependencies", []),
            module_dependencies=data.get("module_dependencies", {}),
            signing=signing,
        )
