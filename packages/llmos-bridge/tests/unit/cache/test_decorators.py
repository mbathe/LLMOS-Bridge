"""Unit tests — Cache decorators (cache/decorators.py).

Tests cover:
  - @cacheable metadata storage and defaults
  - @invalidates_cache metadata storage
  - make_cache_key: stability, path normalisation, key_params subset
  - make_invalidation_patterns: patterns and wildcard
  - Decorator stacking: @cacheable survives @requires_permission
  - @invalidates_cache survives stacking
  - functools.wraps: __name__ preserved
  - collect_cache_metadata: returns empty dict for plain functions
  - Combined: both decorators on the same action
"""

from __future__ import annotations

from pathlib import Path

import pytest

from llmos_bridge.cache.decorators import (
    _CACHE_META_ATTR,
    cacheable,
    collect_cache_metadata,
    invalidates_cache,
    make_cache_key,
    make_invalidation_patterns,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# @cacheable — metadata
# ---------------------------------------------------------------------------


class TestCacheableMetadata:

    def test_default_params_stored(self):
        @cacheable()
        async def _action_foo(self, params):
            pass

        meta = collect_cache_metadata(_action_foo)
        assert meta["cacheable"] is True
        assert meta["ttl"] == 300
        assert meta["key_params"] is None
        assert meta["shared"] is True
        assert meta["invalidated_by"] == []

    def test_custom_ttl(self):
        @cacheable(ttl=60)
        async def _action_foo(self, params):
            pass

        assert collect_cache_metadata(_action_foo)["ttl"] == 60

    def test_custom_key_params(self):
        @cacheable(key_params=["path", "encoding"])
        async def _action_foo(self, params):
            pass

        assert collect_cache_metadata(_action_foo)["key_params"] == ["path", "encoding"]

    def test_shared_false(self):
        @cacheable(shared=False)
        async def _action_foo(self, params):
            pass

        assert collect_cache_metadata(_action_foo)["shared"] is False

    def test_invalidated_by(self):
        @cacheable(invalidated_by=["write_file", "delete_file"])
        async def _action_foo(self, params):
            pass

        assert collect_cache_metadata(_action_foo)["invalidated_by"] == ["write_file", "delete_file"]

    def test_functools_wraps_preserves_name(self):
        @cacheable(ttl=10)
        async def _action_read_file(self, params):
            pass

        assert _action_read_file.__name__ == "_action_read_file"

    def test_meta_attr_set_on_wrapper(self):
        @cacheable()
        async def _action_foo(self, params):
            pass

        assert hasattr(_action_foo, _CACHE_META_ATTR)


# ---------------------------------------------------------------------------
# @invalidates_cache — metadata
# ---------------------------------------------------------------------------


class TestInvalidatesCacheMetadata:

    def test_single_action_stored(self):
        @invalidates_cache("read_file")
        async def _action_write_file(self, params):
            pass

        meta = collect_cache_metadata(_action_write_file)
        assert meta["invalidates"] == ["read_file"]

    def test_multiple_actions_stored(self):
        @invalidates_cache("read_file", "list_directory", "get_file_info")
        async def _action_write_file(self, params):
            pass

        meta = collect_cache_metadata(_action_write_file)
        assert meta["invalidates"] == ["read_file", "list_directory", "get_file_info"]

    def test_wildcard_stored(self):
        @invalidates_cache("*")
        async def _action_reset(self, params):
            pass

        meta = collect_cache_metadata(_action_reset)
        assert "*" in meta["invalidates"]

    def test_functools_wraps_preserves_name(self):
        @invalidates_cache("read_file")
        async def _action_write_file(self, params):
            pass

        assert _action_write_file.__name__ == "_action_write_file"


# ---------------------------------------------------------------------------
# collect_cache_metadata
# ---------------------------------------------------------------------------


class TestCollectCacheMetadata:

    def test_returns_empty_for_plain_function(self):
        async def _action_plain(self, params):
            pass

        assert collect_cache_metadata(_action_plain) == {}

    def test_returns_copy_not_reference(self):
        @cacheable(ttl=99)
        async def _action_foo(self, params):
            pass

        meta1 = collect_cache_metadata(_action_foo)
        meta2 = collect_cache_metadata(_action_foo)
        meta1["ttl"] = 0
        assert meta2["ttl"] == 99  # original unchanged


# ---------------------------------------------------------------------------
# make_cache_key
# ---------------------------------------------------------------------------


class TestMakeCacheKey:

    def test_key_format(self):
        key = make_cache_key("filesystem", "read_file", {"path": "/tmp/test.py"})
        assert key.startswith("llmos:cache:filesystem:read_file:")
        parts = key.split(":")
        assert len(parts) == 5  # llmos : cache : module : action : hash

    def test_same_params_produce_same_key(self):
        params = {"path": "/tmp/test.py", "encoding": "utf-8"}
        k1 = make_cache_key("filesystem", "read_file", params)
        k2 = make_cache_key("filesystem", "read_file", params)
        assert k1 == k2

    def test_different_params_produce_different_keys(self):
        k1 = make_cache_key("filesystem", "read_file", {"path": "/tmp/a.py"})
        k2 = make_cache_key("filesystem", "read_file", {"path": "/tmp/b.py"})
        assert k1 != k2

    def test_different_actions_produce_different_keys(self):
        params = {"path": "/tmp/test.py"}
        k1 = make_cache_key("filesystem", "read_file", params)
        k2 = make_cache_key("filesystem", "list_directory", params)
        assert k1 != k2

    def test_different_modules_produce_different_keys(self):
        params = {"path": "/tmp/test.py"}
        k1 = make_cache_key("filesystem", "read_file", params)
        k2 = make_cache_key("os_exec", "read_file", params)
        assert k1 != k2

    def test_path_normalisation_relative_vs_absolute(self, tmp_path):
        """'.' and the resolved absolute path must produce the same key."""
        # The hash covers the resolved path, so we need to compare resolved paths
        import os
        cwd = os.getcwd()
        k_relative = make_cache_key("filesystem", "list_directory", {"path": "."})
        k_absolute = make_cache_key("filesystem", "list_directory", {"path": cwd})
        assert k_relative == k_absolute

    def test_key_params_subset_used(self):
        """When key_params set, only listed params affect the key."""
        full_params = {"path": "/tmp/a.py", "encoding": "utf-8", "start_line": 1}
        subset_params = {"path": "/tmp/a.py", "encoding": "utf-8", "start_line": 99}

        # With key_params=["path", "encoding"], start_line is ignored
        k1 = make_cache_key("filesystem", "read_file", full_params, key_params=["path", "encoding"])
        k2 = make_cache_key("filesystem", "read_file", subset_params, key_params=["path", "encoding"])
        assert k1 == k2

    def test_key_params_none_uses_all_params(self):
        """key_params=None means all params are in the key."""
        p1 = {"path": "/tmp/a.py", "start_line": 1}
        p2 = {"path": "/tmp/a.py", "start_line": 99}
        k1 = make_cache_key("filesystem", "read_file", p1, key_params=None)
        k2 = make_cache_key("filesystem", "read_file", p2, key_params=None)
        assert k1 != k2

    def test_empty_params_stable(self):
        k1 = make_cache_key("os_exec", "get_system_info", {})
        k2 = make_cache_key("os_exec", "get_system_info", {})
        assert k1 == k2

    def test_param_order_does_not_affect_key(self):
        """Keys are order-independent (params sorted before hashing)."""
        p1 = {"b": 2, "a": 1}
        p2 = {"a": 1, "b": 2}
        k1 = make_cache_key("mod", "action", p1)
        k2 = make_cache_key("mod", "action", p2)
        assert k1 == k2


# ---------------------------------------------------------------------------
# make_invalidation_patterns
# ---------------------------------------------------------------------------


class TestMakeInvalidationPatterns:

    def test_single_action_pattern(self):
        patterns = make_invalidation_patterns("filesystem", ("read_file",))
        assert patterns == ["llmos:cache:filesystem:read_file:*"]

    def test_multiple_actions_patterns(self):
        patterns = make_invalidation_patterns(
            "filesystem", ("read_file", "list_directory", "get_file_info")
        )
        assert len(patterns) == 3
        assert "llmos:cache:filesystem:read_file:*" in patterns
        assert "llmos:cache:filesystem:list_directory:*" in patterns
        assert "llmos:cache:filesystem:get_file_info:*" in patterns

    def test_wildcard_returns_module_wildcard(self):
        patterns = make_invalidation_patterns("filesystem", ("*",))
        assert patterns == ["llmos:cache:filesystem:*"]

    def test_empty_tuple_returns_empty_list(self):
        patterns = make_invalidation_patterns("filesystem", ())
        assert patterns == []


# ---------------------------------------------------------------------------
# Decorator stacking — metadata survives @requires_permission
# ---------------------------------------------------------------------------


class TestDecoratorStacking:

    def test_cacheable_survives_requires_permission(self):
        from llmos_bridge.security.decorators import requires_permission

        @requires_permission("filesystem.read")
        @cacheable(ttl=60, key_params=["path"])
        async def _action_read_file(self, params):
            pass

        meta = collect_cache_metadata(_action_read_file)
        assert meta.get("cacheable") is True
        assert meta.get("ttl") == 60
        assert meta.get("key_params") == ["path"]

    def test_invalidates_cache_survives_requires_permission(self):
        from llmos_bridge.security.decorators import requires_permission

        @requires_permission("filesystem.write")
        @invalidates_cache("read_file", "list_directory")
        async def _action_write_file(self, params):
            pass

        meta = collect_cache_metadata(_action_write_file)
        assert "read_file" in meta.get("invalidates", [])
        assert "list_directory" in meta.get("invalidates", [])

    def test_cacheable_survives_full_security_stack(self):
        from llmos_bridge.security.decorators import (
            audit_trail,
            rate_limited,
            requires_permission,
        )

        @requires_permission("filesystem.read")
        @cacheable(ttl=30, key_params=["path"])
        @rate_limited(calls_per_minute=60)
        @audit_trail("standard")
        async def _action_read_file(self, params):
            pass

        meta = collect_cache_metadata(_action_read_file)
        assert meta.get("cacheable") is True
        assert meta.get("ttl") == 30

    def test_invalidates_survives_full_security_stack(self):
        from llmos_bridge.security.decorators import (
            audit_trail,
            rate_limited,
            requires_permission,
            sensitive_action,
        )
        from llmos_bridge.security.models import RiskLevel

        @requires_permission("filesystem.write")
        @invalidates_cache("read_file", "list_directory")
        @sensitive_action(RiskLevel.HIGH, irreversible=True)
        @rate_limited(calls_per_minute=60)
        @audit_trail("detailed")
        async def _action_delete_file(self, params):
            pass

        meta = collect_cache_metadata(_action_delete_file)
        assert "read_file" in meta.get("invalidates", [])

    def test_name_preserved_through_full_stack(self):
        from llmos_bridge.security.decorators import requires_permission

        @requires_permission("filesystem.read")
        @cacheable(ttl=60)
        async def _action_read_file(self, params):
            pass

        assert _action_read_file.__name__ == "_action_read_file"

    def test_cacheable_and_invalidates_combined_on_same_function(self):
        """Edge case: both decorators on the same action."""
        @cacheable(ttl=60)
        @invalidates_cache("other_action")
        async def _action_mixed(self, params):
            pass

        meta = collect_cache_metadata(_action_mixed)
        # Both sets of metadata should be present
        assert meta.get("cacheable") is True
        assert "other_action" in meta.get("invalidates", [])
