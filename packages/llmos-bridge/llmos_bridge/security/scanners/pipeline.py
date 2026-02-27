"""Security scanners — pipeline orchestrator.

Runs all enabled scanners in priority order (fastest first) and
aggregates their results.  Short-circuits on REJECT if ``fail_fast``
is enabled.

Integration point:
    Called by ``PlanExecutor.run()`` BEFORE ``IntentVerifier.verify_plan()``.
    When ``pipeline.allowed`` is ``False``, the plan is rejected immediately
    without incurring the cost of an LLM call.

Usage::

    pipeline = SecurityPipeline(
        registry=scanner_registry,
        audit_logger=audit,
        fail_fast=True,
    )
    result = await pipeline.scan_input(plan)
    if not result.allowed:
        raise InputScanRejectedError(...)
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from llmos_bridge.logging import get_logger
from llmos_bridge.security.scanners.base import ScanContext, ScanResult, ScanVerdict
from llmos_bridge.security.scanners.registry import ScannerRegistry

if TYPE_CHECKING:
    from llmos_bridge.protocol.models import IMLPlan
    from llmos_bridge.security.audit import AuditLogger

log = get_logger(__name__)


@dataclass
class PipelineResult:
    """Aggregated result from the full scanner pipeline."""

    allowed: bool = True
    aggregate_verdict: ScanVerdict = ScanVerdict.ALLOW
    max_risk_score: float = 0.0
    scanner_results: list[ScanResult] = field(default_factory=list)
    short_circuited: bool = False
    total_duration_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "aggregate_verdict": self.aggregate_verdict.value,
            "max_risk_score": self.max_risk_score,
            "scanner_results": [r.to_dict() for r in self.scanner_results],
            "short_circuited": self.short_circuited,
            "total_duration_ms": self.total_duration_ms,
        }


class SecurityPipeline:
    """Orchestrates input scanners in priority order before plan execution."""

    def __init__(
        self,
        registry: ScannerRegistry,
        audit_logger: AuditLogger | None = None,
        *,
        fail_fast: bool = True,
        reject_threshold: float = 0.7,
        warn_threshold: float = 0.3,
        enabled: bool = True,
    ) -> None:
        self._registry = registry
        self._audit = audit_logger
        self._fail_fast = fail_fast
        self._reject_threshold = reject_threshold
        self._warn_threshold = warn_threshold
        self._enabled = enabled

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    @property
    def registry(self) -> ScannerRegistry:
        return self._registry

    async def scan_input(self, plan: IMLPlan) -> PipelineResult:
        """Run all enabled scanners against the serialised plan."""
        if not self._enabled:
            return PipelineResult(allowed=True)

        scanners = self._registry.list_enabled()
        if not scanners:
            return PipelineResult(allowed=True)

        plan_text = self._serialize_plan(plan)
        context = ScanContext(
            plan_id=plan.plan_id,
            plan_description=plan.description,
            action_count=len(plan.actions),
            module_ids=sorted({a.module for a in plan.actions}),
            session_id=plan.session_id,
        )

        start = time.time()
        pipeline_result = PipelineResult()

        for scanner in scanners:
            try:
                scan_start = time.time()
                result = await scanner.scan(plan_text, context)
                result.scan_duration_ms = round(
                    (time.time() - scan_start) * 1000, 2
                )
            except Exception as exc:
                log.error(
                    "scanner_error",
                    scanner_id=scanner.scanner_id,
                    error=str(exc),
                )
                result = ScanResult(
                    scanner_id=scanner.scanner_id,
                    verdict=ScanVerdict.WARN,
                    risk_score=0.0,
                    details=f"Scanner error: {exc}",
                )

            pipeline_result.scanner_results.append(result)

            # Update aggregates.
            if result.risk_score > pipeline_result.max_risk_score:
                pipeline_result.max_risk_score = result.risk_score
            if result.verdict == ScanVerdict.REJECT:
                pipeline_result.aggregate_verdict = ScanVerdict.REJECT
                pipeline_result.allowed = False
            elif (
                result.verdict == ScanVerdict.WARN
                and pipeline_result.aggregate_verdict != ScanVerdict.REJECT
            ):
                pipeline_result.aggregate_verdict = ScanVerdict.WARN

            # Short-circuit on REJECT if fail_fast.
            if self._fail_fast and result.verdict == ScanVerdict.REJECT:
                pipeline_result.short_circuited = True
                log.warning(
                    "scanner_pipeline_short_circuit",
                    scanner_id=scanner.scanner_id,
                    risk_score=result.risk_score,
                )
                break

        pipeline_result.total_duration_ms = round(
            (time.time() - start) * 1000, 2
        )

        # Also reject if aggregate risk score exceeds threshold.
        if (
            pipeline_result.max_risk_score >= self._reject_threshold
            and pipeline_result.aggregate_verdict != ScanVerdict.REJECT
        ):
            pipeline_result.aggregate_verdict = ScanVerdict.REJECT
            pipeline_result.allowed = False

        # Audit logging.
        if self._audit:
            from llmos_bridge.security.audit import AuditEvent

            if not pipeline_result.allowed:
                event = AuditEvent.INPUT_SCAN_REJECTED
            elif pipeline_result.aggregate_verdict == ScanVerdict.WARN:
                event = AuditEvent.INPUT_SCAN_WARNED
            else:
                event = AuditEvent.INPUT_SCAN_PASSED
            await self._audit.log(
                event,
                plan_id=plan.plan_id,
                scanner_verdict=pipeline_result.aggregate_verdict.value,
                scanner_risk=pipeline_result.max_risk_score,
                scanner_count=len(pipeline_result.scanner_results),
                scanner_duration_ms=pipeline_result.total_duration_ms,
                short_circuited=pipeline_result.short_circuited,
            )

        return pipeline_result

    def status(self) -> dict[str, Any]:
        """Return pipeline status for REST API."""
        return {
            "enabled": self._enabled,
            "fail_fast": self._fail_fast,
            "reject_threshold": self._reject_threshold,
            "warn_threshold": self._warn_threshold,
            "scanners": self._registry.to_dict_list(),
        }

    @staticmethod
    def _serialize_plan(plan: IMLPlan) -> str:
        """Serialize plan to JSON text for scanners."""
        data: dict[str, Any] = {
            "plan_id": plan.plan_id,
            "description": plan.description,
            "actions": [
                {
                    "id": a.id,
                    "module": a.module,
                    "action": a.action,
                    "params": a.params,
                }
                for a in plan.actions
            ],
        }
        if plan.metadata:
            data["metadata"] = {
                "created_by": plan.metadata.created_by,
                "tags": plan.metadata.tags,
            }
        return json.dumps(data, default=str)
