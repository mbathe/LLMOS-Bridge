"""Resource Manager — per-module concurrency limiter.

Controls how many concurrent actions can run against each module
(e.g., max 3 Excel operations at a time to avoid overwhelming
single-threaded office libraries).

Usage::

    rm = ResourceManager({"excel": 3, "word": 3, "api_http": 10})
    async with rm.acquire("excel"):
        result = await module.execute(action)
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator


class ResourceManager:
    """Per-module asyncio.Semaphore pool for concurrency control."""

    def __init__(
        self,
        limits: dict[str, int] | None = None,
        default_limit: int = 10,
    ) -> None:
        self._limits = limits or {}
        self._default = default_limit
        self._semaphores: dict[str, asyncio.Semaphore] = {}

    def _get_semaphore(self, module_id: str) -> asyncio.Semaphore:
        """Return (or lazily create) the semaphore for *module_id*."""
        if module_id not in self._semaphores:
            limit = self._limits.get(module_id, self._default)
            self._semaphores[module_id] = asyncio.Semaphore(limit)
        return self._semaphores[module_id]

    @asynccontextmanager
    async def acquire(self, module_id: str) -> AsyncIterator[None]:
        """Async context manager that blocks if the module is at capacity."""
        sem = self._get_semaphore(module_id)
        await sem.acquire()
        try:
            yield
        finally:
            sem.release()

    def status(self) -> dict[str, dict[str, int]]:
        """Return current semaphore state for monitoring."""
        result: dict[str, dict[str, int]] = {}
        for module_id, sem in self._semaphores.items():
            limit = self._limits.get(module_id, self._default)
            result[module_id] = {
                "limit": limit,
                "available": sem._value,
                "in_use": limit - sem._value,
            }
        return result
