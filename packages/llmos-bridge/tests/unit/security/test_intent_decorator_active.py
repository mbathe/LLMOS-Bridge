"""Unit tests — @intent_verified decorator runtime behaviour.

Tests cover the ACTIVATED runtime path of the ``@intent_verified`` decorator
in ``llmos_bridge.security.decorators``.  When a module has
``self._security.intent_verifier`` set and enabled, the decorator calls
``verify_action()`` before executing the wrapped function.

Scenarios:
  1. Verifier is called when security + intent_verifier are set
  2. strict=True raises SuspiciousIntentError on REJECT verdict
  3. strict=False logs warning but continues on REJECT verdict
  4. Verifier disabled (enabled=False) -> function executes normally
  5. No _security attribute -> function executes normally
  6. _security exists but intent_verifier is None -> function executes normally
"""

from __future__ import annotations

import pytest

from llmos_bridge.exceptions import SuspiciousIntentError
from llmos_bridge.security.decorators import intent_verified
from llmos_bridge.security.intent_verifier import (
    ThreatDetail,
    ThreatType,
    VerificationResult,
    VerificationVerdict,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeVerifier:
    """Configurable fake IntentVerifier for testing decorator integration."""

    def __init__(self, result: VerificationResult) -> None:
        self._result = result
        self._enabled = True
        self.call_count = 0
        self.last_action = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def verify_action(self, action, **kwargs):
        self.call_count += 1
        self.last_action = action
        return self._result


class FakeSecurity:
    """Minimal stand-in for the SecurityManager attached to modules."""

    def __init__(self, verifier=None) -> None:
        self.intent_verifier = verifier


class TestModule:
    """Module stub with decorated action methods."""

    MODULE_ID = "test_module"

    @intent_verified(strict=False)
    async def _action_test_permissive(self, params: dict) -> dict:
        return {"ok": True}

    @intent_verified(strict=True)
    async def _action_test_strict(self, params: dict) -> dict:
        return {"ok": True}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _approve_result() -> VerificationResult:
    return VerificationResult(
        verdict=VerificationVerdict.APPROVE,
        reasoning="Looks safe.",
    )


def _reject_result() -> VerificationResult:
    return VerificationResult(
        verdict=VerificationVerdict.REJECT,
        risk_level="high",
        reasoning="Detected suspicious pattern.",
        threats=[
            ThreatDetail(
                threat_type=ThreatType.PROMPT_INJECTION,
                severity="high",
                description="Injection detected in params",
            ),
        ],
    )


# ===================================================================
# 1. Verifier is called when security + intent_verifier are present
# ===================================================================


class TestIntentVerifierCalled:

    @pytest.mark.asyncio
    async def test_intent_verified_calls_verifier_when_security_set(self) -> None:
        """When module has _security.intent_verifier enabled, verify_action is called."""
        verifier = FakeVerifier(_approve_result())
        mod = TestModule()
        mod._security = FakeSecurity(verifier=verifier)

        result = await mod._action_test_permissive({"path": "/tmp/x"})

        assert result == {"ok": True}
        assert verifier.call_count == 1
        # The decorator should have built a mock IMLAction and passed it
        assert verifier.last_action is not None
        assert verifier.last_action.action == "test_permissive"
        assert verifier.last_action.module == "test_module"
        assert verifier.last_action.params == {"path": "/tmp/x"}


# ===================================================================
# 2. strict=True raises SuspiciousIntentError on REJECT
# ===================================================================


class TestStrictReject:

    @pytest.mark.asyncio
    async def test_intent_verified_strict_raises_on_reject(self) -> None:
        """With strict=True, if verifier returns REJECT, SuspiciousIntentError is raised."""
        verifier = FakeVerifier(_reject_result())
        mod = TestModule()
        mod._security = FakeSecurity(verifier=verifier)

        with pytest.raises(SuspiciousIntentError) as exc_info:
            await mod._action_test_strict({"cmd": "rm -rf /"})

        assert verifier.call_count == 1
        assert "Detected suspicious pattern." in exc_info.value.reasoning
        assert "prompt_injection" in exc_info.value.threats


# ===================================================================
# 3. strict=False continues on REJECT (permissive mode)
# ===================================================================


class TestPermissiveContinues:

    @pytest.mark.asyncio
    async def test_intent_verified_permissive_continues_on_reject(self) -> None:
        """With strict=False, if verifier returns REJECT, function still executes."""
        verifier = FakeVerifier(_reject_result())
        mod = TestModule()
        mod._security = FakeSecurity(verifier=verifier)

        # Should NOT raise — permissive mode logs warning but continues
        result = await mod._action_test_permissive({"cmd": "rm -rf /"})

        assert result == {"ok": True}
        assert verifier.call_count == 1


# ===================================================================
# 4. Verifier disabled -> skipped
# ===================================================================


class TestVerifierDisabled:

    @pytest.mark.asyncio
    async def test_intent_verified_skips_when_verifier_disabled(self) -> None:
        """If verifier.enabled is False, function executes normally without verification."""
        verifier = FakeVerifier(_reject_result())
        verifier._enabled = False
        mod = TestModule()
        mod._security = FakeSecurity(verifier=verifier)

        result = await mod._action_test_strict({"path": "/etc/shadow"})

        assert result == {"ok": True}
        # verify_action should NOT have been called
        assert verifier.call_count == 0


# ===================================================================
# 5. No _security attribute -> skipped
# ===================================================================


class TestNoSecurity:

    @pytest.mark.asyncio
    async def test_intent_verified_skips_when_no_security(self) -> None:
        """If module has no _security attribute, function executes normally."""
        mod = TestModule()
        # Ensure no _security attribute exists
        if hasattr(mod, "_security"):
            delattr(mod, "_security")

        result = await mod._action_test_strict({"path": "/tmp/x"})

        assert result == {"ok": True}


# ===================================================================
# 6. _security exists but intent_verifier is None -> skipped
# ===================================================================


class TestNoVerifierOnManager:

    @pytest.mark.asyncio
    async def test_intent_verified_skips_when_no_verifier_on_manager(self) -> None:
        """If _security exists but intent_verifier is None, function executes normally."""
        mod = TestModule()
        mod._security = FakeSecurity(verifier=None)

        result = await mod._action_test_strict({"path": "/tmp/x"})

        assert result == {"ok": True}
