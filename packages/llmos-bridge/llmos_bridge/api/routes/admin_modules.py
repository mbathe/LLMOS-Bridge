"""API routes — Module administration for dashboard.

Provides REST endpoints for all module lifecycle operations,
delegating to the ModuleManagerModule's IML actions.

Install endpoints require hub.local_install_enabled=True (default) or
hub.enabled=True.  They are available even without a remote hub.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from llmos_bridge.api.dependencies import AuthDep, RegistryDep
from llmos_bridge.api.schemas import ActionToggleRequest, ConfigUpdateRequest

router = APIRouter(prefix="/admin/modules", tags=["admin-modules"])


# ---------------------------------------------------------------------------
# Request bodies for install/upgrade endpoints
# ---------------------------------------------------------------------------


class InstallFromPathRequest(BaseModel):
    """Install a module from a local directory path."""
    path: str = Field(..., description="Absolute path to the module directory (contains llmos-module.toml).")


class UpgradeFromPathRequest(BaseModel):
    """Upgrade an installed module from a local directory path."""
    path: str = Field(..., description="Absolute path to the new version directory.")


def _get_module_manager(registry):
    """Get the ModuleManagerModule instance from registry."""
    if not registry.is_available("module_manager"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Module Manager not available.",
        )
    return registry.get("module_manager")


def _get_installer(request: Request):
    """Return the ModuleInstaller from app.state, or raise 503."""
    installer = getattr(request.app.state, "module_installer", None)
    if installer is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Module installer not configured. "
                "Set hub.local_install_enabled=true (default) or hub.enabled=true."
            ),
        )
    return installer


def _get_module_index(request: Request):
    """Return the ModuleIndex from app.state, or raise 503."""
    index = getattr(request.app.state, "module_index", None)
    if index is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Module index not available. Enable hub.local_install_enabled.",
        )
    return index


@router.get("")
async def list_modules(
    registry: RegistryDep,
    _auth: AuthDep,
    module_type: str | None = None,
    state: str | None = None,
    include_health: bool = False,
) -> dict[str, Any]:
    """List all modules with state, type, and optional health."""
    mm = _get_module_manager(registry)
    return await mm._action_list_modules({
        "module_type": module_type,
        "state": state,
        "include_health": include_health,
    })


# ---------------------------------------------------------------------------
# Community module install / list installed / browse
# NOTE: Static routes MUST be declared before /{module_id} to avoid
# FastAPI matching "installed" or "install" as a module_id parameter.
# ---------------------------------------------------------------------------


@router.get("/browse")
async def browse_directory(
    _auth: AuthDep,
    path: str = "~",
) -> dict[str, Any]:
    """Browse local filesystem directories for module installation.

    Returns a list of subdirectories and whether the given path
    contains an ``llmos-module.toml`` (i.e. is a valid module directory).
    """
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_dir():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Not a directory: {path}",
        )
    entries: list[dict[str, Any]] = []
    try:
        for child in sorted(resolved.iterdir()):
            if child.name.startswith("."):
                continue
            if child.is_dir():
                has_toml = (child / "llmos-module.toml").exists()
                entries.append({
                    "name": child.name,
                    "path": str(child),
                    "is_module": has_toml,
                })
    except PermissionError:
        pass
    return {
        "current": str(resolved),
        "parent": str(resolved.parent) if resolved != resolved.parent else None,
        "is_module": (resolved / "llmos-module.toml").exists(),
        "entries": entries,
    }


@router.post("/install")
async def install_module(
    body: InstallFromPathRequest,
    request: Request,
    registry: RegistryDep,
    _auth: AuthDep,
) -> dict[str, Any]:
    """Install a module from a local directory.

    The directory must contain ``llmos-module.toml``.  The installer will:
    - Validate the module structure (blocking on errors)
    - Resolve module-to-module dependencies
    - Create an isolated Python venv and install declared requirements
    - Register the module in the runtime registry
    - Call the on_install() lifecycle hook

    Requires ``hub.local_install_enabled=true`` (default) or ``hub.enabled=true``.
    """
    installer = _get_installer(request)
    package_path = Path(body.path)
    if not package_path.is_dir():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Path is not a directory: {body.path}",
        )
    result = await installer.install_from_path(package_path)
    if not result.success:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=result.error,
        )
    return {
        "success": result.success,
        "module_id": result.module_id,
        "version": result.version,
        "installed_deps": result.installed_deps,
        "validation_warnings": result.validation_warnings,
        "scan_score": result.scan_score,
        "trust_tier": result.trust_tier,
        "scan_findings_count": result.scan_findings_count,
    }


@router.get("/installed")
async def list_installed_modules(
    request: Request,
    _auth: AuthDep,
    enabled_only: bool = False,
) -> dict[str, Any]:
    """List community modules registered in the module index (SQLite)."""
    index = _get_module_index(request)
    if enabled_only:
        modules = await index.list_enabled()
    else:
        modules = await index.list_all()
    return {
        "modules": [
            {
                "module_id": m.module_id,
                "version": m.version,
                "install_path": m.install_path,
                "enabled": m.enabled,
                "sandbox_level": m.sandbox_level,
                "installed_at": m.installed_at,
                "updated_at": m.updated_at,
                "trust_tier": m.trust_tier,
                "scan_score": m.scan_score,
                "signature_status": m.signature_status,
            }
            for m in modules
        ],
        "total": len(modules),
    }


# ---------------------------------------------------------------------------
# Parametric module routes — /{module_id}/*
# ---------------------------------------------------------------------------


@router.get("/{module_id}/security")
async def get_module_security(
    module_id: str,
    request: Request,
    _auth: AuthDep,
) -> dict[str, Any]:
    """Get security metadata for an installed module (trust tier, scan score, etc.)."""
    index = _get_module_index(request)
    data = await index.get_security_data(module_id)
    if data is None:
        raise HTTPException(404, f"Module '{module_id}' not found in index.")
    # Parse scan_result_json to get findings count.
    import json
    findings_count = 0
    if data.get("scan_result_json"):
        try:
            parsed = json.loads(data["scan_result_json"])
            findings_count = len(parsed.get("findings", []))
        except (json.JSONDecodeError, TypeError):
            pass
    return {
        "module_id": data["module_id"],
        "trust_tier": data["trust_tier"],
        "scan_score": data["scan_score"],
        "signature_status": data["signature_status"],
        "checksum": data["checksum"],
        "findings_count": findings_count,
    }


@router.post("/{module_id}/rescan")
async def rescan_module(
    module_id: str,
    request: Request,
    _auth: AuthDep,
) -> dict[str, Any]:
    """Re-run the source code security scanner on an installed module."""
    index = _get_module_index(request)
    module_data = await index.get(module_id)
    if module_data is None:
        raise HTTPException(404, f"Module '{module_id}' not found in index.")

    install_path = Path(module_data.install_path)
    if not install_path.is_dir():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Install path missing: {install_path}",
        )

    from llmos_bridge.hub.source_scanner import SourceCodeScanner
    import json

    scanner = SourceCodeScanner()
    scan_result = await scanner.scan_directory(install_path)

    from llmos_bridge.hub.trust import TrustPolicy
    trust_tier = TrustPolicy.compute_tier(
        scan_score=scan_result.score,
        signature_verified=(module_data.signature_status == "verified"),
        module_id=module_id,
    )

    await index.update_security_data(
        module_id,
        trust_tier=trust_tier.value,
        scan_score=scan_result.score,
        scan_result_json=json.dumps(scan_result.to_dict()),
    )

    return {
        "module_id": module_id,
        "scan_score": scan_result.score,
        "verdict": scan_result.verdict.value,
        "findings_count": len(scan_result.findings),
        "trust_tier": trust_tier.value,
    }


class SetTrustTierRequest(BaseModel):
    """Request body for setting a module's trust tier."""
    trust_tier: str = Field(..., description="Target tier: unverified, verified, trusted")
    reason: str = Field(default="", description="Reason for the trust tier change.")


