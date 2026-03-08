"""Cache decorators for LLMOS Bridge module actions.

Two decorators for module authors:

``@cacheable`` — mark an action's output as cacheable::

    @cacheable(ttl=300, key_params=["path"])
    async def _action_read_file(self, params: dict) -> dict:
        ...

``@invalidates_cache`` — mark an action as invalidating cached results::

    @invalidates_cache("read_file", "list_directory", "get_file_info")
    async def _action_write_file(self, params: dict) -> dict:
        ...

Both decorators store metadata on the function object under ``_cache_meta``.
:class:`~llmos_bridge.modules.base.BaseModule` reads this metadata in
``execute()`` and applies L2 cache logic automatically.

Runtime bypass
--------------
Any caller can add ``"_no_cache": true`` to the *params* dict of a cacheable
action to skip the L2 cache read for that specific call (cache-refresh
semantics: the action still executes and its fresh result is stored in the
cache afterwards).  ``execute()`` strips this key before building the cache
key or dispatching to the handler, so action implementations never see it.

Stacking with security decorators
----------------------------------
The ``_cache_meta`` attribute is copied through decorator stacks automatically
because :func:`llmos_bridge.security.decorators._copy_metadata` includes it.
Always place ``@cacheable`` / ``@invalidates_cache`` *inside* (closer to the
function than) ``@requires_permission`` so the outermost wrapper still carries
the metadata::

    @requires_permission(...)      # outermost
    @cacheable(ttl=60)             # inner — metadata propagates outward
    async def _action_read_file(self, params: dict) -> dict:
        ...
"""

from __future__ import annotations

import functools
import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Single attribute name to avoid polluting the function namespace
_CACHE_META_ATTR = "_cache_meta"


# ---------------------------------------------------------------------------
# @cacheable
# ---------------------------------------------------------------------------


def cacheable(
    ttl: int = 300,
    key_params: list[str] | None = None,
    shared: bool = True,
    invalidated_by: list[str] | None = None,
) -> Callable:
    """Decorator — cache the output of a module action in the L2 Redis cache.

    Args:
        ttl:
            Time-to-live in seconds.  ``0`` = no expiry (lives until the
            daemon restarts or the key is explicitly deleted).  Default: 300s.
        key_params:
            Subset of parameter names to include in the cache key.
            ``None`` (default) = all parameters are used.
            Specify only the params that meaningfully affect the output to
            maximise hit rate (e.g. ``["path", "encoding"]`` for
            ``read_file``).
        shared:
            ``True`` (default) = use the L2 Redis/fakeredis cache (shared
            across sessions and, with a real Redis, across daemon instances).
            ``False`` = L1 in-session dict only (equivalent to existing
            ``ActionSessionCache`` behaviour — use when results must never
            leak across sessions).
        invalidated_by:
            List of action names (same module) whose execution should
            invalidate this entry.  Informational only — the actual
            invalidation is declared on the write side with
            ``@invalidates_cache``.

    Example::

        @requires_permission(Permission.FILESYSTEM_READ)
        @cacheable(ttl=60, key_params=["path", "encoding"])
        async def _action_read_file(self, params: dict) -> dict:
            ...
    """

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        async def wrapper(self: Any, params: dict) -> Any:
            return await fn(self, params)

        # Merge with any existing cache meta from inner decorators so that
        # @cacheable(outer) + @invalidates_cache(inner) both survive.
        existing: dict[str, Any] = dict(getattr(fn, _CACHE_META_ATTR, {}))
        meta: dict[str, Any] = {
            **existing,
            "cacheable": True,
            "ttl": ttl,
            "key_params": key_params,
            "shared": shared,
            "invalidated_by": invalidated_by or [],
        }
        setattr(wrapper, _CACHE_META_ATTR, meta)
        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# @invalidates_cache
# ---------------------------------------------------------------------------


def invalidates_cache(*action_names: str) -> Callable:
    """Decorator — mark an action as invalidating cached results of other actions.

    When this action executes successfully, all L2 cache entries for the
    listed action names (same module) are deleted.  Use ``"*"`` to invalidate
    ALL cached actions of this module.

    Args:
        action_names:
            One or more action names (without the ``_action_`` prefix) to
            invalidate, or ``"*"`` to invalidate everything in this module.

    Example::

        @requires_permission(Permission.FILESYSTEM_WRITE)
        @invalidates_cache("read_file", "list_directory", "get_file_info", "search_files")
        async def _action_write_file(self, params: dict) -> dict:
            ...
    """

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        async def wrapper(self: Any, params: dict) -> Any:
            return await fn(self, params)

        # Merge with any existing cache meta from inner decorators
        existing: dict[str, Any] = dict(getattr(fn, _CACHE_META_ATTR, {}))
        existing["invalidates"] = list(action_names)
        setattr(wrapper, _CACHE_META_ATTR, existing)
        _propagate_existing(fn, wrapper)
        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _propagate_existing(source: Any, target: Any) -> None:
    """Copy ``_cache_meta`` from *source* to *target* if not already set."""
    if not hasattr(target, _CACHE_META_ATTR):
        existing = getattr(source, _CACHE_META_ATTR, None)
        if existing is not None:
            setattr(target, _CACHE_META_ATTR, existing)


def collect_cache_metadata(fn: Any) -> dict[str, Any]:
    """Return the cache metadata dict stored on *fn*, or ``{}``."""
    return dict(getattr(fn, _CACHE_META_ATTR, {}))


def make_cache_key(
    module_id: str,
    action_name: str,
    params: dict[str, Any],
    key_params: list[str] | None = None,
) -> str:
    """Build a stable, collision-resistant Redis cache key.

    The key format is::

        llmos:cache:{module_id}:{action_name}:{sha256_prefix}

    Path parameters (``path``, ``source``, ``directory``, ``file_path``) are
    resolved to absolute paths before hashing so that ``path="."`` and
    ``path="/home/user/project"`` produce the same key when they refer to the
    same directory.

    Args:
        module_id:    Module identifier (e.g. ``"filesystem"``).
        action_name:  Action name (e.g. ``"read_file"``).
        params:       Raw parameters dict from the LLM tool call.
        key_params:   If set, only these param names are included in the key.

    Returns:
        A string like ``"llmos:cache:filesystem:read_file:a3f9d2c1b4e8f7a0"``.
    """
    subset = (
        {k: v for k, v in params.items() if k in key_params}
        if key_params is not None
        else dict(params)
    )

    # Normalise path-like params to absolute resolved paths
    _PATH_KEYS = {"path", "source", "directory", "file_path"}
    normalised: dict[str, Any] = {}
    for k, v in subset.items():
        if k in _PATH_KEYS and isinstance(v, str):
            try:
                normalised[k] = str(Path(v).expanduser().resolve())
            except Exception:
                normalised[k] = v
        else:
            normalised[k] = v

    key_data = json.dumps(normalised, sort_keys=True, default=str)
    key_hash = hashlib.sha256(key_data.encode()).hexdigest()[:16]
    return f"llmos:cache:{module_id}:{action_name}:{key_hash}"


def make_invalidation_patterns(module_id: str, action_names: tuple[str, ...]) -> list[str]:
    """Return Redis glob patterns to delete when invalidating *action_names*.

    Args:
        module_id:    Module identifier.
        action_names: Action names to invalidate, or ``("*",)`` for all.

    Returns:
        List of patterns like ``["llmos:cache:filesystem:read_file:*"]``.
    """
    if "*" in action_names:
        return [f"llmos:cache:{module_id}:*"]
    return [f"llmos:cache:{module_id}:{name}:*" for name in action_names]
