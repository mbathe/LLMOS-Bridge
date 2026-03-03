"""Unit tests — PermissionProxy (HTTP permission checks with cache).

Tests cover:
- check_permission: fresh cache hit
- check_permission: cache miss → HTTP call → cache update
- check_permission: TTL expiry → refresh
- check_permission: stale cache fallback on network error
- check_permission: no cache + network error → error dict
- LRU eviction when cache exceeds size
- start / stop lifecycle
- Not started → error dict
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llmos_bridge.cluster.permission_proxy import PermissionProxy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_httpx_response(json_data: dict, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        import httpx

        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=resp,
        )
    return resp


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPermissionProxyLifecycle:
    @pytest.mark.asyncio
    async def test_start_creates_client(self) -> None:
        proxy = PermissionProxy("http://orch:40000")
        await proxy.start()
        assert proxy.is_connected is True
        await proxy.stop()

    @pytest.mark.asyncio
    async def test_stop_closes_client(self) -> None:
        proxy = PermissionProxy("http://orch:40000")
        await proxy.start()
        await proxy.stop()
        assert proxy.is_connected is False

    @pytest.mark.asyncio
    async def test_stop_when_not_started(self) -> None:
        proxy = PermissionProxy("http://orch:40000")
        await proxy.stop()  # Should not raise

    @pytest.mark.asyncio
    async def test_start_with_token(self) -> None:
        proxy = PermissionProxy("http://orch:40000", api_token="secret")
        await proxy.start()
        assert proxy._client is not None
        assert "Authorization" in proxy._client.headers
        await proxy.stop()

    @pytest.mark.asyncio
    async def test_strips_trailing_slash(self) -> None:
        proxy = PermissionProxy("http://orch:40000/")
        assert proxy._orchestrator_url == "http://orch:40000"


# ---------------------------------------------------------------------------
# Cache: hit / miss / TTL
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPermissionProxyCaching:
    @pytest.mark.asyncio
    async def test_fresh_cache_hit(self) -> None:
        proxy = PermissionProxy("http://orch:40000", cache_ttl=60.0)
        await proxy.start()

        # Pre-populate cache.
        result = {"granted": True, "permission": "FILESYSTEM_READ"}
        proxy._cache[("FILESYSTEM_READ", "filesystem")] = (result, time.time())

        got = await proxy.check_permission("FILESYSTEM_READ", "filesystem")
        assert got["granted"] is True
        await proxy.stop()

    @pytest.mark.asyncio
    async def test_cache_miss_calls_http(self) -> None:
        proxy = PermissionProxy("http://orch:40000", cache_ttl=60.0)
        await proxy.start()

        resp_data = {"granted": True, "permission": "EXEC_COMMAND"}
        proxy._client.get = AsyncMock(return_value=_mock_httpx_response(resp_data))  # type: ignore

        got = await proxy.check_permission("EXEC_COMMAND", "os_exec")
        assert got["granted"] is True
        proxy._client.get.assert_awaited_once()  # type: ignore

        # Now cached.
        assert ("EXEC_COMMAND", "os_exec") in proxy._cache
        await proxy.stop()

    @pytest.mark.asyncio
    async def test_ttl_expiry_refreshes(self) -> None:
        proxy = PermissionProxy("http://orch:40000", cache_ttl=0.01)
        await proxy.start()

        # Pre-populate with expired entry.
        old_result = {"granted": False}
        proxy._cache[("PERM", "mod")] = (old_result, time.time() - 1.0)

        new_result = {"granted": True}
        proxy._client.get = AsyncMock(return_value=_mock_httpx_response(new_result))  # type: ignore

        got = await proxy.check_permission("PERM", "mod")
        assert got["granted"] is True
        proxy._client.get.assert_awaited_once()  # type: ignore
        await proxy.stop()

    @pytest.mark.asyncio
    async def test_stale_cache_fallback_on_error(self) -> None:
        proxy = PermissionProxy("http://orch:40000", cache_ttl=0.01)
        await proxy.start()

        # Pre-populate with expired entry.
        stale_result = {"granted": True}
        proxy._cache[("PERM", "mod")] = (stale_result, time.time() - 100.0)

        proxy._client.get = AsyncMock(side_effect=ConnectionError("refused"))  # type: ignore

        got = await proxy.check_permission("PERM", "mod")
        assert got["granted"] is True  # Stale cache returned
        await proxy.stop()

    @pytest.mark.asyncio
    async def test_no_cache_no_connection_returns_error(self) -> None:
        proxy = PermissionProxy("http://orch:40000", cache_ttl=60.0)
        await proxy.start()

        proxy._client.get = AsyncMock(side_effect=ConnectionError("refused"))  # type: ignore

        got = await proxy.check_permission("PERM", "mod")
        assert got["granted"] is False
        assert "error" in got
        await proxy.stop()

    @pytest.mark.asyncio
    async def test_not_started_returns_error(self) -> None:
        proxy = PermissionProxy("http://orch:40000")
        # Do not call start()

        got = await proxy.check_permission("PERM", "mod")
        assert got["granted"] is False
        assert "not started" in got.get("error", "").lower()


# ---------------------------------------------------------------------------
# LRU eviction
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPermissionProxyEviction:
    @pytest.mark.asyncio
    async def test_evicts_oldest_when_full(self) -> None:
        proxy = PermissionProxy("http://orch:40000", cache_size=3, cache_ttl=60.0)
        await proxy.start()

        now = time.time()
        proxy._cache[("a", "m")] = ({"granted": True}, now - 3)
        proxy._cache[("b", "m")] = ({"granted": True}, now - 2)
        proxy._cache[("c", "m")] = ({"granted": True}, now - 1)

        # Add a 4th entry via HTTP.
        proxy._client.get = AsyncMock(  # type: ignore
            return_value=_mock_httpx_response({"granted": True})
        )
        await proxy.check_permission("d", "m")

        # Oldest entry ("a") should be evicted.
        assert ("a", "m") not in proxy._cache
        assert len(proxy._cache) == 3
        await proxy.stop()

    def test_clear_cache(self) -> None:
        proxy = PermissionProxy("http://orch:40000")
        proxy._cache[("a", "m")] = ({"granted": True}, time.time())
        proxy._cache[("b", "m")] = ({"granted": True}, time.time())

        proxy.clear_cache()
        assert len(proxy._cache) == 0


# ---------------------------------------------------------------------------
# HTTP call details
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPermissionProxyHTTPCalls:
    @pytest.mark.asyncio
    async def test_correct_url_and_params(self) -> None:
        proxy = PermissionProxy("http://orch:40000", cache_ttl=60.0)
        await proxy.start()

        proxy._client.get = AsyncMock(  # type: ignore
            return_value=_mock_httpx_response({"granted": True})
        )

        await proxy.check_permission("FILESYSTEM_READ", "filesystem")

        call_args = proxy._client.get.call_args  # type: ignore
        assert "/admin/security/permissions/check" in call_args[0][0]
        params = call_args[1]["params"]
        assert params["permission"] == "FILESYSTEM_READ"
        assert params["module_id"] == "filesystem"
        await proxy.stop()

    @pytest.mark.asyncio
    async def test_http_error_falls_back(self) -> None:
        proxy = PermissionProxy("http://orch:40000", cache_ttl=60.0)
        await proxy.start()

        proxy._client.get = AsyncMock(  # type: ignore
            return_value=_mock_httpx_response({"error": "forbidden"}, status_code=403)
        )

        got = await proxy.check_permission("PERM", "mod")
        # HTTP error → no cache → error dict
        assert got["granted"] is False
        await proxy.stop()