@router.put("/{module_id}/trust")
async def set_module_trust(
    module_id: str,
    body: SetTrustTierRequest,
    request: Request,
    _auth: AuthDep,
) -> dict[str, Any]:
    """Manually set the trust tier for an installed module.

    The ``official`` tier cannot be set via API — it is reserved for system modules.
    """
    index = _get_module_index(request)
    module_data = await index.get(module_id)
    if module_data is None:
        raise HTTPException(404, f"Module '{module_id}' not found in index.")

    from llmos_bridge.hub.trust import TrustPolicy, TrustTier

    try:
        tier = TrustPolicy.validate_tier(body.trust_tier)
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    if not TrustPolicy.is_api_assignable(tier):
        raise HTTPException(
            403, f"Trust tier '{tier.value}' is reserved for system assignment only."
        )

    await index.update_trust_tier(module_id, tier.value)

    return {
        "module_id": module_id,
        "trust_tier": tier.value,
        "previous_tier": module_data.trust_tier,
    }


@router.get("/{module_id}/scan-report")
async def get_scan_report(
    module_id: str,
    request: Request,
    _auth: AuthDep,
) -> dict[str, Any]:
    """Get the detailed source code scan report for an installed module."""
    index = _get_module_index(request)
    data = await index.get_security_data(module_id)
    if data is None:
        raise HTTPException(404, f"Module '{module_id}' not found in index.")

    import json

    scan_json = data.get("scan_result_json", "")
    if not scan_json:
        return {
            "module_id": module_id,
            "scan_score": data["scan_score"],
            "verdict": "unknown",
            "findings": [],
            "files_scanned": 0,
            "scan_duration_ms": 0.0,
        }

    try:
        parsed = json.loads(scan_json)
    except (json.JSONDecodeError, TypeError):
        return {
            "module_id": module_id,
            "scan_score": data["scan_score"],
            "verdict": "unknown",
            "findings": [],
            "files_scanned": 0,
            "scan_duration_ms": 0.0,
        }

    return {
        "module_id": module_id,
        "scan_score": parsed.get("score", data["scan_score"]),
        "verdict": parsed.get("verdict", "unknown"),
        "findings": parsed.get("findings", []),
        "files_scanned": parsed.get("files_scanned", 0),
        "scan_duration_ms": parsed.get("scan_duration_ms", 0.0),
    }


