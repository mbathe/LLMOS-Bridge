"""Unit tests — PlanExecutor with mocked dependencies."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from llmos_bridge.modules.filesystem import FilesystemModule
from llmos_bridge.modules.registry import ModuleRegistry
from llmos_bridge.orchestration.executor import PlanExecutor
from llmos_bridge.orchestration.state import PlanStateStore
from llmos_bridge.protocol.models import IMLAction, IMLPlan, OnErrorBehavior
from llmos_bridge.security.audit import AuditLogger
from llmos_bridge.security.guard import PermissionGuard
from llmos_bridge.security.profiles import PermissionProfile, get_profile_config


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def make_plan(
    actions: list[dict[str, Any]],
    plan_id: str = "test-plan",
) -> IMLPlan:
    return IMLPlan(
        plan_id=plan_id,
        description="Unit test plan",
        actions=[IMLAction(**a) for a in actions],
    )


@pytest_asyncio.fixture
async def state_store(tmp_path: Path):
    store = PlanStateStore(tmp_path / "state.db")
    await store.init()
    yield store
    await store.close()


@pytest.fixture
def registry() -> ModuleRegistry:
    reg = ModuleRegistry()
    reg.register(FilesystemModule)
    return reg


@pytest.fixture
def guard() -> PermissionGuard:
    profile_config = get_profile_config(PermissionProfile.UNRESTRICTED)
    return PermissionGuard(profile=profile_config)


@pytest.fixture
def audit_logger() -> AuditLogger:
    bus = MagicMock()
    bus.emit = AsyncMock()
    return AuditLogger(bus=bus)


@pytest.fixture
def executor(registry, guard, state_store, audit_logger) -> PlanExecutor:
    return PlanExecutor(
        module_registry=registry,
        guard=guard,
        state_store=state_store,
        audit_logger=audit_logger,
    )


# ---------------------------------------------------------------------------
# Basic execution
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPlanExecutorBasic:
    async def test_simple_plan_completes(
        self, executor: PlanExecutor, tmp_path: Path
    ) -> None:
        out = tmp_path / "result.txt"
        plan = make_plan(
            [
                {
                    "id": "write1",
                    "module": "filesystem",
                    "action": "write_file",
                    "params": {"path": str(out), "content": "hello"},
                }
            ]
        )
        state = await executor.run(plan)
        from llmos_bridge.protocol.models import PlanStatus

        assert state.plan_status == PlanStatus.COMPLETED
        assert out.read_text() == "hello"

    async def test_plan_with_dependency(
        self, executor: PlanExecutor, tmp_path: Path
    ) -> None:
        src = tmp_path / "src.txt"
        src.write_text("data")
        dst = tmp_path / "dst.txt"
        plan = make_plan(
            [
                {
                    "id": "read1",
                    "module": "filesystem",
                    "action": "read_file",
                    "params": {"path": str(src)},
                },
                {
                    "id": "write1",
                    "module": "filesystem",
                    "action": "write_file",
                    "params": {"path": str(dst), "content": "{{result.read1.content}}"},
                    "depends_on": ["read1"],
                },
            ]
        )
        state = await executor.run(plan)
        from llmos_bridge.protocol.models import PlanStatus

        assert state.plan_status == PlanStatus.COMPLETED
        assert dst.read_text() == "data"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPlanExecutorErrors:
    async def test_plan_fails_on_action_error(
        self, executor: PlanExecutor, tmp_path: Path
    ) -> None:
        plan = make_plan(
            [
                {
                    "id": "bad1",
                    "module": "filesystem",
                    "action": "read_file",
                    "params": {"path": str(tmp_path / "ghost.txt")},
                    "on_error": "abort",
                }
            ]
        )
        state = await executor.run(plan)
        from llmos_bridge.protocol.models import ActionStatus, PlanStatus

        assert state.plan_status == PlanStatus.FAILED
        assert state.get_action("bad1").status == ActionStatus.FAILED

    async def test_cascade_skip_on_abort(
        self, executor: PlanExecutor, tmp_path: Path
    ) -> None:
        out = tmp_path / "never.txt"
        plan = make_plan(
            [
                {
                    "id": "fail1",
                    "module": "filesystem",
                    "action": "read_file",
                    "params": {"path": str(tmp_path / "ghost.txt")},
                    "on_error": "abort",
                },
                {
                    "id": "dep1",
                    "module": "filesystem",
                    "action": "write_file",
                    "params": {"path": str(out), "content": "should not run"},
                    "depends_on": ["fail1"],
                },
            ]
        )
        state = await executor.run(plan)
        from llmos_bridge.protocol.models import ActionStatus, PlanStatus

        assert state.plan_status == PlanStatus.FAILED
        assert state.get_action("fail1").status == ActionStatus.FAILED
        # Dependent action should be skipped
        assert state.get_action("dep1").status == ActionStatus.SKIPPED
        assert not out.exists()

    async def test_unknown_module_fails_action(
        self, executor: PlanExecutor, tmp_path: Path
    ) -> None:
        plan = make_plan(
            [
                {
                    "id": "action1",
                    "module": "no_such_module",
                    "action": "do_stuff",
                    "params": {},
                }
            ]
        )
        state = await executor.run(plan)
        from llmos_bridge.protocol.models import ActionStatus, PlanStatus

        assert state.plan_status == PlanStatus.FAILED
        assert state.get_action("action1").status == ActionStatus.FAILED

    async def test_on_error_continue(
        self, executor: PlanExecutor, tmp_path: Path
    ) -> None:
        out = tmp_path / "second.txt"
        plan = make_plan(
            [
                {
                    "id": "fail1",
                    "module": "filesystem",
                    "action": "read_file",
                    "params": {"path": str(tmp_path / "ghost.txt")},
                    "on_error": "continue",
                },
                {
                    "id": "write1",
                    "module": "filesystem",
                    "action": "write_file",
                    "params": {"path": str(out), "content": "ran after failure"},
                    # No dependency on fail1 — runs in parallel wave
                },
            ]
        )
        state = await executor.run(plan)
        from llmos_bridge.protocol.models import ActionStatus

        # fail1 failed but write1 should have completed (no dependency)
        assert state.get_action("write1").status == ActionStatus.COMPLETED
        assert out.exists()

    async def test_plan_with_security_blocked_action(
        self, tmp_path: Path, registry, audit_logger, state_store
    ) -> None:
        # Use READONLY profile that denies write_file
        readonly_guard = PermissionGuard(profile=get_profile_config(PermissionProfile.READONLY))
        readonly_executor = PlanExecutor(
            module_registry=registry,
            guard=readonly_guard,
            state_store=state_store,
            audit_logger=audit_logger,
        )
        plan = make_plan(
            [
                {
                    "id": "blocked",
                    "module": "filesystem",
                    "action": "write_file",
                    "params": {"path": str(tmp_path / "f.txt"), "content": "x"},
                }
            ]
        )
        state = await readonly_executor.run(plan)
        from llmos_bridge.protocol.models import PlanStatus

        assert state.plan_status == PlanStatus.FAILED


# ---------------------------------------------------------------------------
# Retry
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPlanExecutorRetry:
    async def test_retry_succeeds_on_second_attempt(
        self, registry: ModuleRegistry, guard: PermissionGuard, state_store, audit_logger, tmp_path: Path
    ) -> None:
        """Action fails on attempt 1, succeeds on attempt 2 via retry."""
        call_count = 0
        original_execute = FilesystemModule.execute

        async def patched_execute(self_module, action, params):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("transient failure")
            return await original_execute(self_module, action, params)

        out = tmp_path / "retry_result.txt"
        plan = make_plan(
            [
                {
                    "id": "write1",
                    "module": "filesystem",
                    "action": "write_file",
                    "params": {"path": str(out), "content": "retried"},
                    "on_error": "retry",
                    "retry": {"max_attempts": 3, "delay_seconds": 0.1},
                }
            ]
        )
        with patch.object(FilesystemModule, "execute", patched_execute):
            executor = PlanExecutor(
                module_registry=registry,
                guard=guard,
                state_store=state_store,
                audit_logger=audit_logger,
            )
            state = await executor.run(plan)

        from llmos_bridge.protocol.models import ActionStatus, PlanStatus
        assert state.plan_status == PlanStatus.COMPLETED
        assert state.get_action("write1").status == ActionStatus.COMPLETED
        assert call_count == 2

    async def test_retry_exhausted_fails_action(
        self, registry: ModuleRegistry, guard: PermissionGuard, state_store, audit_logger
    ) -> None:
        """Action always fails — exhausts retries and marks as FAILED."""
        async def always_fail(self_module, action, params):
            raise RuntimeError("always fails")

        plan = make_plan(
            [
                {
                    "id": "fail1",
                    "module": "filesystem",
                    "action": "read_file",
                    "params": {"path": "/nonexistent/path.txt"},
                    "on_error": "retry",
                    "retry": {"max_attempts": 2, "delay_seconds": 0.1},
                }
            ]
        )
        with patch.object(FilesystemModule, "execute", always_fail):
            executor = PlanExecutor(
                module_registry=registry,
                guard=guard,
                state_store=state_store,
                audit_logger=audit_logger,
            )
            state = await executor.run(plan)

        from llmos_bridge.protocol.models import ActionStatus, PlanStatus
        assert state.plan_status == PlanStatus.FAILED
        assert state.get_action("fail1").status == ActionStatus.FAILED


# ---------------------------------------------------------------------------
# Memory read/write
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPlanExecutorMemory:
    async def test_memory_write_key_persists_result(
        self, registry: ModuleRegistry, guard: PermissionGuard, state_store, audit_logger, tmp_path: Path
    ) -> None:
        from llmos_bridge.memory.store import KeyValueStore
        kv_store = KeyValueStore(tmp_path / "kv_exec.db")
        await kv_store.init()

        out = tmp_path / "mem_result.txt"
        plan = make_plan(
            [
                {
                    "id": "write1",
                    "module": "filesystem",
                    "action": "write_file",
                    "params": {"path": str(out), "content": "stored"},
                    "memory": {"write_key": "last_write_result"},
                }
            ]
        )
        executor = PlanExecutor(
            module_registry=registry,
            guard=guard,
            state_store=state_store,
            audit_logger=audit_logger,
            kv_store=kv_store,
        )
        state = await executor.run(plan)

        from llmos_bridge.protocol.models import PlanStatus
        assert state.plan_status == PlanStatus.COMPLETED
        stored = await kv_store.get("last_write_result")
        assert stored is not None

        await kv_store.close()

    async def test_memory_read_keys_loaded_before_action(
        self, registry: ModuleRegistry, guard: PermissionGuard, state_store, audit_logger, tmp_path: Path
    ) -> None:
        from llmos_bridge.memory.store import KeyValueStore
        kv_store = KeyValueStore(tmp_path / "kv_read.db")
        await kv_store.init()
        await kv_store.set("stored_path", str(tmp_path / "hello.txt"))
        (tmp_path / "hello.txt").write_text("mem content")

        plan = make_plan(
            [
                {
                    "id": "read1",
                    "module": "filesystem",
                    "action": "read_file",
                    "params": {"path": str(tmp_path / "hello.txt")},
                    "memory": {"read_keys": ["stored_path"]},
                }
            ]
        )
        executor = PlanExecutor(
            module_registry=registry,
            guard=guard,
            state_store=state_store,
            audit_logger=audit_logger,
            kv_store=kv_store,
        )
        state = await executor.run(plan)

        from llmos_bridge.protocol.models import PlanStatus
        assert state.plan_status == PlanStatus.COMPLETED
        await kv_store.close()


# ---------------------------------------------------------------------------
# Module version requirements
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPlanExecutorModuleRequirements:
    async def test_version_requirement_satisfied(
        self, executor: PlanExecutor, tmp_path: Path
    ) -> None:
        out = tmp_path / "compat_result.txt"
        plan = make_plan(
            [
                {
                    "id": "write1",
                    "module": "filesystem",
                    "action": "write_file",
                    "params": {"path": str(out), "content": "compat"},
                }
            ]
        )
        plan.module_requirements = {"filesystem": ">=1.0.0"}
        state = await executor.run(plan)
        from llmos_bridge.protocol.models import PlanStatus
        assert state.plan_status == PlanStatus.COMPLETED

    async def test_version_requirement_not_satisfied_fails_plan(
        self, executor: PlanExecutor, tmp_path: Path
    ) -> None:
        out = tmp_path / "compat_fail.txt"
        plan = make_plan(
            [
                {
                    "id": "write1",
                    "module": "filesystem",
                    "action": "write_file",
                    "params": {"path": str(out), "content": "compat"},
                }
            ]
        )
        plan.module_requirements = {"filesystem": ">=99.0.0"}
        state = await executor.run(plan)
        from llmos_bridge.protocol.models import PlanStatus
        assert state.plan_status == PlanStatus.FAILED


# ---------------------------------------------------------------------------
# Approval gate
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPlanExecutorApproval:
    async def test_approval_gate_approves_action(
        self, registry: ModuleRegistry, state_store, audit_logger, tmp_path: Path
    ) -> None:
        """When approval gate receives APPROVE, the action runs to completion."""
        from llmos_bridge.orchestration.approval import (
            ApprovalDecision,
            ApprovalGate,
            ApprovalResponse,
        )
        from llmos_bridge.security.profiles import PermissionProfile, get_profile_config

        profile = get_profile_config(PermissionProfile.LOCAL_WORKER)
        approval_guard = PermissionGuard(
            profile=profile,
            require_approval_for=["filesystem.write_file"],
        )
        gate = ApprovalGate(default_timeout=5.0)

        async def auto_approve_after_delay():
            """Background task: wait for a pending request, then approve."""
            for _ in range(50):
                pending = gate.get_pending()
                if pending:
                    req = pending[0]
                    gate.submit_decision(
                        req.plan_id,
                        req.action_id,
                        ApprovalResponse(decision=ApprovalDecision.APPROVE),
                    )
                    return
                await asyncio.sleep(0.05)

        out = tmp_path / "approved.txt"
        plan = make_plan(
            [
                {
                    "id": "write1",
                    "module": "filesystem",
                    "action": "write_file",
                    "params": {"path": str(out), "content": "approved"},
                }
            ]
        )
        executor = PlanExecutor(
            module_registry=registry,
            guard=approval_guard,
            state_store=state_store,
            audit_logger=audit_logger,
            approval_gate=gate,
        )
        # Run executor and auto-approve concurrently.
        state, _ = await asyncio.gather(
            executor.run(plan),
            auto_approve_after_delay(),
        )
        from llmos_bridge.protocol.models import ActionStatus, PlanStatus

        assert state.plan_status == PlanStatus.COMPLETED
        assert state.get_action("write1").status == ActionStatus.COMPLETED
        assert out.read_text() == "approved"

    async def test_approval_gate_rejects_action(
        self, registry: ModuleRegistry, state_store, audit_logger, tmp_path: Path
    ) -> None:
        """When approval gate receives REJECT, the action fails."""
        from llmos_bridge.orchestration.approval import (
            ApprovalDecision,
            ApprovalGate,
            ApprovalResponse,
        )
        from llmos_bridge.security.profiles import PermissionProfile, get_profile_config

        profile = get_profile_config(PermissionProfile.LOCAL_WORKER)
        approval_guard = PermissionGuard(
            profile=profile,
            require_approval_for=["filesystem.write_file"],
        )
        gate = ApprovalGate(default_timeout=5.0)

        async def auto_reject_after_delay():
            """Background task: wait for a pending request, then reject."""
            for _ in range(50):
                pending = gate.get_pending()
                if pending:
                    req = pending[0]
                    gate.submit_decision(
                        req.plan_id,
                        req.action_id,
                        ApprovalResponse(
                            decision=ApprovalDecision.REJECT,
                            reason="User rejected",
                        ),
                    )
                    return
                await asyncio.sleep(0.05)

        out = tmp_path / "rejected.txt"
        plan = make_plan(
            [
                {
                    "id": "write1",
                    "module": "filesystem",
                    "action": "write_file",
                    "params": {"path": str(out), "content": "rejected"},
                }
            ]
        )
        executor = PlanExecutor(
            module_registry=registry,
            guard=approval_guard,
            state_store=state_store,
            audit_logger=audit_logger,
            approval_gate=gate,
        )
        state, _ = await asyncio.gather(
            executor.run(plan),
            auto_reject_after_delay(),
        )
        from llmos_bridge.protocol.models import ActionStatus, PlanStatus

        assert state.plan_status == PlanStatus.FAILED
        assert state.get_action("write1").status == ActionStatus.FAILED
        assert not out.exists()

    async def test_approval_gate_skip_action(
        self, registry: ModuleRegistry, state_store, audit_logger, tmp_path: Path
    ) -> None:
        """When approval gate receives SKIP, the action is skipped."""
        from llmos_bridge.orchestration.approval import (
            ApprovalDecision,
            ApprovalGate,
            ApprovalResponse,
        )
        from llmos_bridge.security.profiles import PermissionProfile, get_profile_config

        profile = get_profile_config(PermissionProfile.LOCAL_WORKER)
        approval_guard = PermissionGuard(
            profile=profile,
            require_approval_for=["filesystem.write_file"],
        )
        gate = ApprovalGate(default_timeout=5.0)

        async def auto_skip_after_delay():
            for _ in range(50):
                pending = gate.get_pending()
                if pending:
                    req = pending[0]
                    gate.submit_decision(
                        req.plan_id,
                        req.action_id,
                        ApprovalResponse(
                            decision=ApprovalDecision.SKIP,
                            reason="Not needed",
                        ),
                    )
                    return
                await asyncio.sleep(0.05)

        out = tmp_path / "skipped.txt"
        plan = make_plan(
            [
                {
                    "id": "write1",
                    "module": "filesystem",
                    "action": "write_file",
                    "params": {"path": str(out), "content": "skipped"},
                }
            ]
        )
        executor = PlanExecutor(
            module_registry=registry,
            guard=approval_guard,
            state_store=state_store,
            audit_logger=audit_logger,
            approval_gate=gate,
        )
        state, _ = await asyncio.gather(
            executor.run(plan),
            auto_skip_after_delay(),
        )
        from llmos_bridge.protocol.models import ActionStatus

        assert state.get_action("write1").status == ActionStatus.SKIPPED
        assert not out.exists()

    async def test_no_approval_gate_fails_action(
        self, registry: ModuleRegistry, state_store, audit_logger, tmp_path: Path
    ) -> None:
        """When no approval gate is configured, action requiring approval fails."""
        from llmos_bridge.security.profiles import PermissionProfile, get_profile_config

        profile = get_profile_config(PermissionProfile.LOCAL_WORKER)
        approval_guard = PermissionGuard(
            profile=profile,
            require_approval_for=["filesystem.write_file"],
        )
        plan = make_plan(
            [
                {
                    "id": "write1",
                    "module": "filesystem",
                    "action": "write_file",
                    "params": {"path": str(tmp_path / "f.txt"), "content": "x"},
                }
            ]
        )
        executor = PlanExecutor(
            module_registry=registry,
            guard=approval_guard,
            state_store=state_store,
            audit_logger=audit_logger,
            # No approval_gate — should fail
        )
        state = await executor.run(plan)
        from llmos_bridge.protocol.models import ActionStatus, PlanStatus

        assert state.plan_status == PlanStatus.FAILED
        assert state.get_action("write1").status == ActionStatus.FAILED
