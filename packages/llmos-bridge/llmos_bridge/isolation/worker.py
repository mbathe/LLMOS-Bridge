"""Generic worker process for isolated LLMOS Bridge modules.

Loads a BaseModule subclass from a given import path and serves JSON-RPC
2.0 requests over stdin/stdout.  stderr is used for logging.

Usage::

    python -m llmos_bridge.isolation.worker \\
        --module-class "llmos_bridge.modules.perception_vision.omniparser.module:OmniParserModule" \\
        --module-id "vision"

The worker is fully generic: it does not know *which* module it will load
until the command-line arguments are parsed.  Any ``BaseModule`` subclass
can be run as an isolated worker.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import importlib
import json
import os
import sys
import time
from typing import Any

from llmos_bridge.isolation.protocol import (
    ACTION_EXECUTION_ERROR,
    ACTION_NOT_FOUND_ERROR,
    BINARY_SCREENSHOT_KEY,
    INTERNAL_ERROR,
    METHOD_EXECUTE,
    METHOD_GET_MANIFEST,
    METHOD_HEALTH_CHECK,
    METHOD_INITIALIZE,
    METHOD_NOT_FOUND,
    METHOD_SHUTDOWN,
    MODULE_LOAD_ERROR,
    NOTIFICATION_WORKER_LOG,
    NOTIFICATION_WORKER_READY,
)


def _emit(data: dict[str, Any]) -> None:
    """Write a JSON line to stdout (host reads this)."""
    sys.stdout.write(json.dumps(data, default=str) + "\n")
    sys.stdout.flush()


def _emit_response(request_id: str, result: Any) -> None:
    _emit({"jsonrpc": "2.0", "id": request_id, "result": result, "error": None})


def _emit_error(request_id: str | None, code: int, message: str, data: dict[str, Any] | None = None) -> None:
    _emit({
        "jsonrpc": "2.0",
        "id": request_id,
        "result": None,
        "error": {"code": code, "message": message, "data": data},
    })


def _emit_notification(method: str, params: dict[str, Any]) -> None:
    _emit({"jsonrpc": "2.0", "method": method, "params": params})


def _log(level: str, message: str, **extra: Any) -> None:
    """Send a structured log entry to the host via notification."""
    _emit_notification(NOTIFICATION_WORKER_LOG, {"level": level, "message": message, **extra})


def _import_module_class(class_path: str) -> type:
    """Import a module class from ``'pkg.module:ClassName'`` notation."""
    if ":" not in class_path:
        raise ValueError(
            f"module_class_path must use 'module.path:ClassName' notation, got: {class_path!r}"
        )
    module_path, class_name = class_path.rsplit(":", 1)
    mod = importlib.import_module(module_path)
    cls = getattr(mod, class_name)
    return cls


# ---------------------------------------------------------------------------
# Request dispatch
# ---------------------------------------------------------------------------

_module_instance: Any = None
_module_id: str = ""
_module_class_path: str = ""
_start_time: float = 0.0


async def _handle_initialize(params: dict[str, Any]) -> dict[str, Any]:
    global _module_instance

    # Apply environment variables from host.
    env_vars = params.get("env_vars", {})
    for k, v in env_vars.items():
        os.environ[k] = str(v)
    # PYTHONPATH from env_vars must also be injected into sys.path so that
    # importlib.import_module() can find the module source directory.
    # (os.environ assignment alone does not update sys.path in a running process.)
    pythonpath = env_vars.get("PYTHONPATH", "")
    if pythonpath:
        for p in pythonpath.split(os.pathsep):
            if p and p not in sys.path:
                sys.path.insert(0, p)

    # Import and instantiate.
    try:
        cls = _import_module_class(_module_class_path)
        _module_instance = cls()
    except Exception as exc:
        raise _WorkerMethodError(MODULE_LOAD_ERROR, f"Failed to load module: {exc}") from exc

    manifest = _module_instance.get_manifest()
    return {
        "manifest": manifest.to_dict(),
        "module_id": _module_instance.MODULE_ID,
        "version": _module_instance.VERSION,
    }


async def _handle_execute(params: dict[str, Any]) -> Any:
    if _module_instance is None:
        raise _WorkerMethodError(MODULE_LOAD_ERROR, "Module not initialized. Send 'initialize' first.")

    action = params.get("action")
    if not action:
        raise _WorkerMethodError(ACTION_NOT_FOUND_ERROR, "Missing 'action' in params")

    action_params = dict(params.get("params", {}))
    context_data = params.get("context")

    # Decode base64 binary data.
    if BINARY_SCREENSHOT_KEY in action_params:
        action_params["screenshot_bytes"] = base64.b64decode(
            action_params.pop(BINARY_SCREENSHOT_KEY)
        )

    # Build ExecutionContext if provided.
    context = None
    if context_data:
        from llmos_bridge.modules.base import ExecutionContext
        context = ExecutionContext(**context_data)

    try:
        result = await _module_instance.execute(action, action_params, context)
    except Exception as exc:
        raise _WorkerMethodError(
            ACTION_EXECUTION_ERROR,
            f"Action '{action}' failed: {exc}",
        ) from exc

    # Ensure result is JSON-serializable.
    if hasattr(result, "model_dump"):
        return result.model_dump()
    if hasattr(result, "to_dict"):
        return result.to_dict()
    return result


def _handle_get_manifest() -> dict[str, Any]:
    if _module_instance is None:
        raise _WorkerMethodError(MODULE_LOAD_ERROR, "Module not initialized.")
    return _module_instance.get_manifest().to_dict()


async def _handle_health_check() -> dict[str, Any]:
    result: dict[str, Any] = {
        "healthy": True,
        "status": "ok",
        "module_id": _module_id,
        "uptime_seconds": round(time.monotonic() - _start_time, 1),
        "pid": os.getpid(),
    }
    # Delegate to the module's own health_check() if available.
    if _module_instance is not None:
        try:
            module_health = await _module_instance.health_check()
            if isinstance(module_health, dict):
                result.update(module_health)
        except Exception as exc:
            result["healthy"] = False
            result["status"] = "error"
            result["error"] = str(exc)
    return result


async def _dispatch(method: str, params: dict[str, Any]) -> Any:
    """Route an RPC method to the appropriate handler."""
    if method == METHOD_INITIALIZE:
        return await _handle_initialize(params)
    elif method == METHOD_EXECUTE:
        return await _handle_execute(params)
    elif method == METHOD_GET_MANIFEST:
        return _handle_get_manifest()
    elif method == METHOD_HEALTH_CHECK:
        return await _handle_health_check()
    elif method == METHOD_SHUTDOWN:
        _log("info", "Shutting down gracefully")
        # Schedule exit after response is sent.
        asyncio.get_event_loop().call_later(0.1, sys.exit, 0)
        return {"status": "shutting_down"}
    else:
        raise _WorkerMethodError(METHOD_NOT_FOUND, f"Unknown method: {method}")


class _WorkerMethodError(Exception):
    """Internal error with a JSON-RPC error code."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