@router.get("/{module_id}")
async def get_module_info(
    module_id: str,
    registry: RegistryDep,
    _auth: AuthDep,
    include_health: bool = False,
    include_metrics: bool = False,
) -> dict[str, Any]:
    """Get detailed info about a module."""
    mm = _get_module_manager(registry)
    return await mm._action_get_module_info({
        "module_id": module_id,
        "include_health": include_health,
        "include_metrics": include_metrics,
    })


@router.get("/{module_id}/health")
async def get_module_health(
    module_id: str,
    registry: RegistryDep,
    _auth: AuthDep,
) -> dict[str, Any]:
    """Run health_check() on a module."""
    mm = _get_module_manager(registry)
    return await mm._action_get_module_health({"module_id": module_id})


@router.get("/{module_id}/metrics")
async def get_module_metrics(
    module_id: str,
    registry: RegistryDep,
    _auth: AuthDep,
) -> dict[str, Any]:
    """Get operational metrics."""
    mm = _get_module_manager(registry)
    return await mm._action_get_module_metrics({"module_id": module_id})


@router.get("/{module_id}/state")
async def get_module_state(
    module_id: str,
    registry: RegistryDep,
    _auth: AuthDep,
) -> dict[str, Any]:
    """Get module state snapshot."""
    mm = _get_module_manager(registry)
    return await mm._action_get_module_state({"module_id": module_id})


@router.get("/{module_id}/describe")
async def describe_module(
    module_id: str,
    registry: RegistryDep,
    _auth: AuthDep,
) -> dict[str, Any]:
    """Get dynamic self-description (v3 describe())."""
    mm = _get_module_manager(registry)
    return await mm._action_describe_module({"module_id": module_id})


@router.get("/{module_id}/manifest")
async def get_module_manifest(
    module_id: str,
    registry: RegistryDep,
    _auth: AuthDep,
) -> dict[str, Any]:
    """Get full module manifest as JSON."""
    if not registry.is_available(module_id):
        raise HTTPException(404, f"Module '{module_id}' not found.")
    module = registry.get(module_id)
    return module.get_manifest().to_dict()


@router.get("/{module_id}/docs")
async def get_module_docs(
    module_id: str,
    registry: RegistryDep,
    _auth: AuthDep,
) -> dict[str, Any]:
    """Get module documentation (README + action docs)."""
    if not registry.is_available(module_id):
        raise HTTPException(404, f"Module '{module_id}' not found.")

    import inspect
    from pathlib import Path
    from llmos_bridge.isolation.proxy import IsolatedModuleProxy

    module = registry.get(module_id)

    # For community modules (IsolatedModuleProxy), use the source install
    # path instead of inspect.getfile() which would return proxy.py's dir.
    if isinstance(module, IsolatedModuleProxy) and module._source_path is not None:
        module_dir = module._source_path
    else:
        module_file = inspect.getfile(type(module))
        module_dir = Path(module_file).parent

    docs: dict[str, Any] = {"module_id": module_id}
    for name, filename in [
        ("readme", "README.md"),
        ("actions", "docs/actions.md"),
        ("integration", "docs/integration.md"),
        ("changelog", "CHANGELOG.md"),
    ]:
        path = module_dir / filename
        if path.exists():
            docs[name] = path.read_text(encoding="utf-8")
        else:
            docs[name] = None
    return docs


