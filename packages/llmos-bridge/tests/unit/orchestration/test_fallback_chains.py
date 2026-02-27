"""Unit tests — Graceful Degradation (fallback chains in PlanExecutor)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from llmos_bridge.modules.filesystem import FilesystemModule
from llmos_bridge.modules.registry import ModuleRegistry
from llmos_bridge.orchestration.executor import PlanExecutor
from llmos_bridge.orchestration.state import PlanStateStore
from llmos_bridge.protocol.models import IMLAction, IMLPlan
from llmos_bridge.security.audit import AuditLogger
from llmos_bridge.security.guard import PermissionGuard
from llmos_bridge.security.profiles import PermissionProfile, get_profile_config


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def make_plan(
    actions: list[dict[str, Any]],
    plan_id: str = "fb-plan",
) -> IMLPlan:
    return IMLPlan(
        plan_id=plan_id,
        description="Fallback test plan",
        actions=[IMLAction(**a) for a in actions],
    )


@pytest_asyncio.fixture
async def state_store(tmp_path: Path):
    store = PlanStateStore(tmp_path / "state.db")
    await store.init()
    yield store
    await store.close()


@pytest.fixture
def audit_logger() -> AuditLogger:
    bus = MagicMock()
    bus.emit = AsyncMock()
    return AuditLogger(bus=bus)


@pytest.fixture
def guard() -> PermissionGuard:
    profile_config = get_profile_config(PermissionProfile.UNRESTRICTED)
    return PermissionGuard(profile=profile_config)


@pytest.fixture
def registry() -> ModuleRegistry:
    reg = ModuleRegistry()
    reg.register(FilesystemModule)
    return reg


# ---------------------------------------------------------------------------
# Tests — _dispatch_with_fallback
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFallbackChains:
    """Verify graceful degradation via fallback chains."""

    @pytest.mark.asyncio
    async def test_no_fallback_success(
        self, registry, guard, state_store, audit_logger, tmp_path: Path
    ) -> None:
        """Normal execution — no fallback needed."""
        f = tmp_path / "ok.txt"
        f.write_text("hello")

        executor = PlanExecutor(
            module_registry=registry,
            guard=guard,
            state_store=state_store,
            audit_logger=audit_logger,
            fallback_chains={},
        )
        plan = make_plan([{
            "id": "r1",
            "action": "read_file",
            "module": "filesystem",
            "params": {"path": str(f)},
        }])
        state = await executor.run(plan)
        assert state.plan_status.value == "completed"

    @pytest.mark.asyncio
    async def test_no_fallback_raises_on_failure(
        self, registry, guard, state_store, audit_logger
    ) -> None:
        """Without fallback chains, failures propagate normally."""
        executor = PlanExecutor(
            module_registry=registry,
            guard=guard,
            state_store=state_store,
            audit_logger=audit_logger,
            fallback_chains={},
        )
        plan = make_plan([{
            "id": "r1",
            "action": "read_file",
            "module": "filesystem",
            "params": {"path": "/does/not/exist/test.txt"},
        }])
        state = await executor.run(plan)
        assert state.plan_status.value == "failed"

    @pytest.mark.asyncio
    async def test_fallback_chains_stored(
        self, registry, guard, state_store, audit_logger
    ) -> None:
        """fallback_chains dict is stored correctly."""
        chains = {"excel": ["filesystem"], "word": ["filesystem"]}
        executor = PlanExecutor(
            module_registry=registry,
            guard=guard,
            state_store=state_store,
            audit_logger=audit_logger,
            fallback_chains=chains,
        )
        assert executor._fallback_chains == chains

    @pytest.mark.asyncio
    async def test_fallback_chains_default_empty(
        self, registry, guard, state_store, audit_logger
    ) -> None:
        """Without explicit fallback_chains, defaults to empty dict."""
        executor = PlanExecutor(
            module_registry=registry,
            guard=guard,
            state_store=state_store,
            audit_logger=audit_logger,
        )
        assert executor._fallback_chains == {}

    @pytest.mark.asyncio
    async def test_dispatch_with_fallback_uses_fallback_module(
        self, registry, guard, state_store, audit_logger, tmp_path: Path
    ) -> None:
        """When primary dispatch fails, a fallback module is tried.

        We mock _dispatch to fail, then verify the fallback path
        calls node.execute_action with the fallback module.
        """
        executor = PlanExecutor(
            module_registry=registry,
            guard=guard,
            state_store=state_store,
            audit_logger=audit_logger,
            fallback_chains={"broken_module": ["filesystem"]},
        )

        action = IMLAction(
            id="a1",
            action="read_file",
            module="broken_module",
            params={"path": str(tmp_path / "test.txt")},
        )
        (tmp_path / "test.txt").write_text("fallback content")

        # Mock _dispatch to fail (simulating broken primary module)
        original_dispatch = executor._dispatch
        call_count = 0

        async def mock_dispatch(act, params):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Primary module broken")
            return await original_dispatch(act, params)

        executor._dispatch = mock_dispatch

        # The fallback goes through node.execute_action directly,
        # not through _dispatch, so it should work.
        result = await executor._dispatch_with_fallback(action, {"path": str(tmp_path / "test.txt")})
        assert result is not None

    @pytest.mark.asyncio
    async def test_all_fallbacks_fail_raises_original(
        self, registry, guard, state_store, audit_logger
    ) -> None:
        """When all fallbacks also fail, the original error is raised."""
        executor = PlanExecutor(
            module_registry=registry,
            guard=guard,
            state_store=state_store,
            audit_logger=audit_logger,
            fallback_chains={"broken": ["also_broken"]},
        )

        action = IMLAction(
            id="a1",
            action="read_file",
            module="broken",
            params={"path": "/nonexistent"},
        )

        original_error = RuntimeError("Primary error")
        executor._dispatch = AsyncMock(side_effect=original_error)

        with pytest.raises(RuntimeError, match="Primary error"):
            await executor._dispatch_with_fallback(action, {"path": "/nonexistent"})


# ---------------------------------------------------------------------------
# Tests — fallback_chains in config
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFallbackConfig:
    """Verify fallback chain config in ModuleConfig."""

    def test_default_fallbacks(self) -> None:
        from llmos_bridge.config import ModuleConfig

        cfg = ModuleConfig()
        assert "excel" in cfg.fallbacks
        assert cfg.fallbacks["excel"] == ["filesystem"]

    def test_custom_fallbacks(self) -> None:
        from llmos_bridge.config import ModuleConfig

        cfg = ModuleConfig(fallbacks={"api_http": ["filesystem"]})
        assert cfg.fallbacks == {"api_http": ["filesystem"]}

    def test_empty_fallbacks(self) -> None:
        from llmos_bridge.config import ModuleConfig

        cfg = ModuleConfig(fallbacks={})
        assert cfg.fallbacks == {}
