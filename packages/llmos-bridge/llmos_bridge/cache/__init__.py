"""LLMOS Bridge — Two-level action cache.

L1: ActionSessionCache (in-memory dict, ~100ns, intra-session dedup)
L2: CacheClient (Redis/fakeredis, ~1-5µs embedded, cross-session sharing)

Usage in a module::

    from llmos_bridge.cache import cacheable, invalidates_cache

    class MyModule(BaseModule):

        @cacheable(ttl=300, key_params=["path"])
        async def _action_read_something(self, params: dict) -> dict:
            ...

        @invalidates_cache("read_something")
        async def _action_write_something(self, params: dict) -> dict:
            ...

The cache client auto-selects the backend:
- If ``REDIS_URL`` is set → real Redis (production scale)
- Otherwise → embedded fakeredis (zero config, pure Python)
"""

from llmos_bridge.cache.client import CacheClient, get_cache_client
from llmos_bridge.cache.decorators import (
    cacheable,
    collect_cache_metadata,
    invalidates_cache,
    make_cache_key,
)

__all__ = [
    "CacheClient",
    "get_cache_client",
    "cacheable",
    "invalidates_cache",
    "collect_cache_metadata",
    "make_cache_key",
]
