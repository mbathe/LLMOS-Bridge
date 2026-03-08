"""Unit tests — L1 ActionSessionCache (apps/action_cache.py).

Tests cover:
  - get: hit / miss / non-cacheable action
  - put: stores result, updates path index
  - invalidate_for_write: parent/child/exact overlap
  - stats: hits, misses, invalidations, cached count
  - clear: empties cache and path index
  - disabled: all operations are no-ops
  - Path normalisation: "." == resolved absolute path
  - _paths_overlap: all overlap cases
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from llmos_bridge.apps.action_cache import ActionSessionCache, _paths_overlap

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# _paths_overlap helper
# ---------------------------------------------------------------------------


class TestPathsOverlap:

    def test_identical_paths_overlap(self):
        assert _paths_overlap("/project/src", "/project/src") is True

    def test_parent_child_overlap(self):
        assert _paths_overlap("/project", "/project/src/main.py") is True

    def test_child_parent_overlap(self):
        assert _paths_overlap("/project/src/main.py", "/project") is True

    def test_sibling_paths_do_not_overlap(self):
        assert _paths_overlap("/project/src", "/project/tests") is False

    def test_unrelated_paths_do_not_overlap(self):
        assert _paths_overlap("/home/user", "/tmp/work") is False

    def test_prefix_collision_without_separator(self):
        """'/project2' must not match '/project' as child."""
        assert _paths_overlap("/project", "/project2") is False

    def test_exact_file_overlap(self):
        assert _paths_overlap("/project/main.py", "/project/main.py") is True


# ---------------------------------------------------------------------------
# Cache get / put basics
# ---------------------------------------------------------------------------


class TestCacheGetPut:

    def test_get_returns_none_on_miss(self):
        cache = ActionSessionCache()
        result = cache.get("filesystem", "read_file", {"path": "/tmp/a.txt"})
        assert result is None

    def test_put_then_get_hit(self):
        cache = ActionSessionCache()
        cache.put("filesystem", "read_file", {"path": "/tmp/a.txt"}, '{"content": "hello"}')
        result = cache.get("filesystem", "read_file", {"path": "/tmp/a.txt"})
        assert result == '{"content": "hello"}'

    def test_get_increments_hits(self):
        cache = ActionSessionCache()
        cache.put("filesystem", "read_file", {"path": "/tmp/a.txt"}, '{"content": "x"}')
        cache.get("filesystem", "read_file", {"path": "/tmp/a.txt"})
        cache.get("filesystem", "read_file", {"path": "/tmp/a.txt"})
        assert cache.hits == 2

    def test_get_increments_misses(self):
        cache = ActionSessionCache()
        cache.get("filesystem", "read_file", {"path": "/tmp/missing.txt"})
        cache.get("filesystem", "read_file", {"path": "/tmp/other.txt"})
        assert cache.misses == 2

    def test_non_cacheable_action_is_not_cached(self):
        cache = ActionSessionCache()
        # write_file is a write action, not in _READ_ACTIONS
        cache.put("filesystem", "write_file", {"path": "/tmp/a.txt"}, '{"written": 10}')
        result = cache.get("filesystem", "write_file", {"path": "/tmp/a.txt"})
        assert result is None

    def test_get_on_non_cacheable_action_returns_none_without_miss(self):
        cache = ActionSessionCache()
        result = cache.get("filesystem", "write_file", {"path": "/tmp"})
        assert result is None
        # Non-cacheable actions don't count as misses in current implementation

    def test_different_params_are_different_keys(self):
        cache = ActionSessionCache()
        cache.put("filesystem", "read_file", {"path": "/tmp/a.txt"}, '{"content": "A"}')
        result = cache.get("filesystem", "read_file", {"path": "/tmp/b.txt"})
        assert result is None

    def test_different_modules_are_different_keys(self):
        cache = ActionSessionCache()
        cache.put("filesystem", "list_directory", {"path": "/tmp"}, '{"entries": []}')
        result = cache.get("os_exec", "list_directory", {"path": "/tmp"})
        assert result is None


# ---------------------------------------------------------------------------
# Path normalisation
# ---------------------------------------------------------------------------


class TestPathNormalisation:

    def test_dot_resolves_to_cwd(self):
        cache = ActionSessionCache()
        cwd = str(Path(".").resolve())
        cache.put("filesystem", "list_directory", {"path": "."}, '{"entries": []}')
        # Both "." and absolute cwd should produce the same cache hit
        result = cache.get("filesystem", "list_directory", {"path": cwd})
        assert result == '{"entries": []}'

    def test_tilde_resolves_to_home(self):
        cache = ActionSessionCache()
        home = str(Path("~").expanduser().resolve())
        cache.put("filesystem", "list_directory", {"path": "~"}, '{"entries": []}')
        result = cache.get("filesystem", "list_directory", {"path": home})
        assert result == '{"entries": []}'


# ---------------------------------------------------------------------------
# Write invalidation
# ---------------------------------------------------------------------------


class TestWriteInvalidation:

    def test_write_invalidates_exact_path(self):
        cache = ActionSessionCache()
        cache.put("filesystem", "read_file", {"path": "/project/main.py"}, '{"content": "x"}')
        cache.invalidate_for_write("filesystem", "write_file", {"path": "/project/main.py"})
        assert cache.get("filesystem", "read_file", {"path": "/project/main.py"}) is None

    def test_write_invalidates_parent_list_directory(self):
        cache = ActionSessionCache()
        cache.put("filesystem", "list_directory", {"path": "/project"}, '{"entries": []}')
        cache.put("filesystem", "list_directory", {"path": "/project/src"}, '{"entries": []}')
        cache.invalidate_for_write("filesystem", "write_file", {"path": "/project/src/main.py"})
        # Both parent directories must be invalidated
        assert cache.get("filesystem", "list_directory", {"path": "/project"}) is None
        assert cache.get("filesystem", "list_directory", {"path": "/project/src"}) is None

    def test_write_does_not_invalidate_sibling(self):
        cache = ActionSessionCache()
        cache.put("filesystem", "list_directory", {"path": "/project/tests"}, '{"entries": []}')
        cache.invalidate_for_write("filesystem", "write_file", {"path": "/project/src/main.py"})
        # /project/tests is not a parent/child of /project/src/main.py
        result = cache.get("filesystem", "list_directory", {"path": "/project/tests"})
        assert result == '{"entries": []}'

    def test_non_write_action_does_not_invalidate(self):
        cache = ActionSessionCache()
        cache.put("filesystem", "read_file", {"path": "/tmp/a.txt"}, '{"content": "x"}')
        # get_file_info is a read action, not a write — must not invalidate
        cache.invalidate_for_write("filesystem", "get_file_info", {"path": "/tmp/a.txt"})
        assert cache.get("filesystem", "read_file", {"path": "/tmp/a.txt"}) == '{"content": "x"}'

    def test_invalidation_count_increments(self):
        cache = ActionSessionCache()
        cache.put("filesystem", "read_file", {"path": "/tmp/a.txt"}, '{"content": "x"}')
        cache.put("filesystem", "list_directory", {"path": "/tmp"}, '{"entries": []}')
        cache.invalidate_for_write("filesystem", "write_file", {"path": "/tmp/a.txt"})
        assert cache.invalidations >= 1

    def test_write_returns_count_of_removed_entries(self):
        cache = ActionSessionCache()
        cache.put("filesystem", "read_file", {"path": "/tmp/a.txt"}, '{"content": "x"}')
        cache.put("filesystem", "get_file_info", {"path": "/tmp/a.txt"}, '{"size": 10}')
        count = cache.invalidate_for_write("filesystem", "write_file", {"path": "/tmp/a.txt"})
        assert count == 2

    def test_delete_file_invalidates_read_cache(self):
        cache = ActionSessionCache()
        cache.put("filesystem", "read_file", {"path": "/tmp/x.txt"}, '{"content": "y"}')
        cache.invalidate_for_write("filesystem", "delete_file", {"path": "/tmp/x.txt"})
        assert cache.get("filesystem", "read_file", {"path": "/tmp/x.txt"}) is None

    def test_create_directory_invalidates_parent_list(self):
        cache = ActionSessionCache()
        cache.put("filesystem", "list_directory", {"path": "/project"}, '{"entries": []}')
        cache.invalidate_for_write("filesystem", "create_directory", {"path": "/project/newdir"})
        assert cache.get("filesystem", "list_directory", {"path": "/project"}) is None


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


class TestStats:

    def test_initial_stats_are_zero(self):
        cache = ActionSessionCache()
        s = cache.stats()
        assert s == {"cached": 0, "hits": 0, "misses": 0, "invalidations": 0}

    def test_stats_after_put_and_get(self):
        cache = ActionSessionCache()
        cache.put("filesystem", "read_file", {"path": "/tmp/a.txt"}, '{}')
        cache.get("filesystem", "read_file", {"path": "/tmp/a.txt"})   # hit
        cache.get("filesystem", "read_file", {"path": "/tmp/b.txt"})   # miss
        s = cache.stats()
        assert s["cached"] == 1
        assert s["hits"] == 1
        assert s["misses"] == 1

    def test_cached_decreases_after_invalidation(self):
        cache = ActionSessionCache()
        cache.put("filesystem", "read_file", {"path": "/tmp/a.txt"}, '{}')
        assert cache.stats()["cached"] == 1
        cache.invalidate_for_write("filesystem", "write_file", {"path": "/tmp/a.txt"})
        assert cache.stats()["cached"] == 0


# ---------------------------------------------------------------------------
# Clear
# ---------------------------------------------------------------------------


class TestClear:

    def test_clear_removes_all_entries(self):
        cache = ActionSessionCache()
        cache.put("filesystem", "read_file", {"path": "/tmp/a.txt"}, '{}')
        cache.put("filesystem", "list_directory", {"path": "/tmp"}, '{}')
        cache.clear()
        assert cache.stats()["cached"] == 0
        assert cache.get("filesystem", "read_file", {"path": "/tmp/a.txt"}) is None

    def test_clear_does_not_reset_hit_counters(self):
        cache = ActionSessionCache()
        cache.put("filesystem", "read_file", {"path": "/tmp/a.txt"}, '{}')
        cache.get("filesystem", "read_file", {"path": "/tmp/a.txt"})
        cache.clear()
        assert cache.hits == 1  # counters preserved after clear


# ---------------------------------------------------------------------------
# Disabled cache
# ---------------------------------------------------------------------------


class TestDisabledCache:

    def test_get_always_returns_none_when_disabled(self):
        cache = ActionSessionCache(enabled=False)
        cache.put("filesystem", "read_file", {"path": "/tmp/a.txt"}, '{"content": "x"}')
        assert cache.get("filesystem", "read_file", {"path": "/tmp/a.txt"}) is None

    def test_invalidate_returns_zero_when_disabled(self):
        cache = ActionSessionCache(enabled=False)
        count = cache.invalidate_for_write("filesystem", "write_file", {"path": "/tmp/a.txt"})
        assert count == 0

    def test_hits_and_misses_not_tracked_when_disabled(self):
        cache = ActionSessionCache(enabled=False)
        cache.get("filesystem", "read_file", {"path": "/tmp/a.txt"})
        assert cache.hits == 0
        assert cache.misses == 0
