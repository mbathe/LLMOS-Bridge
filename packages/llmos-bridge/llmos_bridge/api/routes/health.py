"""GET /health"""

from __future__ import annotations

import time

from fastapi import APIRouter

from llmos_bridge import __protocol_version__, __version__
from llmos_bridge.api.dependencies import ConfigDep, RegistryDep
from llmos_bridge.api.schemas import HealthResponse

router = APIRouter(tags=["health"])

_start_time = time.time()


@router.get("/health", response_model=HealthResponse, summary="Daemon health check")
async def health(registry: RegistryDep, config: ConfigDep) -> HealthResponse:
    available = registry.list_available()
    all_modules = registry.list_modules()
    failed_count = len(all_modules) - len(available)

    return HealthResponse(
        status="ok",
        version=__version__,
        protocol_version=__protocol_version__,
        uptime_seconds=round(time.time() - _start_time, 2),
        modules_loaded=len(available),
        modules_failed=failed_count,
    )
