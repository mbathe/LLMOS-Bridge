"""Unit tests — Production robustness features.

Covers:
  - Rate limiting middleware
  - Result size truncation
  - Enriched health endpoint
  - Auto-purge old plans
"""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llmos_bridge.api.middleware import RateLimitMiddleware
from llmos_bridge.orchestration.executor import _truncate_result


# ---------------------------------------------------------------------------
# Tests — Rate Limiting
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRateLimitMiddleware:
    def _make_middleware(self, max_per_minute: int = 5) -> RateLimitMiddleware:
        return RateLimitMiddleware(app=MagicMock(), max_per_minute=max_per_minute)

    def test_is_not_rate_limited_under_threshold(self) -> None:
        mw = self._make_middleware(max_per_minute=10)
        for _ in range(10):
            assert not mw._is_rate_limited("127.0.0.1")

    def test_is_rate_limited_at_threshold(self) -> None:
        mw = self._make_middleware(max_per_minute=3)
        assert not mw._is_rate_limited("1.2.3.4")
        assert not mw._is_rate_limited("1.2.3.4")
        assert not mw._is_rate_limited("1.2.3.4")
        # 4th request should be blocked.
        assert mw._is_rate_limited("1.2.3.4")

    def test_different_ips_tracked_separately(self) -> None:
        mw = self._make_middleware(max_per_minute=2)
        assert not mw._is_rate_limited("10.0.0.1")
        assert not mw._is_rate_limited("10.0.0.1")
        assert mw._is_rate_limited("10.0.0.1")
        # Different IP — fresh quota.
        assert not mw._is_rate_limited("10.0.0.2")

    def test_old_entries_expire(self) -> None:
        mw = self._make_middleware(max_per_minute=2)
        mw._window = 1.0  # 1 second window for testing.

        assert not mw._is_rate_limited("ip1")
        assert not mw._is_rate_limited("ip1")
        assert mw._is_rate_limited("ip1")

        # Manually expire entries.
        old_time = time.time() - 2.0
        mw._hits["ip1"].clear()
        mw._hits["ip1"].append(old_time)
        # Should now accept (old entry expired during check).
        assert not mw._is_rate_limited("ip1")

    def test_client_ip_from_header(self) -> None:
        mw = self._make_middleware()
        request = MagicMock()
        request.headers = {"X-Forwarded-For": "203.0.113.1, 10.0.0.1"}
        assert mw._client_ip(request) == "203.0.113.1"

    def test_client_ip_from_socket(self) -> None:
        mw = self._make_middleware()
        request = MagicMock()
        request.headers = {}
        request.client.host = "192.168.1.1"
        assert mw._client_ip(request) == "192.168.1.1"

    @pytest.mark.asyncio
    async def test_dispatch_rate_limits_post_plans(self) -> None:
        mw = self._make_middleware(max_per_minute=1)
        call_next = AsyncMock(return_value=MagicMock(status_code=200))

        # Build mock request for POST /plans.
        request = MagicMock()
        request.method = "POST"
        request.url.path = "/plans"
        request.headers = {}
        request.client.host = "127.0.0.1"

        # First request passes.
        resp1 = await mw.dispatch(request, call_next)
        assert call_next.await_count == 1

        # Second request is rate limited.
        resp2 = await mw.dispatch(request, call_next)
        assert resp2.status_code == 429

    @pytest.mark.asyncio
    async def test_dispatch_does_not_limit_get(self) -> None:
        mw = self._make_middleware(max_per_minute=1)
        call_next = AsyncMock(return_value=MagicMock(status_code=200))

        request = MagicMock()
        request.method = "GET"
        request.url.path = "/plans"

        # GET is not rate limited.
        await mw.dispatch(request, call_next)
        await mw.dispatch(request, call_next)
        assert call_next.await_count == 2

    @pytest.mark.asyncio
    async def test_dispatch_does_not_limit_other_post(self) -> None:
        mw = self._make_middleware(max_per_minute=1)
        call_next = AsyncMock(return_value=MagicMock(status_code=200))

        request = MagicMock()
        request.method = "POST"
        request.url.path = "/plans/123/actions/456/approve"

        # POST to other endpoints is not rate limited.
        await mw.dispatch(request, call_next)
        await mw.dispatch(request, call_next)
        assert call_next.await_count == 2