async def run_worker(module_class_path: str, module_id: str) -> None:
    """Main event loop: read JSON-RPC from stdin, write responses to stdout."""
    global _module_id, _module_class_path, _start_time
    _module_id = module_id
    _module_class_path = module_class_path
    _start_time = time.monotonic()

    # Emit ready notification.
    _emit_notification(NOTIFICATION_WORKER_READY, {
        "module_id": module_id,
        "pid": os.getpid(),
    })

    # Read lines from stdin.
    loop = asyncio.get_event_loop()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: protocol, sys.stdin.buffer)

    while True:
        line = await reader.readline()
        if not line:
            break  # EOF — host closed stdin

        line_str = line.decode(errors="replace").strip()
        if not line_str:
            continue

        try:
            request = json.loads(line_str)
        except json.JSONDecodeError:
            _emit_error(None, INTERNAL_ERROR, "Invalid JSON")
            continue

        # Notifications from host (no id) — ignore for now.
        request_id = request.get("id")
        if request_id is None:
            continue

        method = request.get("method", "")
        params = request.get("params", {})

        try:
            result = await _dispatch(method, params)
            _emit_response(request_id, result)
        except _WorkerMethodError as exc:
            _emit_error(request_id, exc.code, str(exc))
        except Exception as exc:
            _emit_error(request_id, INTERNAL_ERROR, str(exc))


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="LLMOS Bridge isolated module worker")
    parser.add_argument(
        "--module-class",
        required=True,
        help="Fully-qualified class path: 'package.module:ClassName'",
    )
    parser.add_argument(
        "--module-id",
        required=True,
        help="Module ID for this worker (e.g. 'vision')",
    )
    args = parser.parse_args()

    asyncio.run(run_worker(args.module_class, args.module_id))


if __name__ == "__main__":
    main()
