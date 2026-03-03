"""Health monitor for isolated worker processes.

Runs a background asyncio task that periodically pings each registered
worker via ``health_check`` RPC.  If a worker is unresponsive or has
crashed, the monitor auto-restarts it (up to ``max_restarts``).
"""

from __future__ import annotations

import asyncio
from typing import Any

from llmos_bridge.logging import get_logger

log = get_logger(__name__)


class HealthMonitor:
    """Monitor isolated worker processes and auto-restart on crash.

    Usage::

        monitor = HealthMonitor(check_interval=10.0)
        monitor.register(proxy_a)
        monitor.register(proxy_b)
        await monitor.start()
        # ... later ...
        await monitor.stop()
    """

    def __init__(self, check_interval: float = 10.0) -> None:
        self._check_interval = check_interval
        self._proxies: list[Any] = []  # list[IsolatedModuleProxy]
        self._task: asyncio.Task[None] | None = None
        self._running = False

    def register(self, proxy: Any) -> None:
        """Register a proxy for health monitoring."""
        self._proxies.append(proxy)

    @property
    def monitored_count(self) -> int:
        return len(self._proxies)

    @property
    def is_running(self) -> bool:
        return self._running and self._task is not None and not self._task.done()

    async def start(self) -> None:
        """Start the background health check loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._check_loop())
        log.info("health_monitor_started", interval=self._check_interval, proxies=len(self._proxies))

    async def stop(self) -> None:
        """Stop the health check loop and all monitored workers."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        # Stop all workers.
        for proxy in self._proxies:
            try:
                await proxy.stop()
            except Exception as exc:
                log.warning("worker_stop_error", module_id=getattr(proxy, "MODULE_ID", "?"), error=str(exc))

        log.info("health_monitor_stopped")

    async def check_all(self) -> list[dict[str, Any]]:
        """Run a single health check round on all proxies."""
        results = []
        for proxy in self._proxies:
            result = await self._check_one(proxy)
            results.append(result)
        return results

    async def _check_loop(self) -> None:
        """Background task: periodically check all registered proxies."""
        try:
            while self._running:
                await asyncio.sleep(self._check_interval)
                if not self._running:
                    break
                for proxy in list(self._proxies):
                    await self._check_one(proxy)
        except asyncio.CancelledError:
            pass

    async def _check_one(self, proxy: Any) -> dict[str, Any]:
        """Check a single proxy and restart if needed."""
        module_id = getattr(proxy, "MODULE_ID", "unknown")

        # If not started yet (lazy start), skip.
        if not getattr(proxy, "_started", False):
            return {"module_id": module_id, "status": "not_started"}

        # Check if alive.
        if not proxy.is_alive:
            log.warning("worker_dead_detected", module_id=module_id)
            restart_count = getattr(proxy, "_restart_count", 0)
            max_restarts = getattr(proxy, "_max_restarts", 3)

            if restart_count < max_restarts:
                try:
                    await proxy.restart()
                    log.info("worker_restarted", module_id=module_id, restart_count=restart_count + 1)
                    return {"module_id": module_id, "status": "restarted"}
                except Exception as exc:
                    log.error("worker_restart_failed", module_id=module_id, error=str(exc))
                    return {"module_id": module_id, "status": "restart_failed", "error": str(exc)}
            else:
                log.error("worker_max_restarts", module_id=module_id, max_restarts=max_restarts)
                return {"module_id": module_id, "status": "max_restarts_exhausted"}

        # Ping health check.
        try:
            health = await proxy.health_check()
            return {"module_id": module_id, "status": health.get("status", "ok")}
        except Exception as exc:
            log.warning("health_check_failed", module_id=module_id, error=str(exc))
            return {"module_id": module_id, "status": "unhealthy", "error": str(exc)}
