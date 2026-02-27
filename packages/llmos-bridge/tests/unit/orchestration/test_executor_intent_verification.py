"""Unit tests -- PlanExecutor integration with IntentVerifier.

Tests how the PlanExecutor handles different VerificationVerdict outcomes
from the IntentVerifier (Step 1.5 in the run() method).  All tests use a
FakeIntentVerifier that returns pre-configured VerificationResult objects
so no real LLM is needed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from llmos_bridge.modules.filesystem import FilesystemModule
from llmos_bridge.modules.registry import ModuleRegistry
from llmos_bridge.orchestration.executor import PlanExecutor
from llmos_bridge.orchestration.state import PlanStateStore
from llmos_bridge.protocol.models import IMLAction, IMLPlan, PlanStatus
from llmos_bridge.security.audit import AuditLogger
from llmos_bridge.security.guard import PermissionGuard
from llmos_bridge.security.intent_verifier import (
    ThreatDetail,
    ThreatType,
    VerificationResult,
    VerificationVerdict,
)
from llmos_bridge.security.profiles import PermissionProfile, get_profile_config


# ---------------------------------------------------------------------------
# Fake IntentVerifier
# ---------------------------------------------------------------------------


class FakeIntentVerifier:
    """Minimal IntentVerifier stub returning a pre-configured result.

    Attributes match the duck-typed interface that PlanExecutor expects:
      - ``enabled`` property
      - ``_strict`` attribute (accessed directly by executor)
      - ``verify_plan(plan)`` async method
    """

    def __init__(self, result: VerificationResult, strict: bool = False) -> None:
        self._result = result
        self._enabled = True
        self._strict = strict

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def verify_plan(self, plan: IMLPlan) -> VerificationResult:
        return self._result


class ExplodingIntentVerifier:
    """IntentVerifier stub that raises on verify_plan().

    Used to test the executor's exception handling around Step 1.5.
    """

    def __init__(self, strict: bool = False) -> None:
        self._enabled = True
        self._strict = strict

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def verify_plan(self, plan: IMLPlan) -> VerificationResult:
        raise RuntimeError("LLM service unavailable")


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_plan(
    actions: list[dict[str, Any]],
    plan_id: str = "intent-test-plan",
) -> IMLPlan:
    return IMLPlan(
        plan_id=plan_id,
        description="Intent verification test plan",
        actions=[IMLAction(**a) for a in actions],
    )


def _simple_write_action(tmp_path: Path) -> list[dict[str, Any]]:
    """Return a single write_file action targeting *tmp_path*."""
    return [
        {
            "id": "w1",
            "module": "filesystem",
            "action": "write_file",
            "params": {
                "path": str(tmp_path / "intent_out.txt"),
                "content": "verified",
            },
        }
    ]


@pytest_asyncio.fixture
async def state_store(tmp_path: Path) -> PlanStateStore:
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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExecutorIntentVerification:
    """Verify PlanExecutor behaviour for each VerificationVerdict path."""

    # 1. APPROVE -> plan proceeds normally
    async def test_executor_runs_plan_when_verified_approve(
        self,
        registry: ModuleRegistry,
        guard: PermissionGuard,
        state_store: PlanStateStore,
        audit_logger: AuditLogger,
        tmp_path: Path,
    ) -> None:
        verifier = FakeIntentVerifier(
            result=VerificationResult(
                verdict=VerificationVerdict.APPROVE,
                risk_level="low",
                reasoning="Plan is safe.",
            ),
        )
        executor = PlanExecutor(
            module_registry=registry,
            guard=guard,
            state_store=state_store,
            audit_logger=audit_logger,
            intent_verifier=verifier,
        )
        plan = _make_plan(_simple_write_action(tmp_path))
        state = await executor.run(plan)

        assert state.plan_status == PlanStatus.COMPLETED
        assert (tmp_path / "intent_out.txt").read_text() == "verified"

    # 2. REJECT -> plan FAILED, returned early
    async def test_executor_fails_plan_when_verified_reject(
        self,
        registry: ModuleRegistry,
        guard: PermissionGuard,
        state_store: PlanStateStore,
        audit_logger: AuditLogger,
        tmp_path: Path,
    ) -> None:
        verifier = FakeIntentVerifier(
            result=VerificationResult(
                verdict=VerificationVerdict.REJECT,
                risk_level="critical",
                reasoning="Data exfiltration pattern detected.",
                threats=[
                    ThreatDetail(
                        threat_type=ThreatType.DATA_EXFILTRATION,
                        severity="critical",
                        description="Read credentials then HTTP POST",
                        affected_action_ids=["w1"],
                    )
                ],
            ),
        )
        executor = PlanExecutor(
            module_registry=registry,
            guard=guard,
            state_store=state_store,
            audit_logger=audit_logger,
            intent_verifier=verifier,
        )
        plan = _make_plan(_simple_write_action(tmp_path))
        state = await executor.run(plan)

        assert state.plan_status == PlanStatus.FAILED
        # The action should never have run.
        assert not (tmp_path / "intent_out.txt").exists()

    # 3. WARN -> plan proceeds normally
    async def test_executor_warns_and_continues(
        self,
        registry: ModuleRegistry,
        guard: PermissionGuard,
        state_store: PlanStateStore,
        audit_logger: AuditLogger,
        tmp_path: Path,
    ) -> None:
        verifier = FakeIntentVerifier(
            result=VerificationResult(
                verdict=VerificationVerdict.WARN,
                risk_level="medium",
                reasoning="Minor concern: dynamic path parameter.",
            ),
        )
        executor = PlanExecutor(
            module_registry=registry,
            guard=guard,
            state_store=state_store,
            audit_logger=audit_logger,
            intent_verifier=verifier,
        )
        plan = _make_plan(_simple_write_action(tmp_path))
        state = await executor.run(plan)

        assert state.plan_status == PlanStatus.COMPLETED
        assert (tmp_path / "intent_out.txt").read_text() == "verified"

    # 4. CLARIFY + strict=True -> plan FAILED
    async def test_executor_clarify_strict_fails(
        self,
        registry: ModuleRegistry,
        guard: PermissionGuard,
        state_store: PlanStateStore,
        audit_logger: AuditLogger,
        tmp_path: Path,
    ) -> None:
        verifier = FakeIntentVerifier(
            result=VerificationResult(
                verdict=VerificationVerdict.CLARIFY,
                risk_level="medium",
                reasoning="Ambiguous intent.",
                clarification_needed="What is the purpose of this file write?",
            ),
            strict=True,
        )
        executor = PlanExecutor(
            module_registry=registry,
            guard=guard,
            state_store=state_store,
            audit_logger=audit_logger,
            intent_verifier=verifier,
        )
        plan = _make_plan(_simple_write_action(tmp_path))
        state = await executor.run(plan)

        assert state.plan_status == PlanStatus.FAILED
        assert not (tmp_path / "intent_out.txt").exists()

    # 5. CLARIFY + strict=False -> plan proceeds
    async def test_executor_clarify_permissive_continues(
        self,
        registry: ModuleRegistry,
        guard: PermissionGuard,
        state_store: PlanStateStore,
        audit_logger: AuditLogger,
        tmp_path: Path,
    ) -> None:
        verifier = FakeIntentVerifier(
            result=VerificationResult(
                verdict=VerificationVerdict.CLARIFY,
                risk_level="medium",
                reasoning="Ambiguous intent.",
                clarification_needed="What is the purpose of this file write?",
            ),
            strict=False,
        )
        executor = PlanExecutor(
            module_registry=registry,
            guard=guard,
            state_store=state_store,
            audit_logger=audit_logger,
            intent_verifier=verifier,
        )
        plan = _make_plan(_simple_write_action(tmp_path))
        state = await executor.run(plan)

        assert state.plan_status == PlanStatus.COMPLETED
        assert (tmp_path / "intent_out.txt").read_text() == "verified"

    # 6. No verifier (None) -> plan proceeds normally
    async def test_executor_skips_verification_when_no_verifier(
        self,
        registry: ModuleRegistry,
        guard: PermissionGuard,
        state_store: PlanStateStore,
        audit_logger: AuditLogger,
        tmp_path: Path,
    ) -> None:
        executor = PlanExecutor(
            module_registry=registry,
            guard=guard,
            state_store=state_store,
            audit_logger=audit_logger,
            intent_verifier=None,
        )
        plan = _make_plan(_simple_write_action(tmp_path))
        state = await executor.run(plan)

        assert state.plan_status == PlanStatus.COMPLETED
        assert (tmp_path / "intent_out.txt").read_text() == "verified"

    # 7. Verifier raises exception + strict=False -> plan continues
    async def test_executor_handles_verifier_exception_permissive(
        self,
        registry: ModuleRegistry,
        guard: PermissionGuard,
        state_store: PlanStateStore,
        audit_logger: AuditLogger,
        tmp_path: Path,
    ) -> None:
        verifier = ExplodingIntentVerifier(strict=False)
        executor = PlanExecutor(
            module_registry=registry,
            guard=guard,
            state_store=state_store,
            audit_logger=audit_logger,
            intent_verifier=verifier,
        )
        plan = _make_plan(_simple_write_action(tmp_path))
        state = await executor.run(plan)

        assert state.plan_status == PlanStatus.COMPLETED
        assert (tmp_path / "intent_out.txt").read_text() == "verified"

    # 8. Verifier raises exception + strict=True -> plan FAILED
    async def test_executor_handles_verifier_exception_strict(
        self,
        registry: ModuleRegistry,
        guard: PermissionGuard,
        state_store: PlanStateStore,
        audit_logger: AuditLogger,
        tmp_path: Path,
    ) -> None:
        verifier = ExplodingIntentVerifier(strict=True)
        executor = PlanExecutor(
            module_registry=registry,
            guard=guard,
            state_store=state_store,
            audit_logger=audit_logger,
            intent_verifier=verifier,
        )
        plan = _make_plan(_simple_write_action(tmp_path))
        state = await executor.run(plan)

        assert state.plan_status == PlanStatus.FAILED
        assert not (tmp_path / "intent_out.txt").exists()
