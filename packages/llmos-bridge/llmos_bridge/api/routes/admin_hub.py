"""API routes — Hub / package manager administration for dashboard."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

from llmos_bridge.api.dependencies import AuthDep, RegistryDep
from llmos_bridge.api.schemas import InstallModuleRequest, UpgradeModuleRequest

router = APIRouter(prefix="/admin/hub", tags=["admin-hub"])


def _get_module_manager(registry):
    """Get the ModuleManagerModule instance from registry."""
    if not registry.is_available("module_manager"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Module Manager not available.",
        )
    return registry.get("module_manager")


@router.get("/search")
async def search_hub(
    registry: RegistryDep,
    _auth: AuthDep,
    q: str = "",
    limit: int = 20,
) -> dict[str, Any]:
    """Search the module hub."""
    mm = _get_module_manager(registry)
    return await mm._action_search_hub({"query": q, "limit": limit})


@router.get("/installed")
async def list_installed(
    registry: RegistryDep,
    _auth: AuthDep,
    enabled_only: bool = False,
) -> dict[str, Any]:
    """List installed community modules."""
    mm = _get_module_manager(registry)
    return await mm._action_list_installed({"enabled_only": enabled_only})


@router.post("/install")
async def install_module(
    body: InstallModuleRequest,
    registry: RegistryDep,
    _auth: AuthDep,
) -> dict[str, Any]:
    """Install a module from hub or local path."""
    mm = _get_module_manager(registry)
    return await mm._action_install_module({
        "source": body.source,
        "module_id": body.module_id,
        "path": body.path,
        "version": body.version,
    })


@router.delete("/modules/{module_id}")
async def uninstall_module(
    module_id: str,
    registry: RegistryDep,
    _auth: AuthDep,
) -> dict[str, Any]:
    """Uninstall a community module."""
    mm = _get_module_manager(registry)
    return await mm._action_uninstall_module({"module_id": module_id})


@router.post("/modules/{module_id}/upgrade")
async def upgrade_module(
    module_id: str,
    body: UpgradeModuleRequest,
    registry: RegistryDep,
    _auth: AuthDep,
) -> dict[str, Any]:
    """Upgrade an installed module."""
    mm = _get_module_manager(registry)
    return await mm._action_upgrade_module({
        "module_id": module_id,
        "path": body.path,
    })


@router.get("/modules/{module_id}/verify")
async def verify_module(
    module_id: str,
    registry: RegistryDep,
    _auth: AuthDep,
) -> dict[str, Any]:
    """Verify module integrity (signature + hash)."""
    mm = _get_module_manager(registry)
    return await mm._action_verify_module({"module_id": module_id})
