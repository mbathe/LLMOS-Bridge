"""API routes — Scanner pipeline introspection and management.

Provides endpoints to:
  - Check pipeline status and list scanners
  - Enable / disable individual scanners at runtime
  - Run a manual scan (dry-run, no plan execution)
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from llmos_bridge.api.dependencies import ScannerPipelineDep

router = APIRouter(prefix="/security/scanners", tags=["security-scanners"])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class ScanRequest(BaseModel):
    """Request body for a manual scan (dry-run)."""

    plan: dict[str, Any] = Field(description="Full IML plan JSON to scan.")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("")
async def list_scanners(pipeline: ScannerPipelineDep) -> dict[str, Any]:
    """List all registered scanners and pipeline configuration."""
    if pipeline is None:
        return {"enabled": False, "reason": "Scanner pipeline not configured."}
    return pipeline.status()


@router.post("/scan")
async def scan_plan(
    body: ScanRequest,
    pipeline: ScannerPipelineDep,
) -> dict[str, Any]:
    """Manually scan an IML plan without executing it (dry-run).

    Useful for testing scanner rules before submitting a plan.
    """
    if pipeline is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Scanner pipeline not configured.",
        )

    from llmos_bridge.protocol.models import IMLPlan

    try:
        plan = IMLPlan(**body.plan)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid IML plan: {exc}",
        ) from exc

    result = await pipeline.scan_input(plan)
    return result.to_dict()


@router.post("/{scanner_id}/enable")
async def enable_scanner(
    scanner_id: str,
    pipeline: ScannerPipelineDep,
) -> dict[str, Any]:
    """Enable a scanner at runtime."""
    if pipeline is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Scanner pipeline not configured.",
        )

    if not pipeline.registry.enable(scanner_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Scanner '{scanner_id}' not found.",
        )

    return {"scanner_id": scanner_id, "enabled": True}


@router.post("/{scanner_id}/disable")
async def disable_scanner(
    scanner_id: str,
    pipeline: ScannerPipelineDep,
) -> dict[str, Any]:
    """Disable a scanner at runtime."""
    if pipeline is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Scanner pipeline not configured.",
        )

    if not pipeline.registry.disable(scanner_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Scanner '{scanner_id}' not found.",
        )

    return {"scanner_id": scanner_id, "enabled": False}
