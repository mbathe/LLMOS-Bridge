"""API routes — System administration for dashboard."""

from __future__ import annotations

import os
import signal
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from llmos_bridge.api.dependencies import AuthDep, ConfigDep, RegistryDep

router = APIRouter(prefix="/admin", tags=["admin-system"])


@router.get("/system/status")
async def get_system_status(
    registry: RegistryDep,
    _auth: AuthDep,
) -> dict[str, Any]:
    """Aggregated system health status."""
    if not registry.is_available("module_manager"):
        return {"error": "Module Manager not available"}
    mm = registry.get("module_manager")
    return await mm._action_get_system_status({"include_health": True})


@router.get("/system/services")
async def list_services(
    registry: RegistryDep,
    _auth: AuthDep,
) -> dict[str, Any]:
    """List all ServiceBus registrations."""
    if not registry.is_available("module_manager"):
        return {"services": [], "error": "Module Manager not available"}
    mm = registry.get("module_manager")
    return await mm._action_list_services({})


@router.get("/system/config")
async def get_config(
    config: ConfigDep,
    _auth: AuthDep,
) -> dict[str, Any]:
    """Get current configuration (secrets redacted)."""
    raw = config.model_dump()
    # Redact sensitive values.
    if "security" in raw and "api_token" in raw["security"]:
        token = raw["security"]["api_token"]
        if token:
            raw["security"]["api_token"] = f"{token[:4]}***" if len(token) > 4 else "***"
    return raw


@router.get("/system/events")
async def get_events(
    _auth: AuthDep,
    request: Request,
    limit: int = 50,
    topic: str | None = None,
) -> dict[str, Any]:
    """Query recent events from the event bus ring buffer."""
    # Try to get the event bus from state.
    lifecycle = getattr(request.app.state, "lifecycle_manager", None)
    if lifecycle is None:
        return {"events": [], "count": 0}

    bus = getattr(lifecycle, "_event_bus", None)
    if bus is None:
        return {"events": [], "count": 0}

    recent = list(getattr(bus, "_recent_events", []))
    if topic:
        recent = [e for e in recent if e.get("_topic") == topic]
    recent = recent[-limit:]
    return {"events": recent, "count": len(recent)}


@router.get("/system/policies")
async def get_policies(
    _auth: AuthDep,
    request: Request,
) -> dict[str, Any]:
    """List all module policies and enforcement status."""
    # Get policy enforcer from executor.
    executor = getattr(request.app.state, "plan_executor", None)
    if executor is None:
        return {"policies": {}, "error": "Executor not available"}

    policy_enforcer = getattr(executor, "_policy_enforcer", None)
    if policy_enforcer is None:
        return {"policies": {}, "note": "Policy enforcement not configured"}

    return {"policies": policy_enforcer.status()}


class ConfigUpdateRequest(BaseModel):
    """Partial config update — keys are top-level section names."""
    config: dict[str, Any]


@router.put("/system/config")
async def update_config(
    body: ConfigUpdateRequest,
    _auth: AuthDep,
) -> dict[str, Any]:
    """Update daemon configuration and save to ~/.llmos/config.yaml.

    Accepts a partial config dict. Merges with existing config file,
    writes to disk.  A daemon restart is required for changes to take effect.
    """
    import yaml

    config_path = Path.home() / ".llmos" / "config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)

    # Load existing config from disk (if any).
    existing: dict[str, Any] = {}
    if config_path.exists():
        with config_path.open() as f:
            existing = yaml.safe_load(f) or {}

    # Deep merge: update section by section, stripping None values
    # (None/null breaks Pydantic validation on reload).
    def _strip_none(d: dict) -> dict:
        return {k: _strip_none(v) if isinstance(v, dict) else v
                for k, v in d.items() if v is not None}

    for section, values in body.config.items():
        cleaned = _strip_none(values) if isinstance(values, dict) else values
        if isinstance(cleaned, dict) and isinstance(existing.get(section), dict):
            existing[section].update(cleaned)
        else:
            existing[section] = cleaned

    with config_path.open("w") as f:
        yaml.dump(existing, f, default_flow_style=False, sort_keys=False)

    return {
        "saved": True,
        "path": str(config_path),
        "sections_updated": list(body.config.keys()),
        "restart_required": True,
    }


@router.post("/system/restart")
async def restart_daemon(
    config: ConfigDep,
    _auth: AuthDep,
) -> dict[str, Any]:
    """Restart the daemon process.

    Spawns a new daemon process with the same host/port, then exits the
    current process after a short delay so the HTTP response can flush.
    """
    import asyncio
    import subprocess

    host = config.server.host
    port = config.server.port

    async def _do_restart() -> None:
        await asyncio.sleep(0.5)  # let the response flush

        # Spawn a new daemon process that will take over once we exit.
        spawn_code = (
            "import time; time.sleep(1.5); "  # wait for old process to exit
            "from llmos_bridge.cli.commands.daemon import start; "
            f"start(host='{host}', port={port}, config=None, reload=False, log_level='info')"
        )
        subprocess.Popen(
            [sys.executable, "-c", spawn_code],
            start_new_session=True,  # detach from parent
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # Exit current process.
        os._exit(0)

    asyncio.create_task(_do_restart())
    return {"restarting": True, "pid": os.getpid()}
