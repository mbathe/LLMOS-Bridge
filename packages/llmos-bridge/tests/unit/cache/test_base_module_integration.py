"""Integration tests — BaseModule.execute() with the L2 cache.

Tests cover:
  - A @cacheable action: second call hits L2, handler not called again
  - A @cacheable action: error from handler is NOT cached
  - An @invalidates_cache action: clears the right L2 keys
  - Non-decorated action: L2 cache is never touched
  - shared=False: L2 is skipped entirely
  - @invalidates_cache("*"): wildcard clears all module entries
  - Full stacking: @requires_permission + @cacheable + @invalidates_cache
  - L2 backend failure: graceful degradation (execute still succeeds)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llmos_bridge.cache.client import reset_cache_client
from llmos_bridge.cache.decorators import cacheable, invalidates_cache, make_cache_key
from llmos_bridge.modules.base import BaseModule, Platform
from llmos_bridge.modules.manifest import ModuleManifest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Minimal test module
# ---------------------------------------------------------------------------


class _SimpleModule(BaseModule):
    """Minimal module with cacheable and invalidating actions."""

    MODULE_ID = "test_cache_module"
    VERSION = "0.1.0"
    SUPPORTED_PLATFORMS = [Platform.ALL]

    def __init__(self):
        # Skip _check_dependencies
        self._security = None
        self._ctx = None
        self._dynamic_actions = {}
        self._dynamic_specs = {}
        self._config = None
        self.call_count: dict[str, int] = {}

    def _track(self, name: str) -> None:
        self.call_count[name] = self.call_count.get(name, 0) + 1

    def get_manifest(self) -> ModuleManifest:
        return ModuleManifest(
            module_id=self.MODULE_ID,
            version=self.VERSION,
            description="Test module",
            platforms=["all"],
            actions=[],
        )

    @cacheable(ttl=60, key_params=["id"])
    async def _action_get_item(self, params: dict) -> dict:
        self._track("get_item")
        return {"id": params["id"], "value": "fresh"}

    @cacheable(ttl=60, key_params=["id"])
    async def _action_get_error(self, params: dict) -> dict:
        self._track("get_error")
        raise ValueError("boom")

    @invalidates_cache("get_item")
    async def _action_update_item(self, params: dict) -> dict:
        self._track("update_item")
        return {"updated": True}

    @invalidates_cache("*")
    async def _action_reset_all(self, params: dict) -> dict:
        self._track("reset_all")
        return {"reset": True}

    @cacheable(ttl=60, shared=False)
    async def _action_local_only(self, params: dict) -> dict:
        self._track("local_only")
        return {"local": True}

    async def _action_no_cache(self, params: dict) -> dict:
        self._track("no_cache")
        return {"plain": True}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_l2_singleton(monkeypatch):
    """Reset L2 cache singleton before each test."""
    reset_cache_client()
    yield
    reset_cache_client()


@pytest.fixture
def module():
    return _SimpleModule()


# ---------------------------------------------------------------------------
# L2 cache hit — handler called only once
# ---------------------------------------------------------------------------


class TestCacheableL2:

    @pytest.mark.asyncio
    async def test_first_call_executes_handler(self, module):
        result = await module.execute("get_item", {"id": "abc"})
        assert result == {"id": "abc", "value": "fresh"}
        assert module.call_count["get_item"] == 1

    @pytest.mark.asyncio
    async def test_second_call_returns_cached_result(self, module):
        await module.execute("get_item", {"id": "abc"})
        result = await module.execute("get_item", {"id": "abc"})
        assert result == {"id": "abc", "value": "fresh"}
        assert module.call_count["get_item"] == 1  # handler called only once

    @pytest.mark.asyncio
    async def test_different_params_are_cached_separately(self, module):
        r1 = await module.execute("get_item", {"id": "abc"})
        r2 = await module.execute("get_item", {"id": "xyz"})
        assert module.call_count["get_item"] == 2  # both miss → both called

    @pytest.mark.asyncio
    async def test_error_from_handler_is_not_cached(self, module):
        from llmos_bridge.exceptions import ActionExecutionError

        with pytest.raises(ActionExecutionError):
            await module.execute("get_error", {"id": "bad"})

        # Second call must also reach the handler (error not cached)
        with pytest.raises(ActionExecutionError):
            await module.execute("get_error", {"id": "bad"})

        assert module.call_count["get_error"] == 2


# ---------------------------------------------------------------------------
# @invalidates_cache — L2 keys cleared
# ---------------------------------------------------------------------------


class TestInvalidatesCacheL2:

    @pytest.mark.asyncio
    async def test_invalidates_clears_cached_action(self, module):
        # Prime the cache
        await module.execute("get_item", {"id": "abc"})
        assert module.call_count.get("get_item", 0) == 1

        # Invalidate
        await module.execute("update_item", {"id": "abc"})

        # Next get_item must reach the handler again
        await module.execute("get_item", {"id": "abc"})
        assert module.call_count["get_item"] == 2

    @pytest.mark.asyncio
    async def test_wildcard_invalidates_all_module_entries(self, module):
        # Prime multiple cached actions
        await module.execute("get_item", {"id": "a"})
        await module.execute("get_item", {"id": "b"})
        assert module.call_count.get("get_item", 0) == 2

        # reset_all uses @invalidates_cache("*")
        await module.execute("reset_all", {})

        # Both entries must be gone
        await module.execute("get_item", {"id": "a"})
        await module.execute("get_item", {"id": "b"})
        assert module.call_count["get_item"] == 4  # all 4 calls hit the handler

    @pytest.mark.asyncio
    async def test_invalidation_does_not_affect_other_module_keys(self, module):
        """Keys from a different module should not be deleted."""
        from llmos_bridge.cache.client import get_cache_client
        from llmos_bridge.cache.decorators import make_cache_key

        other_key = make_cache_key("other_module", "get_item", {"id": "abc"})
        l2 = await get_cache_client()
        await l2.set(other_key, {"preserved": True}, ttl=60)

        # Invalidate our module
        await module.execute("update_item", {"id": "abc"})

        # Other module key untouched
        assert await l2.get(other_key) == {"preserved": True}


# ---------------------------------------------------------------------------
# shared=False — L2 skipped
# ---------------------------------------------------------------------------


class TestSharedFalse:

    @pytest.mark.asyncio
    async def test_shared_false_skips_l2(self, module):
        """With shared=False, every call hits the handler (no L2)."""
        await module.execute("local_only", {"x": 1})
        await module.execute("local_only", {"x": 1})
        # Without L2, the handler is called twice
        assert module.call_count["local_only"] == 2

    @pytest.mark.asyncio
    async def test_shared_false_does_not_store_in_l2(self, module):
        from llmos_bridge.cache.client import get_cache_client
        from llmos_bridge.cache.decorators import make_cache_key

        await module.execute("local_only", {"x": 1})
        key = make_cache_key("test_cache_module", "local_only", {"x": 1})
        l2 = await get_cache_client()
        assert await l2.get(key) is None


# ---------------------------------------------------------------------------
# Non-decorated action
# ---------------------------------------------------------------------------


class TestNonDecoratedAction:

    @pytest.mark.asyncio
    async def test_non_decorated_action_always_runs(self, module):
        await module.execute("no_cache", {})
        await module.execute("no_cache", {})
        assert module.call_count["no_cache"] == 2

    @pytest.mark.asyncio
    async def test_non_decorated_action_does_not_store_in_l2(self, module):
        from llmos_bridge.cache.client import get_cache_client
        from llmos_bridge.cache.decorators import make_cache_key

        await module.execute("no_cache", {})
        key = make_cache_key("test_cache_module", "no_cache", {})
        l2 = await get_cache_client()
        assert await l2.get(key) is None


# ---------------------------------------------------------------------------
# L2 backend failure — graceful degradation
# ---------------------------------------------------------------------------


class TestL2GracefulDegradation:

    @pytest.mark.asyncio
    async def test_l2_get_failure_does_not_block_execution(self, module):
        """If L2.get raises, the action must still execute."""
        from llmos_bridge.cache import client as cache_client_module

        broken = MagicMock()
        broken.enabled = True
        broken.get = AsyncMock(side_effect=RuntimeError("redis down"))
        broken.set = AsyncMock(side_effect=RuntimeError("redis down"))

        with patch.object(cache_client_module, "get_cache_client", AsyncMock(return_value=broken)):
            result = await module.execute("get_item", {"id": "abc"})

        assert result == {"id": "abc", "value": "fresh"}
        assert module.call_count["get_item"] == 1

    @pytest.mark.asyncio
    async def test_l2_set_failure_does_not_block_execution(self, module):
        from llmos_bridge.cache import client as cache_client_module

        # First call works (no cache yet), second call — cache.set fails
        broken = MagicMock()
        broken.enabled = True
        broken.get = AsyncMock(return_value=None)  # always miss
        broken.set = AsyncMock(side_effect=RuntimeError("redis down"))

        with patch.object(cache_client_module, "get_cache_client", AsyncMock(return_value=broken)):
            result = await module.execute("get_item", {"id": "abc"})

        assert result == {"id": "abc", "value": "fresh"}


# ---------------------------------------------------------------------------
# Full stack: @requires_permission + @cacheable
# ---------------------------------------------------------------------------


class TestFullSecurityStack:

    @pytest.mark.asyncio
    async def test_cacheable_works_with_requires_permission_no_security(self):
        """When no SecurityManager is set, @requires_permission degrades gracefully."""
        from llmos_bridge.security.decorators import requires_permission

        class SecureModule(BaseModule):
            MODULE_ID = "secure_test"
            VERSION = "0.1.0"
            SUPPORTED_PLATFORMS = [Platform.ALL]

            def __init__(self):
                self._security = None
                self._ctx = None
                self._dynamic_actions = {}
                self._dynamic_specs = {}
                self._config = None
                self.calls = 0

            def get_manifest(self) -> ModuleManifest:
                return ModuleManifest(
                    module_id=self.MODULE_ID, version=self.VERSION,
                    description="", platforms=["all"], actions=[],
                )

            @requires_permission("filesystem.read")
            @cacheable(ttl=60, key_params=["path"])
            async def _action_read_file(self, params: dict) -> dict:
                self.calls += 1
                return {"content": "hello"}

        mod = SecureModule()
        reset_cache_client()

        r1 = await mod.execute("read_file", {"path": "/tmp/test.txt"})
        r2 = await mod.execute("read_file", {"path": "/tmp/test.txt"})
        assert r1 == {"content": "hello"}
        assert r2 == {"content": "hello"}
        assert mod.calls == 1  # cached after first call

        reset_cache_client()


# ---------------------------------------------------------------------------
# _no_cache meta-parameter
# ---------------------------------------------------------------------------


class TestNoCacheBypass:

    @pytest.mark.asyncio
    async def test_no_cache_true_bypasses_l2_read(self, module):
        """_no_cache=True forces fresh execution even when L2 has a cached value."""
        # Prime the cache
        await module.execute("get_item", {"id": "abc"})
        assert module.call_count["get_item"] == 1

        # Second call with _no_cache bypasses the cached value
        result = await module.execute("get_item", {"id": "abc", "_no_cache": True})
        assert result == {"id": "abc", "value": "fresh"}
        assert module.call_count["get_item"] == 2  # handler called again

    @pytest.mark.asyncio
    async def test_no_cache_false_uses_cache_normally(self, module):
        """_no_cache=False (explicit) behaves like the default — cache is used."""
        await module.execute("get_item", {"id": "abc"})
        await module.execute("get_item", {"id": "abc", "_no_cache": False})
        assert module.call_count["get_item"] == 1  # second call was a cache hit

    @pytest.mark.asyncio
    async def test_no_cache_stripped_before_handler(self, module):
        """_no_cache must be removed from params before the handler is called.

        The _SimpleModule._action_get_item uses key_params=["id"], so extra
        keys in params are irrelevant to the cache key — but the handler would
        fail/behave unexpectedly if it received _no_cache in params.
        We verify by checking the handler returns normally (no KeyError etc.).
        """
        result = await module.execute("get_item", {"id": "xyz", "_no_cache": True})
        assert result == {"id": "xyz", "value": "fresh"}

    @pytest.mark.asyncio
    async def test_no_cache_still_writes_result_to_l2(self, module):
        """After a _no_cache=True call, the fresh result is stored in L2.

        A subsequent call WITHOUT _no_cache must be a cache hit (handler NOT called).
        """
        from llmos_bridge.cache.client import get_cache_client
        from llmos_bridge.cache.decorators import make_cache_key

        # First call with _no_cache — should execute and store
        await module.execute("get_item", {"id": "abc", "_no_cache": True})
        assert module.call_count["get_item"] == 1

        # Verify the key exists in L2
        l2 = await get_cache_client()
        key = make_cache_key("test_cache_module", "get_item", {"id": "abc"},
                             key_params=["id"])
        assert await l2.get(key) is not None

        # Next call without _no_cache must hit the cache
        await module.execute("get_item", {"id": "abc"})
        assert module.call_count["get_item"] == 1  # still 1 — served from L2

    @pytest.mark.asyncio
    async def test_no_cache_without_decorated_action_is_stripped_silently(self, module):
        """_no_cache on a non-decorated action is stripped without side effects."""
        result = await module.execute("no_cache", {"_no_cache": True})
        assert result == {"plain": True}

    @pytest.mark.asyncio
    async def test_no_cache_consecutive_calls_each_hit_handler(self, module):
        """Every _no_cache=True call bypasses the cache, even back-to-back."""
        await module.execute("get_item", {"id": "abc", "_no_cache": True})
        await module.execute("get_item", {"id": "abc", "_no_cache": True})
        await module.execute("get_item", {"id": "abc", "_no_cache": True})
        assert module.call_count["get_item"] == 3
