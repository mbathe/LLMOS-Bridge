"""PermissionProxy — HTTP proxy for remote permission checks.

In ``mode="node"``, this instance delegates permission checks to the
orchestrator via ``GET /admin/security/permissions/check``.

Features:

- **LRU + TTL cache**: avoids repeated network round-trips for the same
  permission/module pair.
- **Stale-cache fallback**: if the orchestrator is unreachable, a previously
  cached result is returned (even if expired) rather than failing hard.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from llmos_bridge.logging import get_logger

log = get_logger(__name__)


class PermissionProxy:
    """Check permissions on a remote orchestrator over HTTP.

    Parameters
    ----------
    orchestrator_url:
        Base URL of the orchestrator daemon (e.g. ``http://10.0.0.1:40000``).
    api_token:
        Optional bearer token for authenticating to the orchestrator.
    cache_size:
        Maximum entries in the LRU cache.
    cache_ttl:
        Seconds before a cache entry is considered stale.
    timeout:
        HTTP request timeout in seconds.
    """

    def __init__(
        self,
        orchestrator_url: str,
        api_token: str | None = None,
        cache_size: int = 256,
        cache_ttl: float = 60.0,
        timeout: float = 5.0,
    ) -> None:
        self._orchestrator_url = orchestrator_url.rstrip("/")
        self._api_token = api_token
        self._cache: dict[tuple[str, str], tuple[dict[str, Any], float]] = {}
        self._cache_size = cache_size
        self._cache_ttl = cache_ttl
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    # -- Lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        """Create the HTTP client."""
        headers: dict[str, str] = {}
        if self._api_token:
            headers["Authorization"] = f"Bearer {self._api_token}"
        self._client = httpx.AsyncClient(
            headers=headers,
            timeout=self._timeout,
        )
        log.info(
            "permission_proxy_started",
            orchestrator=self._orchestrator_url,
        )

    async def stop(self) -> None:
        """Close the HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
            log.info("permission_proxy_stopped")

    @property
    def is_connected(self) -> bool:
        return self._client is not None

    # -- Permission check ----------------------------------------------------

    async def check_permission(
        self,
        permission: str,
        module_id: str,
    ) -> dict[str, Any]:
        """Check whether *permission* is granted for *module_id*.

        Lookup order:
        1. Fresh cache hit → return immediately.
        2. HTTP GET to orchestrator → cache & return.
        3. On network error → return stale cache if available, else error dict.
        """
        cache_key = (permission, module_id)

        # 1. Fresh cache hit.
        if cache_key in self._cache:
            result, ts = self._cache[cache_key]
            if time.time() - ts < self._cache_ttl:
                return result

        # 2. HTTP call.
        if self._client is None:
            return {"granted": False, "error": "PermissionProxy not started"}

        try:
            resp = await self._client.get(
                f"{self._orchestrator_url}/admin/security/permissions/check",
                params={"permission": permission, "module_id": module_id},
            )
            resp.raise_for_status()
            result = resp.json()
            self._cache[cache_key] = (result, time.time())
            self._evict_if_needed()
            return result
        except Exception as exc:
            log.warning(
                "permission_proxy_error",
                permission=permission,
                module_id=module_id,
                error=str(exc),
            )
            # 3. Stale cache fallback.
            if cache_key in self._cache:
                return self._cache[cache_key][0]
            return {"granted": False, "error": str(exc)}

    # -- Internal cache management -------------------------------------------

    def _evict_if_needed(self) -> None:
        """Evict oldest entries when cache exceeds *cache_size*."""
        if len(self._cache) <= self._cache_size:
            return
        # Remove the oldest entries (by timestamp).
        sorted_keys = sorted(self._cache, key=lambda k: self._cache[k][1])
        excess = len(self._cache) - self._cache_size
        for key in sorted_keys[:excess]:
            del self._cache[key]

    def clear_cache(self) -> None:
        """Clear the entire permission cache."""
        self._cache.clear()
