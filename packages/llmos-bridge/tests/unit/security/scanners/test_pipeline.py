"""Unit tests — SecurityPipeline orchestrator."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from llmos_bridge.protocol.models import IMLAction, IMLPlan
from llmos_bridge.security.scanners.base import (
    InputScanner,
    ScanContext,
    ScanResult,
    ScanVerdict,
)
from llmos_bridge.security.scanners.pipeline import PipelineResult, SecurityPipeline
from llmos_bridge.security.scanners.registry import ScannerRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FixedScanner(InputScanner):
    """Scanner that returns a fixed verdict."""

    def __init__(
        self, sid: str, verdict: ScanVerdict, risk: float = 0.0,
        priority: int = 10, threat_types: list[str] | None = None,
    ) -> None:
        self.scanner_id = sid  # type: ignore[misc]
        self.priority = priority  # type: ignore[misc]
        self._verdict = verdict
        self._risk = risk
        self._threat_types = threat_types or []

    async def scan(self, text: str, context: ScanContext | None = None) -> ScanResult:
        return ScanResult(
            scanner_id=self.scanner_id,
            verdict=self._verdict,
            risk_score=self._risk,
            threat_types=self._threat_types,
        )


class _ErrorScanner(InputScanner):
    scanner_id = "error"  # type: ignore[assignment]
    priority = 10  # type: ignore[assignment]

    async def scan(self, text: str, context: ScanContext | None = None) -> ScanResult:
        raise RuntimeError("boom")


def _plan(description: str = "test plan") -> IMLPlan:
    return IMLPlan(
        plan_id="p-test",
        description=description,
        actions=[
            IMLAction(id="a1", action="read_file", module="filesystem", params={"path": "/tmp/x"})
        ],
    )


def _registry(*scanners: InputScanner) -> ScannerRegistry:
    reg = ScannerRegistry()
    for s in scanners:
        reg.register(s)
    return reg


def _pipeline(
    *scanners: InputScanner,
    fail_fast: bool = True,
    reject_threshold: float = 0.7,
    warn_threshold: float = 0.3,
    audit: bool = False,
) -> SecurityPipeline:
    audit_logger = AsyncMock() if audit else None
    return SecurityPipeline(
        registry=_registry(*scanners),
        audit_logger=audit_logger,
        fail_fast=fail_fast,
        reject_threshold=reject_threshold,
        warn_threshold=warn_threshold,
    )


# ---------------------------------------------------------------------------
# PipelineResult
# ---------------------------------------------------------------------------


class TestPipelineResult:
    def test_defaults(self) -> None:
        r = PipelineResult()
        assert r.allowed is True
        assert r.aggregate_verdict == ScanVerdict.ALLOW
        assert r.max_risk_score == 0.0
        assert r.scanner_results == []
        assert r.short_circuited is False
        assert r.total_duration_ms == 0.0

    def test_to_dict(self) -> None:
        sr = ScanResult(scanner_id="heuristic", verdict=ScanVerdict.WARN, risk_score=0.5)
        r = PipelineResult(
            allowed=True,
            aggregate_verdict=ScanVerdict.WARN,
            max_risk_score=0.5,
            scanner_results=[sr],
            short_circuited=False,
            total_duration_ms=1.23,
        )
        d = r.to_dict()
        assert d["allowed"] is True
        assert d["aggregate_verdict"] == "warn"
        assert d["max_risk_score"] == 0.5
        assert len(d["scanner_results"]) == 1
        assert d["total_duration_ms"] == 1.23


# ---------------------------------------------------------------------------
# Pipeline — enabled/disabled
# ---------------------------------------------------------------------------


class TestPipelineEnabled:
    @pytest.mark.asyncio
    async def test_disabled_pipeline_allows_everything(self) -> None:
        p = SecurityPipeline(
            registry=_registry(_FixedScanner("s", ScanVerdict.REJECT, 1.0)),
            enabled=False,
        )
        r = await p.scan_input(_plan())
        assert r.allowed is True

    def test_enabled_property(self) -> None:
        p = _pipeline()
        assert p.enabled is True
        p.enabled = False
        assert p.enabled is False


# ---------------------------------------------------------------------------
# Pipeline — all ALLOW
# ---------------------------------------------------------------------------


class TestPipelineAllAllow:
    @pytest.mark.asyncio
    async def test_all_allow(self) -> None:
        p = _pipeline(
            _FixedScanner("s1", ScanVerdict.ALLOW, 0.0, priority=10),
            _FixedScanner("s2", ScanVerdict.ALLOW, 0.0, priority=20),
        )
        r = await p.scan_input(_plan())
        assert r.allowed is True
        assert r.aggregate_verdict == ScanVerdict.ALLOW
        assert len(r.scanner_results) == 2
        assert r.short_circuited is False

    @pytest.mark.asyncio
    async def test_empty_registry_allows(self) -> None:
        p = _pipeline()
        r = await p.scan_input(_plan())
        assert r.allowed is True


# ---------------------------------------------------------------------------
# Pipeline — WARN aggregation
# ---------------------------------------------------------------------------


class TestPipelineWarn:
    @pytest.mark.asyncio
    async def test_warn_propagates(self) -> None:
        p = _pipeline(
            _FixedScanner("s1", ScanVerdict.ALLOW, 0.0, priority=10),
            _FixedScanner("s2", ScanVerdict.WARN, 0.4, priority=20),
        )
        r = await p.scan_input(_plan())
        assert r.allowed is True
        assert r.aggregate_verdict == ScanVerdict.WARN

    @pytest.mark.asyncio
    async def test_warn_below_reject_threshold_allows(self) -> None:
        p = _pipeline(
            _FixedScanner("s1", ScanVerdict.WARN, 0.5, priority=10),
        )
        r = await p.scan_input(_plan())
        assert r.allowed is True


# ---------------------------------------------------------------------------
# Pipeline — REJECT
# ---------------------------------------------------------------------------


class TestPipelineReject:
    @pytest.mark.asyncio
    async def test_reject_blocks(self) -> None:
        p = _pipeline(
            _FixedScanner("s1", ScanVerdict.REJECT, 0.9, priority=10),
        )
        r = await p.scan_input(_plan())
        assert r.allowed is False
        assert r.aggregate_verdict == ScanVerdict.REJECT

    @pytest.mark.asyncio
    async def test_reject_overrides_allow(self) -> None:
        p = _pipeline(
            _FixedScanner("s1", ScanVerdict.ALLOW, 0.0, priority=10),
            _FixedScanner("s2", ScanVerdict.REJECT, 0.9, priority=20),
            fail_fast=False,
        )
        r = await p.scan_input(_plan())
        assert r.allowed is False
        assert r.aggregate_verdict == ScanVerdict.REJECT


# ---------------------------------------------------------------------------
# Pipeline — fail_fast short-circuit
# ---------------------------------------------------------------------------


class TestPipelineFailFast:
    @pytest.mark.asyncio
    async def test_fail_fast_stops_on_reject(self) -> None:
        p = _pipeline(
            _FixedScanner("s1", ScanVerdict.REJECT, 0.9, priority=10),
            _FixedScanner("s2", ScanVerdict.ALLOW, 0.0, priority=20),
            fail_fast=True,
        )
        r = await p.scan_input(_plan())
        assert r.allowed is False
        assert r.short_circuited is True
        # Only first scanner ran
        assert len(r.scanner_results) == 1

    @pytest.mark.asyncio
    async def test_no_fail_fast_runs_all(self) -> None:
        p = _pipeline(
            _FixedScanner("s1", ScanVerdict.REJECT, 0.9, priority=10),
            _FixedScanner("s2", ScanVerdict.ALLOW, 0.0, priority=20),
            fail_fast=False,
        )
        r = await p.scan_input(_plan())
        assert r.allowed is False
        assert r.short_circuited is False
        assert len(r.scanner_results) == 2


# ---------------------------------------------------------------------------
# Pipeline — reject_threshold
# ---------------------------------------------------------------------------


class TestPipelineRiskThreshold:
    @pytest.mark.asyncio
    async def test_high_risk_below_reject_threshold(self) -> None:
        """WARN with high risk but below threshold stays WARN."""
        p = _pipeline(
            _FixedScanner("s1", ScanVerdict.WARN, 0.6),
            reject_threshold=0.7,
        )
        r = await p.scan_input(_plan())
        assert r.allowed is True

    @pytest.mark.asyncio
    async def test_high_risk_above_threshold_rejects(self) -> None:
        """WARN with risk >= threshold gets upgraded to REJECT."""
        p = _pipeline(
            _FixedScanner("s1", ScanVerdict.WARN, 0.8),
            reject_threshold=0.7,
        )
        r = await p.scan_input(_plan())
        assert r.allowed is False
        assert r.aggregate_verdict == ScanVerdict.REJECT


# ---------------------------------------------------------------------------
# Pipeline — error handling
# ---------------------------------------------------------------------------


class TestPipelineErrorHandling:
    @pytest.mark.asyncio
    async def test_scanner_error_treated_as_warn(self) -> None:
        p = _pipeline(_ErrorScanner())
        r = await p.scan_input(_plan())
        assert r.allowed is True
        assert r.aggregate_verdict == ScanVerdict.WARN
        assert "Scanner error" in r.scanner_results[0].details


# ---------------------------------------------------------------------------
# Pipeline — execution order
# ---------------------------------------------------------------------------


class TestPipelineOrder:
    @pytest.mark.asyncio
    async def test_scanners_run_in_priority_order(self) -> None:
        order: list[str] = []

        class _TrackingScanner(InputScanner):
            def __init__(self, sid: str, prio: int) -> None:
                self.scanner_id = sid  # type: ignore[misc]
                self.priority = prio  # type: ignore[misc]

            async def scan(self, text: str, context: ScanContext | None = None) -> ScanResult:
                order.append(self.scanner_id)
                return ScanResult(scanner_id=self.scanner_id, verdict=ScanVerdict.ALLOW)

        p = _pipeline(
            _TrackingScanner("high_prio", 5),
            _TrackingScanner("low_prio", 100),
            _TrackingScanner("mid_prio", 50),
        )
        await p.scan_input(_plan())
        assert order == ["high_prio", "mid_prio", "low_prio"]


# ---------------------------------------------------------------------------
# Pipeline — ScanContext
# ---------------------------------------------------------------------------


class TestPipelineContext:
    @pytest.mark.asyncio
    async def test_context_populated(self) -> None:
        received_ctx: list[ScanContext | None] = []

        class _CtxScanner(InputScanner):
            scanner_id = "ctx"  # type: ignore[assignment]
            priority = 10  # type: ignore[assignment]

            async def scan(self, text: str, context: ScanContext | None = None) -> ScanResult:
                received_ctx.append(context)
                return ScanResult(scanner_id=self.scanner_id, verdict=ScanVerdict.ALLOW)

        p = _pipeline(_CtxScanner())
        plan = _plan("my description")
        await p.scan_input(plan)

        assert len(received_ctx) == 1
        ctx = received_ctx[0]
        assert ctx is not None
        assert ctx.plan_id == "p-test"
        assert ctx.plan_description == "my description"
        assert ctx.action_count == 1
        assert "filesystem" in ctx.module_ids


# ---------------------------------------------------------------------------
# Pipeline — audit logging
# ---------------------------------------------------------------------------


class TestPipelineAudit:
    @pytest.mark.asyncio
    async def test_audit_passed(self) -> None:
        audit = AsyncMock()
        p = SecurityPipeline(
            registry=_registry(_FixedScanner("s1", ScanVerdict.ALLOW)),
            audit_logger=audit,
        )
        await p.scan_input(_plan())
        audit.log.assert_called_once()
        call_args = audit.log.call_args
        from llmos_bridge.security.audit import AuditEvent
        assert call_args[0][0] == AuditEvent.INPUT_SCAN_PASSED

    @pytest.mark.asyncio
    async def test_audit_rejected(self) -> None:
        audit = AsyncMock()
        p = SecurityPipeline(
            registry=_registry(_FixedScanner("s1", ScanVerdict.REJECT, 0.9)),
            audit_logger=audit,
        )
        await p.scan_input(_plan())
        from llmos_bridge.security.audit import AuditEvent
        assert audit.log.call_args[0][0] == AuditEvent.INPUT_SCAN_REJECTED

    @pytest.mark.asyncio
    async def test_audit_warned(self) -> None:
        audit = AsyncMock()
        p = SecurityPipeline(
            registry=_registry(_FixedScanner("s1", ScanVerdict.WARN, 0.4)),
            audit_logger=audit,
        )
        await p.scan_input(_plan())
        from llmos_bridge.security.audit import AuditEvent
        assert audit.log.call_args[0][0] == AuditEvent.INPUT_SCAN_WARNED


# ---------------------------------------------------------------------------
# Pipeline — status
# ---------------------------------------------------------------------------


class TestPipelineStatus:
    def test_status_dict(self) -> None:
        p = _pipeline(
            _FixedScanner("s1", ScanVerdict.ALLOW, priority=10),
            _FixedScanner("s2", ScanVerdict.ALLOW, priority=20),
            reject_threshold=0.8,
        )
        st = p.status()
        assert st["enabled"] is True
        assert st["fail_fast"] is True
        assert st["reject_threshold"] == 0.8
        assert len(st["scanners"]) == 2


# ---------------------------------------------------------------------------
# Pipeline — serialization
# ---------------------------------------------------------------------------


class TestPipelineSerialization:
    def test_serialize_plan(self) -> None:
        import json

        plan = _plan("serialize test")
        text = SecurityPipeline._serialize_plan(plan)
        data = json.loads(text)
        assert data["plan_id"] == "p-test"
        assert data["description"] == "serialize test"
        assert len(data["actions"]) == 1
        assert data["actions"][0]["module"] == "filesystem"