# ---------------------------------------------------------------------------
# Tests — Result Size Truncation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestResultTruncation:
    def test_small_result_unchanged(self) -> None:
        data = {"key": "value", "number": 42}
        result = _truncate_result(data, max_bytes=1024)
        assert result == data

    def test_large_result_truncated(self) -> None:
        data = {"data": "x" * 10_000}
        result = _truncate_result(data, max_bytes=1000)

        assert result["_truncated"] is True
        assert result["_original_size"] > 1000
        assert result["_max_size"] == 1000
        assert len(result["data"]) == 1000
        assert "warning" in result

    def test_string_result_truncated(self) -> None:
        data = "a" * 5000
        result = _truncate_result(data, max_bytes=100)
        assert result["_truncated"] is True

    def test_non_serializable_handled(self) -> None:
        # Objects that can't be JSON-serialized fall back to str().
        data = {"value": object()}
        result = _truncate_result(data, max_bytes=100_000)
        # Should not raise, may or may not truncate depending on str() size.
        assert result is not None

    def test_exact_boundary_not_truncated(self) -> None:
        # A result that serialises to exactly max_bytes should not be truncated.
        data = "x" * 90  # '"' + 'x'*90 + '"' = 92 bytes
        result = _truncate_result(data, max_bytes=92)
        assert result == data

    def test_list_result_truncated(self) -> None:
        data = list(range(10_000))
        result = _truncate_result(data, max_bytes=500)
        assert result["_truncated"] is True

    def test_none_result_unchanged(self) -> None:
        result = _truncate_result(None, max_bytes=100)
        assert result is None

    def test_empty_dict_unchanged(self) -> None:
        result = _truncate_result({}, max_bytes=100)
        assert result == {}


# ---------------------------------------------------------------------------
# Tests — Enriched Health Response
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEnrichedHealth:
    def test_health_response_has_modules_field(self) -> None:
        from llmos_bridge.api.schemas import HealthResponse, ModuleStatusDetail

        detail = ModuleStatusDetail(
            available=["filesystem", "os_exec"],
            failed={"browser": "playwright not installed"},
            platform_excluded={},
        )
        resp = HealthResponse(
            version="1.0.0",
            protocol_version="2.0",
            uptime_seconds=100.0,
            modules_loaded=2,
            modules_failed=1,
            modules=detail,
            active_plans=3,
        )
        assert resp.modules is not None
        assert "filesystem" in resp.modules.available
        assert resp.modules.failed["browser"] == "playwright not installed"
        assert resp.active_plans == 3

    def test_health_response_backward_compatible(self) -> None:
        from llmos_bridge.api.schemas import HealthResponse

        # Old-style response without modules field should still work.
        resp = HealthResponse(
            version="1.0.0",
            protocol_version="2.0",
            uptime_seconds=50.0,
            modules_loaded=5,
            modules_failed=0,
        )
        assert resp.modules is None
        assert resp.active_plans == 0

    def test_module_status_detail_serialization(self) -> None:
        from llmos_bridge.api.schemas import ModuleStatusDetail

        detail = ModuleStatusDetail(
            available=["a", "b"],
            failed={"c": "missing dep"},
            platform_excluded={"d": "wrong os"},
        )
        d = detail.model_dump()
        assert d["available"] == ["a", "b"]
        assert d["failed"] == {"c": "missing dep"}
        assert d["platform_excluded"] == {"d": "wrong os"}