@router.post("/{module_id}/enable")
async def enable_module(
    module_id: str,
    registry: RegistryDep,
    _auth: AuthDep,
) -> dict[str, Any]:
    """Enable (start) a module."""
    mm = _get_module_manager(registry)
    return await mm._action_enable_module({"module_id": module_id})


@router.post("/{module_id}/disable")
async def disable_module(
    module_id: str,
    registry: RegistryDep,
    _auth: AuthDep,
) -> dict[str, Any]:
    """Disable (stop) a module."""
    mm = _get_module_manager(registry)
    return await mm._action_disable_module({"module_id": module_id})


@router.post("/{module_id}/pause")
async def pause_module(
    module_id: str,
    registry: RegistryDep,
    _auth: AuthDep,
) -> dict[str, Any]:
    """Pause a module."""
    mm = _get_module_manager(registry)
    return await mm._action_pause_module({"module_id": module_id})


@router.post("/{module_id}/resume")
async def resume_module(
    module_id: str,
    registry: RegistryDep,
    _auth: AuthDep,
) -> dict[str, Any]:
    """Resume a paused module."""
    mm = _get_module_manager(registry)
    return await mm._action_resume_module({"module_id": module_id})


@router.post("/{module_id}/restart")
async def restart_module(
    module_id: str,
    registry: RegistryDep,
    _auth: AuthDep,
) -> dict[str, Any]:
    """Restart a module."""
    mm = _get_module_manager(registry)
    return await mm._action_restart_module({"module_id": module_id})


@router.get("/{module_id}/config/schema")
async def get_module_config_schema(
    module_id: str,
    registry: RegistryDep,
    _auth: AuthDep,
) -> dict[str, Any]:
    """Get the config JSON Schema with UI metadata for dashboard form generation."""
    if not registry.is_available(module_id):
        raise HTTPException(404, f"Module '{module_id}' not found.")
    module = registry.get(module_id)
    if module.CONFIG_MODEL is None:
        return {"configurable": False, "schema": None}
    return {
        "configurable": True,
        "schema": module.CONFIG_MODEL.to_config_schema(),
    }


@router.put("/{module_id}/config")
async def update_module_config(
    module_id: str,
    body: ConfigUpdateRequest,
    registry: RegistryDep,
    _auth: AuthDep,
) -> dict[str, Any]:
    """Update module runtime config."""
    mm = _get_module_manager(registry)
    return await mm._action_update_module_config({
        "module_id": module_id,
        "config": body.config,
    })


@router.post("/{module_id}/actions/{action_name}/enable")
async def enable_action(
    module_id: str,
    action_name: str,
    registry: RegistryDep,
    _auth: AuthDep,
) -> dict[str, Any]:
    """Re-enable a disabled action."""
    mm = _get_module_manager(registry)
    return await mm._action_enable_action({
        "module_id": module_id,
        "action": action_name,
    })


@router.post("/{module_id}/actions/{action_name}/disable")
async def disable_action(
    module_id: str,
    action_name: str,
    body: ActionToggleRequest,
    registry: RegistryDep,
    _auth: AuthDep,
) -> dict[str, Any]:
    """Disable a specific action."""
    mm = _get_module_manager(registry)
    return await mm._action_disable_action({
        "module_id": module_id,
        "action": action_name,
        "reason": body.reason,
    })


@router.delete("/{module_id}/uninstall")
async def uninstall_module(
    module_id: str,
    request: Request,
    registry: RegistryDep,
    _auth: AuthDep,
) -> dict[str, Any]:
    """Uninstall a community module.

    Removes the module from the runtime registry and the module index.
    The source directory is NOT deleted — only the registration is removed.
    """
    installer = _get_installer(request)
    result = await installer.uninstall(module_id)
    if not result.success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=result.error,
        )
    return {"success": True, "module_id": result.module_id, "version": result.version}


@router.post("/{module_id}/upgrade")
async def upgrade_module(
    module_id: str,
    body: UpgradeFromPathRequest,
    request: Request,
    registry: RegistryDep,
    _auth: AuthDep,
) -> dict[str, Any]:
    """Upgrade an installed module to a new version from a local directory."""
    installer = _get_installer(request)
    new_path = Path(body.path)
    if not new_path.is_dir():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Path is not a directory: {body.path}",
        )
    result = await installer.upgrade(module_id, new_path)
    if not result.success:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=result.error,
        )
    return {
        "success": True,
        "module_id": result.module_id,
        "version": result.version,
        "validation_warnings": result.validation_warnings,
        "scan_score": result.scan_score,
        "trust_tier": result.trust_tier,
        "scan_findings_count": result.scan_findings_count,
    }
