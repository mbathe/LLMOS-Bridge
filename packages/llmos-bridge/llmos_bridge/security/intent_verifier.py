"""Security layer — LLM-based intent verification (Couche 1).

The IntentVerifier is the FIRST barrier before any plan reaches the
PermissionManager.  It uses a dedicated "security LLM" to analyse
incoming IML plans for:

  - Prompt injection attacks embedded in parameters
  - Privilege escalation attempts
  - Suspicious action sequences (e.g. read credentials then HTTP POST)
  - Intent misalignment (description says X, actions do Y)
  - Data exfiltration patterns
  - Obfuscated malicious payloads
  - Resource abuse
  - Custom user-defined threat categories

Architecture:
  - LLM-agnostic via LLMClient ABC
  - Async-first (all calls are awaitable)
  - Composable system prompt via PromptComposer + ThreatCategoryRegistry
  - Caching via plan content hash (don't re-verify identical plans)
  - Configurable strict/permissive mode
  - EventBus integration via AuditLogger

Usage::

    from llmos_bridge.security.threat_categories import ThreatCategoryRegistry
    from llmos_bridge.security.prompt_composer import PromptComposer

    registry = ThreatCategoryRegistry()
    registry.register_builtins()
    composer = PromptComposer(category_registry=registry)

    verifier = IntentVerifier(
        llm_client=my_llm_client,
        audit_logger=audit,
        prompt_composer=composer,
        enabled=True,
        strict=True,
    )
    result = await verifier.verify_plan(plan)
    if not result.is_safe():
        raise SuspiciousIntentError(plan.plan_id, result.reasoning)
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import OrderedDict
from enum import Enum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from llmos_bridge.logging import get_logger
from llmos_bridge.protocol.models import IMLAction, IMLPlan
from llmos_bridge.security.audit import AuditEvent, AuditLogger
from llmos_bridge.security.llm_client import LLMClient, LLMMessage, NullLLMClient

if TYPE_CHECKING:
    from llmos_bridge.security.prompt_composer import PromptComposer
    from llmos_bridge.security.threat_categories import ThreatCategoryRegistry

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Verification result models
# ---------------------------------------------------------------------------


class VerificationVerdict(str, Enum):
    """Verdict from the security analysis LLM."""

    APPROVE = "approve"
    REJECT = "reject"
    WARN = "warn"
    CLARIFY = "clarify"


class ThreatType(str, Enum):
    """Categories of threats the verifier can detect."""

    PROMPT_INJECTION = "prompt_injection"
    PRIVILEGE_ESCALATION = "privilege_escalation"
    DATA_EXFILTRATION = "data_exfiltration"
    SUSPICIOUS_SEQUENCE = "suspicious_sequence"
    INTENT_MISALIGNMENT = "intent_misalignment"
    OBFUSCATED_PAYLOAD = "obfuscated_payload"
    RESOURCE_ABUSE = "resource_abuse"
    CUSTOM = "custom"
    NONE = "none"


class ThreatDetail(BaseModel):
    """Details about a single detected threat."""

    threat_type: ThreatType
    severity: str = "medium"  # low, medium, high, critical
    description: str = ""
    affected_action_ids: list[str] = Field(default_factory=list)
    evidence: str = ""


class VerificationResult(BaseModel):
    """Result of intent verification analysis."""

    verdict: VerificationVerdict
    risk_level: str = "low"  # low, medium, high, critical
    reasoning: str = ""
    threats: list[ThreatDetail] = Field(default_factory=list)
    clarification_needed: str | None = None
    recommendations: list[str] = Field(default_factory=list)
    analysis_duration_ms: float = 0.0
    llm_model: str = ""
    cached: bool = False

    def is_safe(self) -> bool:
        """Return True if the plan can proceed without human review."""
        return self.verdict in (VerificationVerdict.APPROVE, VerificationVerdict.WARN)


# ---------------------------------------------------------------------------
# Fallback system prompt (used when no PromptComposer is provided)
# ---------------------------------------------------------------------------
# This is a compact fallback to ensure the verifier works even without the
# PromptComposer.  In production, the PromptComposer should always be used.

_FALLBACK_SYSTEM_PROMPT = """\
You are a security analysis agent for LLMOS Bridge.  Analyse IML plans for \
security threats.  Respond with ONLY a valid JSON object:
{"verdict":"approve|reject|warn|clarify","risk_level":"low|medium|high|critical",\
"reasoning":"...","threats":[],"clarification_needed":null,"recommendations":[]}"""


# ---------------------------------------------------------------------------
# IntentVerifier
# ---------------------------------------------------------------------------


class IntentVerifier:
    """LLM-based pre-execution security analysis.

    Analyses IML plans and individual actions for security threats
    using a dedicated LLM with a hardened, composable security-focused
    system prompt.
    """

    def __init__(
        self,
        llm_client: LLMClient | None = None,
        audit_logger: AuditLogger | None = None,
        *,
        prompt_composer: PromptComposer | None = None,
        category_registry: ThreatCategoryRegistry | None = None,
        enabled: bool = True,
        strict: bool = False,
        cache_size: int = 256,
        cache_ttl: float = 300.0,
        timeout: float = 30.0,
        model: str = "",
    ) -> None:
        self._llm = llm_client or NullLLMClient()
        self._audit = audit_logger
        self._prompt_composer = prompt_composer
        self._category_registry = category_registry
        self._enabled = enabled
        self._strict = strict
        self._timeout = timeout
        self._model = model
        # LRU cache: content_hash → (VerificationResult, created_at)
        self._cache: OrderedDict[str, tuple[VerificationResult, float]] = OrderedDict()
        self._cache_size = cache_size
        self._cache_ttl = cache_ttl  # seconds, 0 = no TTL

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    @property
    def strict(self) -> bool:
        return self._strict

    @property
    def prompt_composer(self) -> PromptComposer | None:
        return self._prompt_composer

    @property
    def category_registry(self) -> ThreatCategoryRegistry | None:
        return self._category_registry

    def clear_cache(self) -> None:
        """Clear all cached verification results.

        Called when threat categories change (via PromptComposer invalidation)
        to prevent stale results from being served.
        """
        self._cache.clear()

    def _get_system_prompt(self) -> str:
        """Get the current system prompt (dynamic via PromptComposer or fallback)."""
        if self._prompt_composer is not None:
            return self._prompt_composer.compose()
        return _FALLBACK_SYSTEM_PROMPT

    def status(self) -> dict[str, Any]:
        """Return current verifier status for REST API introspection."""
        categories: list[dict[str, Any]] = []
        if self._category_registry is not None:
            categories = self._category_registry.to_dict_list()

        return {
            "enabled": self._enabled,
            "strict": self._strict,
            "model": self._model,
            "timeout": self._timeout,
            "cache_size": self._cache_size,
            "cache_ttl": self._cache_ttl,
            "cache_entries": len(self._cache),
            "has_prompt_composer": self._prompt_composer is not None,
            "threat_categories": categories,
        }

    # ------------------------------------------------------------------
    # Plan-level verification
    # ------------------------------------------------------------------

    async def verify_plan(self, plan: IMLPlan) -> VerificationResult:
        """Analyse an entire IML plan before execution.

        Returns a VerificationResult.  When strict=True and the verdict
        is REJECT, callers should raise SuspiciousIntentError.
        """
        if not self._enabled:
            return VerificationResult(
                verdict=VerificationVerdict.APPROVE,
                reasoning="Intent verification disabled.",
                cached=False,
            )

        # 1. Check cache FIRST (cheapest path — hash + dict lookup only).
        cache_key = self._plan_hash(plan)
        cached = self._check_cache(cache_key)
        if cached is not None:
            return cached

        # 2. Cache miss — serialize plan + compose prompt + call LLM.
        plan_summary = self._serialize_plan(plan)
        user_message = (
            "Analyse the following IML plan for security threats. "
            "Respond with ONLY a JSON object.\n\n"
            f"```json\n{plan_summary}\n```"
        )

        system_prompt = self._get_system_prompt()
        start = time.time()
        try:
            response = await self._llm.chat(
                messages=[
                    LLMMessage(role="system", content=system_prompt),
                    LLMMessage(role="user", content=user_message),
                ],
                temperature=0.0,
                max_tokens=1024,
                timeout=self._timeout,
            )
            result = self._parse_response(response.content, response.model)
        except Exception as exc:
            log.error("intent_verification_failed", error=str(exc))
            # On LLM failure: permissive mode continues, strict mode blocks
            if self._strict:
                result = VerificationResult(
                    verdict=VerificationVerdict.REJECT,
                    risk_level="high",
                    reasoning=f"Intent verification LLM call failed: {exc}",
                )
            else:
                result = VerificationResult(
                    verdict=VerificationVerdict.WARN,
                    risk_level="medium",
                    reasoning=f"Intent verification unavailable: {exc}. Proceeding in permissive mode.",
                )

        result.analysis_duration_ms = round((time.time() - start) * 1000, 1)

        # 3. Store in cache.
        self._store_cache(cache_key, result)

        # 4. Audit log.
        if self._audit:
            event = (
                AuditEvent.INTENT_REJECTED
                if result.verdict == VerificationVerdict.REJECT
                else AuditEvent.INTENT_VERIFIED
            )
            await self._audit.log(
                event,
                plan_id=plan.plan_id,
                intent_verdict=result.verdict.value,
                intent_risk=result.risk_level,
                intent_threats=[t.threat_type.value for t in result.threats],
                intent_reasoning=result.reasoning[:500],
            )

        return result

    # ------------------------------------------------------------------
    # Action-level verification (for @intent_verified decorator)
    # ------------------------------------------------------------------

    async def verify_action(
        self,
        action: IMLAction,
        *,
        plan_id: str = "",
        plan_description: str = "",
    ) -> VerificationResult:
        """Analyse a single action (used by @intent_verified decorator).

        This is a lighter-weight check focused on the specific action's
        parameters and context, not the full plan sequence analysis.
        """
        if not self._enabled:
            return VerificationResult(
                verdict=VerificationVerdict.APPROVE,
                reasoning="Intent verification disabled.",
            )

        action_summary = json.dumps(
            {
                "action_id": action.id,
                "module": action.module,
                "action": action.action,
                "params": action.params,
                "plan_id": plan_id,
                "plan_description": plan_description,
            },
            default=str,
            indent=2,
        )

        user_message = (
            "Analyse this single IML action for security threats. "
            "Focus on parameter safety, prompt injection, and whether "
            "the action matches the stated plan description. "
            "Respond with ONLY a JSON object.\n\n"
            f"```json\n{action_summary}\n```"
        )

        system_prompt = self._get_system_prompt()
        start = time.time()
        try:
            response = await self._llm.chat(
                messages=[
                    LLMMessage(role="system", content=system_prompt),
                    LLMMessage(role="user", content=user_message),
                ],
                temperature=0.0,
                max_tokens=256,
                timeout=self._timeout,
            )
            result = self._parse_response(response.content, response.model)
        except Exception as exc:
            log.error(
                "intent_action_verification_failed",
                action_id=action.id,
                error=str(exc),
            )
            if self._strict:
                result = VerificationResult(
                    verdict=VerificationVerdict.REJECT,
                    risk_level="high",
                    reasoning=f"Action verification LLM call failed: {exc}",
                )
            else:
                result = VerificationResult(
                    verdict=VerificationVerdict.WARN,
                    reasoning=f"Action verification unavailable: {exc}.",
                )

        result.analysis_duration_ms = round((time.time() - start) * 1000, 1)
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_cache(self, cache_key: str) -> VerificationResult | None:
        """Return a cached result if present and not expired, else None."""
        entry = self._cache.get(cache_key)
        if entry is None:
            return None
        result, created_at = entry
        # TTL check.
        if self._cache_ttl > 0 and (time.time() - created_at) > self._cache_ttl:
            del self._cache[cache_key]
            return None
        # LRU: move to end on access.
        self._cache.move_to_end(cache_key)
        return VerificationResult(**{**result.model_dump(), "cached": True})

    def _store_cache(self, cache_key: str, result: VerificationResult) -> None:
        """Store a verification result in the LRU cache."""
        if self._cache_size <= 0:
            return
        self._cache[cache_key] = (result, time.time())
        if len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)

    def _serialize_plan(self, plan: IMLPlan) -> str:
        """Serialise plan to a compact JSON string for the LLM."""
        data: dict[str, Any] = {
            "plan_id": plan.plan_id,
            "description": plan.description,
            "execution_mode": plan.execution_mode.value,
            "plan_mode": plan.plan_mode.value,
            "action_count": len(plan.actions),
            "actions": [],
        }
        if plan.metadata:
            data["metadata"] = {
                "created_by": plan.metadata.created_by,
                "llm_model": plan.metadata.llm_model,
                "tags": plan.metadata.tags,
            }
        if plan.compiler_trace:
            data["compiler_trace"] = {
                "generation_approved": plan.compiler_trace.generation_approved,
                "llm_model": plan.compiler_trace.llm_model,
            }
        for action in plan.actions:
            data["actions"].append(
                {
                    "id": action.id,
                    "module": action.module,
                    "action": action.action,
                    "params": action.params,
                    "depends_on": action.depends_on,
                    "on_error": action.on_error.value,
                    "requires_approval": action.requires_approval,
                }
            )
        return json.dumps(data, default=str, indent=2)

    def _parse_response(self, content: str, model: str = "") -> VerificationResult:
        """Parse the LLM's JSON response into a VerificationResult."""
        # Strip markdown code fences if present
        clean = content.strip()
        if clean.startswith("```"):
            first_newline = clean.find("\n")
            if first_newline != -1:
                clean = clean[first_newline + 1:]
        if clean.endswith("```"):
            clean = clean[:-3]
        clean = clean.strip()

        try:
            data = json.loads(clean)
        except json.JSONDecodeError:
            return VerificationResult(
                verdict=VerificationVerdict.WARN,
                reasoning=f"Could not parse verification response: {content[:200]}",
                llm_model=model,
            )

        # Parse threats
        threats: list[ThreatDetail] = []
        for t in data.get("threats", []):
            try:
                threats.append(
                    ThreatDetail(
                        threat_type=ThreatType(t.get("threat_type", "none")),
                        severity=t.get("severity", "medium"),
                        description=t.get("description", ""),
                        affected_action_ids=t.get("affected_action_ids", []),
                        evidence=t.get("evidence", ""),
                    )
                )
            except (ValueError, KeyError):
                continue

        return VerificationResult(
            verdict=VerificationVerdict(data.get("verdict", "warn")),
            risk_level=data.get("risk_level", "medium"),
            reasoning=data.get("reasoning", ""),
            threats=threats,
            clarification_needed=data.get("clarification_needed"),
            recommendations=data.get("recommendations", []),
            llm_model=model,
        )

    @staticmethod
    def _plan_hash(plan: IMLPlan) -> str:
        """Compute a content-based hash for caching."""
        # Hash based on action content, not plan_id (which is random)
        content = json.dumps(
            [
                {
                    "module": a.module,
                    "action": a.action,
                    "params": a.params,
                    "depends_on": a.depends_on,
                }
                for a in plan.actions
            ],
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(content.encode()).hexdigest()[:32]

    async def close(self) -> None:
        """Release LLM client resources."""
        await self._llm.close()
