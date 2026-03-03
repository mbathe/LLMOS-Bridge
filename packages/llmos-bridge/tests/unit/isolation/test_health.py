"""Tests for isolation.health — HealthMonitor."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from llmos_bridge.isolation.health import HealthMonitor


def _make_proxy(module_id: str = "test", alive: bool = True, started: bool = True, restart_count: int = 0, max_restarts: int = 3):
    proxy = MagicMock()
    proxy.MODULE_ID = module_id
    proxy._started = started
    proxy._restart_count = restart_count
    proxy._max_restarts = max_restarts
    type(proxy).is_alive = PropertyMock(return_value=alive)
    proxy.health_check = AsyncMock(return_value={"status": "ok", "module_id": module_id})
    proxy.restart = AsyncMock()
    proxy.stop = AsyncMock()
    return proxy


class TestHealthMonitorBasic:
    def test_register(self):
        m = HealthMonitor()
        p = _make_proxy()
        m.register(p)
        assert m.monitored_count == 1

    def test_not_running_initially(self):
        m = HealthMonitor()
        assert m.is_running is False

    @pytest.mark.asyncio
    async def test_start_stop(self):
        m = HealthMonitor(check_interval=100.0)
        await m.start()
        assert m.is_running is True
        await m.stop()
        assert m.is_running is False

    @pytest.mark.asyncio
    async def test_stop_calls_proxy_stop(self):
        m = HealthMonitor()
        p = _make_proxy()
        m.register(p)
        await m.start()
        await m.stop()
        p.stop.assert_called_once()


class TestCheckAll:
    @pytest.mark.asyncio
    async def test_healthy_worker(self):
        m = HealthMonitor()
        p = _make_proxy("vision")
        m.register(p)
        results = await m.check_all()
        assert len(results) == 1
        assert results[0]["status"] == "ok"
        assert results[0]["module_id"] == "vision"

    @pytest.mark.asyncio
    async def test_not_started_skipped(self):
        m = HealthMonitor()
        p = _make_proxy("lazy_mod", started=False)
        m.register(p)
        results = await m.check_all()
        assert results[0]["status"] == "not_started"

    @pytest.mark.asyncio
    async def test_dead_worker_restarted(self):
        m = HealthMonitor()
        p = _make_proxy("dead_mod", alive=False, restart_count=0, max_restarts=3)
        m.register(p)
        results = await m.check_all()
        assert results[0]["status"] == "restarted"
        p.restart.assert_called_once()

    @pytest.mark.asyncio
    async def test_max_restarts_exhausted(self):
        m = HealthMonitor()
        p = _make_proxy("dying_mod", alive=False, restart_count=3, max_restarts=3)
        m.register(p)
        results = await m.check_all()
        assert results[0]["status"] == "max_restarts_exhausted"
        p.restart.assert_not_called()

    @pytest.mark.asyncio
    async def test_restart_failure(self):
        m = HealthMonitor()
        p = _make_proxy("fail_mod", alive=False, restart_count=0, max_restarts=3)
        p.restart = AsyncMock(side_effect=RuntimeError("spawn failed"))
        m.register(p)
        results = await m.check_all()
        assert results[0]["status"] == "restart_failed"

    @pytest.mark.asyncio
    async def test_health_check_error(self):
        m = HealthMonitor()
        p = _make_proxy("err_mod")
        p.health_check = AsyncMock(side_effect=TimeoutError("timeout"))
        m.register(p)
        results = await m.check_all()
        assert results[0]["status"] == "unhealthy"

    @pytest.mark.asyncio
    async def test_multiple_proxies(self):
        m = HealthMonitor()
        m.register(_make_proxy("mod_a"))
        m.register(_make_proxy("mod_b"))
        m.register(_make_proxy("mod_c", alive=False, restart_count=0))
        results = await m.check_all()
        assert len(results) == 3
        statuses = {r["module_id"]: r["status"] for r in results}
        assert statuses["mod_a"] == "ok"
        assert statuses["mod_b"] == "ok"
        assert statuses["mod_c"] == "restarted"
