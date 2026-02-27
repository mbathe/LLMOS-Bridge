"""GET /health — enriched health endpoint with per-module status."""

from __future__ import annotations

import time

from fastapi import APIRouter

from llmos_bridge import __protocol_version__, __version__
from llmos_bridge.api.dependencies import ConfigDep, RegistryDep, StateStoreDep
from llmos_bridge.api.schemas import HealthResponse, ModuleStatusDetail
from llmos_bridge.protocol.models import PlanStatus

router = APIRouter(tags=["health"])

_start_time = time.time()


@router.get("/health", response_model=HealthResponse, summary="Daemon health check")
async def health(
    registry: RegistryDep,
    config: ConfigDep,
    state_store: StateStoreDep,
) -> HealthResponse:
    available = registry.list_available()
    all_modules = registry.list_modules()
    failed_count = len(all_modules) - len(available)

    # Per-module status breakdown.
    report = registry.status_report()
    modules_detail = ModuleStatusDetail(
        available=report["available"],
        failed=report.get("failed", {}),
        platform_excluded=report.get("platform_excluded", {}),
    )

    # Count active (running) plans.
    try:
        running_plans = await state_store.list_plans(status=PlanStatus.RUNNING, limit=1000)
        active_count = len(running_plans)
    except Exception:
        active_count = 0

    return HealthResponse(
        status="ok",
        version=__version__,
        protocol_version=__protocol_version__,
        uptime_seconds=round(time.time() - _start_time, 2),
        modules_loaded=len(available),
        modules_failed=failed_count,
        modules=modules_detail,
        active_plans=active_count,
    )
