"""Unit tests for ResourceManager — per-module concurrency limiter."""

from __future__ import annotations

import asyncio

import pytest

from llmos_bridge.orchestration.resource_manager import ResourceManager


@pytest.fixture
def rm() -> ResourceManager:
    return ResourceManager(limits={"excel": 2, "word": 1}, default_limit=5)


class TestResourceManagerInit:
    """Test construction and configuration."""

    def test_default_limits(self) -> None:
        rm = ResourceManager()
        assert rm._default == 10

    def test_custom_limits(self, rm: ResourceManager) -> None:
        assert rm._limits == {"excel": 2, "word": 1}
        assert rm._default == 5

    def test_custom_default(self) -> None:
        rm = ResourceManager(default_limit=3)
        assert rm._default == 3


class TestAcquire:
    """Test the async context manager acquire."""

    @pytest.mark.asyncio
    async def test_acquire_and_release(self, rm: ResourceManager) -> None:
        async with rm.acquire("excel"):
            status = rm.status()
            assert status["excel"]["in_use"] == 1
            assert status["excel"]["available"] == 1

        # After release
        status = rm.status()
        assert status["excel"]["in_use"] == 0
        assert status["excel"]["available"] == 2

    @pytest.mark.asyncio
    async def test_acquire_uses_default_limit(self, rm: ResourceManager) -> None:
        async with rm.acquire("filesystem"):
            status = rm.status()
            assert status["filesystem"]["limit"] == 5

    @pytest.mark.asyncio
    async def test_semaphore_reuse(self, rm: ResourceManager) -> None:
        """Same module returns the same semaphore."""
        async with rm.acquire("excel"):
            pass
        async with rm.acquire("excel"):
            pass
        # Only one entry in _semaphores
        assert "excel" in rm._semaphores

    @pytest.mark.asyncio
    async def test_blocks_at_limit(self, rm: ResourceManager) -> None:
        """When at capacity, further acquires should block."""
        blocked = asyncio.Event()
        released = asyncio.Event()

        async def hold_slot() -> None:
            async with rm.acquire("word"):  # limit=1
                blocked.set()
                await released.wait()

        task = asyncio.create_task(hold_slot())
        await blocked.wait()

        # word is at capacity (1/1) — try to acquire with timeout
        acquired = False
        try:
            async with asyncio.timeout(0.1):
                async with rm.acquire("word"):
                    acquired = True
        except asyncio.TimeoutError:
            pass

        assert not acquired, "Should have been blocked at word limit=1"

        # Release the slot
        released.set()
        await task

    @pytest.mark.asyncio
    async def test_different_modules_independent(self, rm: ResourceManager) -> None:
        """Excel and word semaphores are independent."""
        async with rm.acquire("excel"):
            async with rm.acquire("word"):
                status = rm.status()
                assert status["excel"]["in_use"] == 1
                assert status["word"]["in_use"] == 1


class TestStatus:
    """Test the status() method."""

    @pytest.mark.asyncio
    async def test_empty_status(self) -> None:
        rm = ResourceManager()
        assert rm.status() == {}

    @pytest.mark.asyncio
    async def test_status_after_use(self, rm: ResourceManager) -> None:
        async with rm.acquire("excel"):
            status = rm.status()
            assert "excel" in status
            assert status["excel"]["limit"] == 2
            assert status["excel"]["in_use"] == 1
            assert status["excel"]["available"] == 1

    @pytest.mark.asyncio
    async def test_concurrent_status(self, rm: ResourceManager) -> None:
        async with rm.acquire("excel"):
            async with rm.acquire("excel"):
                status = rm.status()
                assert status["excel"]["in_use"] == 2
                assert status["excel"]["available"] == 0
