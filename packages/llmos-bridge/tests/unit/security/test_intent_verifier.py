"""Unit tests -- IntentVerifier (LLM-based security analysis).

Tests cover:
  - VerificationResult and ThreatDetail model invariants
  - Disabled verifier (bypass path)
  - Plan-level verification with mock LLM responses
  - LRU cache behaviour (hit, miss, eviction, hash stability)
  - Error handling (LLM failures, malformed JSON, markdown fences)
  - Action-level verification
  - Audit event emission
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from llmos_bridge.protocol.models import IMLAction, IMLPlan
from llmos_bridge.security.audit import AuditEvent, AuditLogger
from llmos_bridge.security.intent_verifier import (
    IntentVerifier,
    ThreatDetail,
    ThreatType,
    VerificationResult,
    VerificationVerdict,
)
from llmos_bridge.security.llm_client import LLMClient, LLMMessage, LLMResponse


# ---------------------------------------------------------------------------
# Mock LLM client
# ---------------------------------------------------------------------------


class MockLLMClient(LLMClient):
    """Deterministic LLM client that returns a pre-configured response."""

    def __init__(self, response: str) -> None:
        self._response = response
        self.call_count = 0

    async def chat(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int = 2048,
        timeout: float = 30.0,
    ) -> LLMResponse:
        self.call_count += 1
        return LLMResponse(content=self._response, model="mock")

    async def close(self) -> None:
        pass


class FailingLLMClient(LLMClient):
    """LLM client that always raises an exception."""

    async def chat(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int = 2048,
        timeout: float = 30.0,
    ) -> LLMResponse:
        raise ConnectionError("LLM service unavailable")

    async def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_plan(
    *,
    plan_id: str = "test-plan-1",
    description: str = "Test plan",
    actions: list[IMLAction] | None = None,
) -> IMLPlan:
    """Build a minimal IMLPlan for testing."""
    if actions is None:
        actions = [
            IMLAction(
                id="a1",
                action="read_file",
                module="filesystem",
                params={"path": "/tmp/test.txt"},
            ),
        ]
    return IMLPlan(plan_id=plan_id, description=description, actions=actions)


def _approve_json(**overrides: object) -> str:
    """Return a JSON string for an APPROVE verdict."""
    data = {
        "verdict": "approve",
        "risk_level": "low",
        "reasoning": "Plan is safe.",
        "threats": [],
        "clarification_needed": None,
        "recommendations": [],
    }
    data.update(overrides)
    return json.dumps(data)


def _reject_json(
    *,
    reasoning: str = "Dangerous plan detected.",
    threats: list[dict[str, object]] | None = None,
    recommendations: list[str] | None = None,
) -> str:
    """Return a JSON string for a REJECT verdict."""
    return json.dumps(
        {
            "verdict": "reject",
            "risk_level": "critical",
            "reasoning": reasoning,
            "threats": threats
            or [
                {
                    "threat_type": "data_exfiltration",
                    "severity": "critical",
                    "description": "Reads credentials then sends HTTP request",
                    "affected_action_ids": ["a1", "a2"],
                    "evidence": "read /etc/passwd then POST to external URL",
                }
            ],
            "clarification_needed": None,
            "recommendations": recommendations or ["Remove the HTTP action."],
        }
    )


# ===================================================================
# VerificationResult model tests
# ===================================================================


class TestVerificationResultModel:
    """VerificationResult Pydantic model invariants."""

    def test_verification_result_defaults(self) -> None:
        """Default values are sensible when only verdict is provided."""
        result = VerificationResult(verdict=VerificationVerdict.APPROVE)

        assert result.verdict == VerificationVerdict.APPROVE
        assert result.risk_level == "low"
        assert result.reasoning == ""
        assert result.threats == []
        assert result.clarification_needed is None
        assert result.recommendations == []
        assert result.analysis_duration_ms == 0.0
        assert result.llm_model == ""
        assert result.cached is False

    def test_is_safe_approve(self) -> None:
        """APPROVE verdict is safe."""
        result = VerificationResult(verdict=VerificationVerdict.APPROVE)
        assert result.is_safe() is True

    def test_is_safe_warn(self) -> None:
        """WARN verdict is safe (proceed with warnings)."""
        result = VerificationResult(verdict=VerificationVerdict.WARN)
        assert result.is_safe() is True

    def test_is_safe_reject(self) -> None:
        """REJECT verdict is NOT safe."""
        result = VerificationResult(verdict=VerificationVerdict.REJECT)
        assert result.is_safe() is False

    def test_is_safe_clarify(self) -> None:
        """CLARIFY verdict is NOT safe (requires user input)."""
        result = VerificationResult(verdict=VerificationVerdict.CLARIFY)
        assert result.is_safe() is False

    def test_threat_detail_model(self) -> None:
        """ThreatDetail fields are stored correctly."""
        threat = ThreatDetail(
            threat_type=ThreatType.PROMPT_INJECTION,
            severity="high",
            description="Injection in file content",
            affected_action_ids=["a1", "a3"],
            evidence="'ignore previous instructions' found in params",
        )

        assert threat.threat_type == ThreatType.PROMPT_INJECTION
        assert threat.severity == "high"
        assert threat.description == "Injection in file content"
        assert threat.affected_action_ids == ["a1", "a3"]
        assert threat.evidence == "'ignore previous instructions' found in params"


# ===================================================================
# IntentVerifier disabled
# ===================================================================


class TestDisabledVerifier:
    """When enabled=False the verifier is a no-op pass-through."""

    @pytest.mark.asyncio
    async def test_disabled_returns_approve(self) -> None:
        """Disabled verifier returns APPROVE for any plan."""
        verifier = IntentVerifier(enabled=False)
        plan = _make_plan()
        result = await verifier.verify_plan(plan)

        assert result.verdict == VerificationVerdict.APPROVE
        assert result.is_safe() is True
        assert "disabled" in result.reasoning.lower()

    @pytest.mark.asyncio
    async def test_disabled_skips_llm_call(self) -> None:
        """LLM client is never called when verifier is disabled."""
        client = MockLLMClient(response="SHOULD NOT BE CALLED")
        verifier = IntentVerifier(llm_client=client, enabled=False)
        plan = _make_plan()

        await verifier.verify_plan(plan)

        assert client.call_count == 0


# ===================================================================
# Plan-level verification (mock LLM)
# ===================================================================


class TestPlanVerification:
    """Verify plan analysis with controlled LLM responses."""

    @pytest.mark.asyncio
    async def test_safe_plan_approved(self) -> None:
        """LLM returns approve -- result.is_safe() is True."""
        client = MockLLMClient(response=_approve_json())
        verifier = IntentVerifier(llm_client=client, enabled=True)
        plan = _make_plan()

        result = await verifier.verify_plan(plan)

        assert result.verdict == VerificationVerdict.APPROVE
        assert result.is_safe() is True
        assert result.risk_level == "low"
        assert result.llm_model == "mock"
        assert client.call_count == 1

    @pytest.mark.asyncio
    async def test_dangerous_plan_rejected(self) -> None:
        """LLM returns reject -- result.is_safe() is False."""
        client = MockLLMClient(response=_reject_json())
        verifier = IntentVerifier(llm_client=client, enabled=True)
        plan = _make_plan()

        result = await verifier.verify_plan(plan)

        assert result.verdict == VerificationVerdict.REJECT
        assert result.is_safe() is False
        assert result.risk_level == "critical"
        assert len(result.threats) == 1
        assert result.threats[0].threat_type == ThreatType.DATA_EXFILTRATION

    @pytest.mark.asyncio
    async def test_warning_plan_passes(self) -> None:
        """WARN verdict -- is_safe() is True."""
        response = json.dumps(
            {
                "verdict": "warn",
                "risk_level": "medium",
                "reasoning": "Minor concern about file path.",
                "threats": [],
                "recommendations": ["Double-check the target path."],
            }
        )
        client = MockLLMClient(response=response)
        verifier = IntentVerifier(llm_client=client, enabled=True)
        plan = _make_plan()

        result = await verifier.verify_plan(plan)

        assert result.verdict == VerificationVerdict.WARN
        assert result.is_safe() is True
        assert result.risk_level == "medium"

    @pytest.mark.asyncio
    async def test_clarify_plan(self) -> None:
        """CLARIFY verdict -- is_safe() is False."""
        response = json.dumps(
            {
                "verdict": "clarify",
                "risk_level": "medium",
                "reasoning": "Intent is ambiguous.",
                "threats": [],
                "clarification_needed": "Is the target /etc/hosts intentional?",
            }
        )
        client = MockLLMClient(response=response)
        verifier = IntentVerifier(llm_client=client, enabled=True)
        plan = _make_plan()

        result = await verifier.verify_plan(plan)

        assert result.verdict == VerificationVerdict.CLARIFY
        assert result.is_safe() is False
        assert result.clarification_needed == "Is the target /etc/hosts intentional?"

    @pytest.mark.asyncio
    async def test_plan_with_threats_parsed(self) -> None:
        """Threats are correctly parsed from JSON into ThreatDetail objects."""
        response = _reject_json(
            threats=[
                {
                    "threat_type": "privilege_escalation",
                    "severity": "critical",
                    "description": "Modifies sudoers file",
                    "affected_action_ids": ["a1"],
                    "evidence": "write_file /etc/sudoers",
                }
            ],
        )
        client = MockLLMClient(response=response)
        verifier = IntentVerifier(llm_client=client, enabled=True)
        plan = _make_plan()

        result = await verifier.verify_plan(plan)

        assert len(result.threats) == 1
        threat = result.threats[0]
        assert threat.threat_type == ThreatType.PRIVILEGE_ESCALATION
        assert threat.severity == "critical"
        assert "sudoers" in threat.description.lower()
        assert threat.affected_action_ids == ["a1"]
        assert threat.evidence == "write_file /etc/sudoers"

    @pytest.mark.asyncio
    async def test_recommendations_parsed(self) -> None:
        """Recommendations list is parsed from the LLM response."""
        response = json.dumps(
            {
                "verdict": "warn",
                "risk_level": "medium",
                "reasoning": "Path looks unusual.",
                "threats": [],
                "recommendations": [
                    "Use an absolute path.",
                    "Consider sandboxing.",
                ],
            }
        )
        client = MockLLMClient(response=response)
        verifier = IntentVerifier(llm_client=client, enabled=True)
        plan = _make_plan()

        result = await verifier.verify_plan(plan)

        assert result.recommendations == [
            "Use an absolute path.",
            "Consider sandboxing.",
        ]

    @pytest.mark.asyncio
    async def test_multiple_threats(self) -> None:
        """Multiple threats in a single response are all parsed."""
        response = json.dumps(
            {
                "verdict": "reject",
                "risk_level": "critical",
                "reasoning": "Multiple threats found.",
                "threats": [
                    {
                        "threat_type": "data_exfiltration",
                        "severity": "critical",
                        "description": "Reads SSH keys then POSTs to external URL.",
                        "affected_action_ids": ["a1", "a2"],
                        "evidence": "read ~/.ssh/id_rsa",
                    },
                    {
                        "threat_type": "prompt_injection",
                        "severity": "high",
                        "description": "Injection attempt in file content parameter.",
                        "affected_action_ids": ["a3"],
                        "evidence": "ignore previous instructions",
                    },
                    {
                        "threat_type": "suspicious_sequence",
                        "severity": "medium",
                        "description": "Write then execute pattern.",
                        "affected_action_ids": ["a4", "a5"],
                        "evidence": "write_file .sh then run_command",
                    },
                ],
                "recommendations": [],
            }
        )
        client = MockLLMClient(response=response)
        verifier = IntentVerifier(llm_client=client, enabled=True)
        plan = _make_plan()

        result = await verifier.verify_plan(plan)

        assert len(result.threats) == 3
        types = {t.threat_type for t in result.threats}
        assert types == {
            ThreatType.DATA_EXFILTRATION,
            ThreatType.PROMPT_INJECTION,
            ThreatType.SUSPICIOUS_SEQUENCE,
        }


# ===================================================================
# Cache tests
# ===================================================================


class TestCache:
    """LRU cache keyed on plan content hash."""

    @pytest.mark.asyncio
    async def test_cache_hit_on_same_plan(self) -> None:
        """Second verify_plan with the same plan returns cached=True."""
        client = MockLLMClient(response=_approve_json())
        verifier = IntentVerifier(llm_client=client, enabled=True)
        plan = _make_plan()

        first = await verifier.verify_plan(plan)
        second = await verifier.verify_plan(plan)

        assert first.cached is False
        assert second.cached is True
        assert client.call_count == 1  # LLM called only once

    @pytest.mark.asyncio
    async def test_cache_miss_on_different_plan(self) -> None:
        """Plans with different actions produce cache misses."""
        client = MockLLMClient(response=_approve_json())
        verifier = IntentVerifier(llm_client=client, enabled=True)

        plan_a = _make_plan(
            plan_id="plan-a",
            actions=[
                IMLAction(
                    id="a1",
                    action="read_file",
                    module="filesystem",
                    params={"path": "/tmp/a.txt"},
                ),
            ],
        )
        plan_b = _make_plan(
            plan_id="plan-b",
            actions=[
                IMLAction(
                    id="a1",
                    action="write_file",
                    module="filesystem",
                    params={"path": "/tmp/b.txt", "content": "hello"},
                ),
            ],
        )

        await verifier.verify_plan(plan_a)
        await verifier.verify_plan(plan_b)

        assert client.call_count == 2

    @pytest.mark.asyncio
    async def test_cache_eviction(self) -> None:
        """When cache exceeds max size, oldest entries are evicted."""
        client = MockLLMClient(response=_approve_json())
        verifier = IntentVerifier(llm_client=client, enabled=True, cache_size=2)

        plans = [
            _make_plan(
                plan_id=f"plan-{i}",
                actions=[
                    IMLAction(
                        id="a1",
                        action="read_file",
                        module="filesystem",
                        params={"path": f"/tmp/{i}.txt"},
                    ),
                ],
            )
            for i in range(3)
        ]

        # Fill cache: plan-0, plan-1
        await verifier.verify_plan(plans[0])
        await verifier.verify_plan(plans[1])
        assert client.call_count == 2

        # Add plan-2 -- should evict plan-0
        await verifier.verify_plan(plans[2])
        assert client.call_count == 3

        # plan-1 should still be cached
        result_1 = await verifier.verify_plan(plans[1])
        assert result_1.cached is True
        assert client.call_count == 3

        # plan-0 was evicted -- LLM called again
        result_0 = await verifier.verify_plan(plans[0])
        assert result_0.cached is False
        assert client.call_count == 4

    @pytest.mark.asyncio
    async def test_cache_hash_ignores_plan_id(self) -> None:
        """Same actions with different plan_id produce a cache hit."""
        client = MockLLMClient(response=_approve_json())
        verifier = IntentVerifier(llm_client=client, enabled=True)

        actions = [
            IMLAction(
                id="a1",
                action="read_file",
                module="filesystem",
                params={"path": "/tmp/same.txt"},
            ),
        ]
        plan_a = _make_plan(plan_id="plan-alpha", actions=actions)
        plan_b = _make_plan(plan_id="plan-beta", actions=actions)

        first = await verifier.verify_plan(plan_a)
        second = await verifier.verify_plan(plan_b)

        assert first.cached is False
        assert second.cached is True
        assert client.call_count == 1

    @pytest.mark.asyncio
    async def test_no_cache_when_cache_size_zero(self) -> None:
        """cache_size=0 disables caching entirely."""
        client = MockLLMClient(response=_approve_json())
        verifier = IntentVerifier(llm_client=client, enabled=True, cache_size=0)
        plan = _make_plan()

        first = await verifier.verify_plan(plan)
        second = await verifier.verify_plan(plan)

        assert first.cached is False
        assert second.cached is False
        assert client.call_count == 2


# ===================================================================
# Error handling
# ===================================================================


class TestErrorHandling:
    """Graceful degradation when the LLM fails or returns garbage."""

    @pytest.mark.asyncio
    async def test_llm_exception_permissive(self) -> None:
        """LLM raises exception, strict=False -> WARN verdict (permissive)."""
        verifier = IntentVerifier(
            llm_client=FailingLLMClient(),
            enabled=True,
            strict=False,
        )
        plan = _make_plan()

        result = await verifier.verify_plan(plan)

        assert result.verdict == VerificationVerdict.WARN
        assert result.is_safe() is True
        assert "unavailable" in result.reasoning.lower() or "failed" in result.reasoning.lower()

    @pytest.mark.asyncio
    async def test_llm_exception_strict(self) -> None:
        """LLM raises exception, strict=True -> REJECT verdict."""
        verifier = IntentVerifier(
            llm_client=FailingLLMClient(),
            enabled=True,
            strict=True,
        )
        plan = _make_plan()

        result = await verifier.verify_plan(plan)

        assert result.verdict == VerificationVerdict.REJECT
        assert result.is_safe() is False
        assert result.risk_level == "high"
        assert "failed" in result.reasoning.lower()

    @pytest.mark.asyncio
    async def test_llm_invalid_json_response(self) -> None:
        """Unparseable response -> WARN verdict (graceful fallback)."""
        client = MockLLMClient(response="This is not JSON at all!!!")
        verifier = IntentVerifier(llm_client=client, enabled=True)
        plan = _make_plan()

        result = await verifier.verify_plan(plan)

        assert result.verdict == VerificationVerdict.WARN
        assert "could not parse" in result.reasoning.lower()
        assert result.llm_model == "mock"

    @pytest.mark.asyncio
    async def test_parse_response_with_markdown_fences(self) -> None:
        """```json ... ``` wrapper is stripped before parsing."""
        inner = _approve_json(reasoning="Fenced response parsed OK.")
        fenced = f"```json\n{inner}\n```"
        client = MockLLMClient(response=fenced)
        verifier = IntentVerifier(llm_client=client, enabled=True)
        plan = _make_plan()

        result = await verifier.verify_plan(plan)

        assert result.verdict == VerificationVerdict.APPROVE
        assert result.reasoning == "Fenced response parsed OK."


# ===================================================================
# Action-level verification
# ===================================================================


class TestActionVerification:
    """verify_action() path for single-action analysis."""

    @pytest.mark.asyncio
    async def test_verify_action_safe(self) -> None:
        """verify_action with an approve response returns safe result."""
        client = MockLLMClient(response=_approve_json())
        verifier = IntentVerifier(llm_client=client, enabled=True)
        action = IMLAction(
            id="a1",
            action="read_file",
            module="filesystem",
            params={"path": "/tmp/test.txt"},
        )

        result = await verifier.verify_action(
            action,
            plan_id="plan-x",
            plan_description="Read a file",
        )

        assert result.verdict == VerificationVerdict.APPROVE
        assert result.is_safe() is True
        assert result.analysis_duration_ms >= 0
        assert client.call_count == 1

    @pytest.mark.asyncio
    async def test_verify_action_disabled(self) -> None:
        """verify_action returns approve when verifier is disabled."""
        client = MockLLMClient(response="SHOULD NOT BE CALLED")
        verifier = IntentVerifier(llm_client=client, enabled=False)
        action = IMLAction(
            id="a1",
            action="read_file",
            module="filesystem",
            params={"path": "/tmp/test.txt"},
        )

        result = await verifier.verify_action(action)

        assert result.verdict == VerificationVerdict.APPROVE
        assert result.is_safe() is True
        assert client.call_count == 0


# ===================================================================
# Audit logging
# ===================================================================


class TestAuditLogging:
    """Audit events emitted on verify_plan() completion."""

    @pytest.mark.asyncio
    async def test_audit_event_on_reject(self) -> None:
        """INTENT_REJECTED event emitted when verdict is REJECT."""
        audit = AsyncMock(spec=AuditLogger)
        client = MockLLMClient(response=_reject_json())
        verifier = IntentVerifier(
            llm_client=client,
            audit_logger=audit,
            enabled=True,
        )
        plan = _make_plan()

        await verifier.verify_plan(plan)

        audit.log.assert_awaited_once()
        call_args = audit.log.call_args
        assert call_args[0][0] == AuditEvent.INTENT_REJECTED
        assert call_args[1]["plan_id"] == "test-plan-1"
        assert call_args[1]["intent_verdict"] == "reject"
        assert call_args[1]["intent_risk"] == "critical"
        assert "data_exfiltration" in call_args[1]["intent_threats"]

    @pytest.mark.asyncio
    async def test_audit_event_on_approve(self) -> None:
        """INTENT_VERIFIED event emitted when verdict is APPROVE."""
        audit = AsyncMock(spec=AuditLogger)
        client = MockLLMClient(response=_approve_json())
        verifier = IntentVerifier(
            llm_client=client,
            audit_logger=audit,
            enabled=True,
        )
        plan = _make_plan()

        await verifier.verify_plan(plan)

        audit.log.assert_awaited_once()
        call_args = audit.log.call_args
        assert call_args[0][0] == AuditEvent.INTENT_VERIFIED
        assert call_args[1]["plan_id"] == "test-plan-1"
        assert call_args[1]["intent_verdict"] == "approve"
