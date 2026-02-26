"""GET /modules, GET /modules/{id}, GET /modules/{id}/schema"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from llmos_bridge.api.dependencies import AuthDep, RegistryDep
from llmos_bridge.api.schemas import ModuleActionSchema, ModuleManifestResponse
from llmos_bridge.exceptions import ModuleLoadError, ModuleNotFoundError

router = APIRouter(prefix="/modules", tags=["modules"])


@router.get("", summary="List all loaded modules")
async def list_modules(
    _auth: AuthDep,
    registry: RegistryDep,
) -> list[dict[str, object]]:
    modules = []
    for module_id in registry.list_modules():
        available = registry.is_available(module_id)
        entry: dict[str, object] = {"module_id": module_id, "available": available}
        if available:
            try:
                manifest = registry.get_manifest(module_id)
                entry["version"] = manifest.version
                entry["description"] = manifest.description
                entry["action_count"] = len(manifest.actions)
            except Exception:
                pass
        modules.append(entry)
    return modules


@router.get("/{module_id}", response_model=ModuleManifestResponse, summary="Get module manifest")
async def get_module(
    module_id: str,
    _auth: AuthDep,
    registry: RegistryDep,
) -> ModuleManifestResponse:
    try:
        manifest = registry.get_manifest(module_id)
    except ModuleNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Module '{module_id}' is not registered.",
        )
    except ModuleLoadError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        )

    actions = [
        ModuleActionSchema(
            name=a.name,
            description=a.description,
            params_schema=a.to_json_schema(),
            returns=a.returns,
            permission_required=a.permission_required,
            platforms=a.platforms,
            examples=a.examples,
        )
        for a in manifest.actions
    ]

    return ModuleManifestResponse(
        module_id=manifest.module_id,
        version=manifest.version,
        description=manifest.description,
        platforms=manifest.platforms,
        actions=actions,
        tags=manifest.tags,
    )


@router.get(
    "/{module_id}/actions/{action_name}/schema",
    summary="Get the JSONSchema for a specific action's params",
)
async def get_action_schema(
    module_id: str,
    action_name: str,
    _auth: AuthDep,
    registry: RegistryDep,
) -> dict[str, object]:
    try:
        manifest = registry.get_manifest(module_id)
    except ModuleNotFoundError:
        raise HTTPException(status_code=404, detail=f"Module '{module_id}' not found.")

    action = manifest.get_action(action_name)
    if action is None:
        raise HTTPException(
            status_code=404,
            detail=f"Action '{action_name}' not found in module '{module_id}'.",
        )

    return action.to_json_schema()