# ---------------------------------------------------------------------------
# Tests — Auto-purge old plans
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAutoPurge:
    @pytest.mark.asyncio
    async def test_purge_old_plans(self, tmp_path) -> None:
        from llmos_bridge.orchestration.state import PlanStateStore, ExecutionState
        from llmos_bridge.protocol.models import PlanStatus

        store = PlanStateStore(tmp_path / "purge_test.db")
        await store.init()

        old_ts = time.time() - 86400 * 10  # 10 days ago

        # Create a completed plan, then backdate its timestamps.
        state = ExecutionState(plan_id="old-plan-1", plan_status=PlanStatus.COMPLETED)
        await store.create(state)
        await store._conn.execute(
            "UPDATE plans SET updated_at=?, created_at=? WHERE plan_id=?",
            (old_ts, old_ts, "old-plan-1"),
        )
        await store._conn.commit()

        # Create a recent plan.
        state2 = ExecutionState(plan_id="new-plan-1", plan_status=PlanStatus.COMPLETED)
        await store.create(state2)

        # Create a running plan (should NOT be purged regardless of age).
        state3 = ExecutionState(plan_id="running-plan", plan_status=PlanStatus.RUNNING)
        await store.create(state3)
        await store._conn.execute(
            "UPDATE plans SET updated_at=?, created_at=? WHERE plan_id=?",
            (old_ts, old_ts, "running-plan"),
        )
        await store._conn.commit()

        # Purge plans older than 7 days.
        purged = await store.purge_old_plans(86400 * 7)
        assert purged == 1

        # Verify old plan is gone.
        assert await store.get("old-plan-1") is None
        # Recent plan still exists.
        assert await store.get("new-plan-1") is not None
        # Running plan still exists.
        assert await store.get("running-plan") is not None

        await store.close()

    @pytest.mark.asyncio
    async def test_purge_nothing_to_purge(self, tmp_path) -> None:
        from llmos_bridge.orchestration.state import PlanStateStore

        store = PlanStateStore(tmp_path / "purge_empty.db")
        await store.init()

        purged = await store.purge_old_plans(86400)
        assert purged == 0

        await store.close()

    @pytest.mark.asyncio
    async def test_purge_with_actions(self, tmp_path) -> None:
        from llmos_bridge.orchestration.state import (
            PlanStateStore, ExecutionState, ActionState
        )
        from llmos_bridge.protocol.models import PlanStatus, ActionStatus

        store = PlanStateStore(tmp_path / "purge_actions.db")
        await store.init()

        old_ts = time.time() - 86400 * 30

        # Create plan with actions.
        state = ExecutionState(
            plan_id="old-with-actions",
            plan_status=PlanStatus.FAILED,
        )
        state.actions["a1"] = ActionState(
            action_id="a1",
            status=ActionStatus.FAILED,
            module="test",
            action="test_action",
        )
        await store.create(state)
        await store._conn.execute(
            "UPDATE plans SET updated_at=?, created_at=? WHERE plan_id=?",
            (old_ts, old_ts, "old-with-actions"),
        )
        await store._conn.commit()

        purged = await store.purge_old_plans(86400 * 7)
        assert purged == 1
        assert await store.get("old-with-actions") is None

        await store.close()


# ---------------------------------------------------------------------------
# Tests — Config defaults
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRobustnessConfig:
    def test_default_rate_limit(self) -> None:
        from llmos_bridge.config import ServerConfig

        cfg = ServerConfig()
        assert cfg.rate_limit_per_minute == 60

    def test_default_max_result_size(self) -> None:
        from llmos_bridge.config import ServerConfig

        cfg = ServerConfig()
        assert cfg.max_result_size == 524_288  # 512 KB

    def test_default_retention(self) -> None:
        from llmos_bridge.config import ServerConfig

        cfg = ServerConfig()
        assert cfg.plan_retention_hours == 168  # 7 days

    def test_custom_rate_limit(self) -> None:
        from llmos_bridge.config import ServerConfig

        cfg = ServerConfig(rate_limit_per_minute=100)
        assert cfg.rate_limit_per_minute == 100
