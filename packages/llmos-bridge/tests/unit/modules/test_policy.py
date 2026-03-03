"""Tests for Module Spec v3 — PolicyEnforcer.

Covers:
  - PolicyEnforcer: load_policy, check_and_acquire, release, status, reset
  - Cooldown enforcement
  - Concurrent call limits (max_parallel_calls)
  - Graceful degradation when no policy is set
  - PolicyViolationError raised correctly
"""

from __future__ import annotations

import asyncio
import time

import pytest

from llmos_bridge.exceptions import PolicyViolationError
from llmos_bridge.modules.base import BaseModule, ModulePolicy, ResourceEstimate
from llmos_bridge.modules.manifest import ModuleManifest
from llmos_bridge.modules.policy import PolicyEnforcer
from llmos_bridge.modules.registry import ModuleRegistry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

class UnlimitedModule(BaseModule):
    MODULE_ID = "unlimited"
    VERSION = "1.0.0"

    def get_manifest(self) -> ModuleManifest:
        return ModuleManifest(module_id=self.MODULE_ID, version=self.VERSION, description="")


class RateLimitedModule(BaseModule):
    MODULE_ID = "rate_limited"
    VERSION = "1.0.0"

    def get_manifest(self) -> ModuleManifest:
        return ModuleManifest(module_id=self.MODULE_ID, version=self.VERSION, description="")

    def policy_rules(self) -> ModulePolicy:
        return ModulePolicy(
            max_parallel_calls=2,
            cooldown_seconds=0.5,
        )


class CooldownOnlyModule(BaseModule):
    MODULE_ID = "cooldown_only"
    VERSION = "1.0.0"

    def get_manifest(self) -> ModuleManifest:
        return ModuleManifest(module_id=self.MODULE_ID, version=self.VERSION, description="")

    def policy_rules(self) -> ModulePolicy:
        return ModulePolicy(cooldown_seconds=0.3)


class ConcurrencyOnlyModule(BaseModule):
    MODULE_ID = "concurrency_only"
    VERSION = "1.0.0"

    def get_manifest(self) -> ModuleManifest:
        return ModuleManifest(module_id=self.MODULE_ID, version=self.VERSION, description="")

    def policy_rules(self) -> ModulePolicy:
        return ModulePolicy(max_parallel_calls=1)


@pytest.fixture()
def registry() -> ModuleRegistry:
    reg = ModuleRegistry()
    reg.register_instance(UnlimitedModule())
    reg.register_instance(RateLimitedModule())
    reg.register_instance(CooldownOnlyModule())
    reg.register_instance(ConcurrencyOnlyModule())
    return reg


@pytest.fixture()
def enforcer(registry: ModuleRegistry) -> PolicyEnforcer:
    return PolicyEnforcer(registry)


# ---------------------------------------------------------------------------
# Basic operations
# ---------------------------------------------------------------------------

class TestPolicyEnforcerBasic:
    def test_load_policy_default(self, enforcer):
        policy = enforcer.load_policy("unlimited")
        assert policy.max_parallel_calls == 0
        assert policy.cooldown_seconds == 0.0

    def test_load_policy_custom(self, enforcer):
        policy = enforcer.load_policy("rate_limited")
        assert policy.max_parallel_calls == 2
        assert policy.cooldown_seconds == 0.5

    def test_load_policy_cached(self, enforcer):
        p1 = enforcer.load_policy("unlimited")
        p2 = enforcer.load_policy("unlimited")
        assert p1 is p2

    @pytest.mark.asyncio
    async def test_check_and_acquire_unlimited(self, enforcer):
        """Unlimited module should always succeed."""
        await enforcer.check_and_acquire("unlimited", "any_action")
        enforcer.release("unlimited")

    @pytest.mark.asyncio
    async def test_release_unknown_module(self, enforcer):
        """Release on an untracked module should not error."""
        enforcer.release("nonexistent")

    def test_status_empty(self, enforcer):
        assert enforcer.status() == {}

    @pytest.mark.asyncio
    async def test_status_after_acquire(self, enforcer):
        await enforcer.check_and_acquire("rate_limited", "action")
        status = enforcer.status()
        assert "rate_limited" in status
        assert status["rate_limited"]["active_calls"] == 1
        assert status["rate_limited"]["max_parallel_calls"] == 2
        enforcer.release("rate_limited")

    def test_reset(self, enforcer):
        enforcer.load_policy("rate_limited")
        assert "rate_limited" in enforcer._policies
        enforcer.reset("rate_limited")
        assert "rate_limited" not in enforcer._policies
        assert "rate_limited" not in enforcer._states


# ---------------------------------------------------------------------------
# Cooldown enforcement
# ---------------------------------------------------------------------------

class TestCooldownEnforcement:
    @pytest.mark.asyncio
    async def test_cooldown_violation(self, enforcer):
        """Second call within cooldown period should raise."""
        await enforcer.check_and_acquire("cooldown_only", "action")
        enforcer.release("cooldown_only")

        with pytest.raises(PolicyViolationError, match="Cooldown"):
            await enforcer.check_and_acquire("cooldown_only", "action")

    @pytest.mark.asyncio
    async def test_cooldown_passes_after_wait(self, enforcer):
        """After waiting the cooldown period, the call should succeed."""
        await enforcer.check_and_acquire("cooldown_only", "action")
        enforcer.release("cooldown_only")

        await asyncio.sleep(0.35)  # Cooldown is 0.3s

        # Should not raise.
        await enforcer.check_and_acquire("cooldown_only", "action")
        enforcer.release("cooldown_only")

    @pytest.mark.asyncio
    async def test_first_call_never_triggers_cooldown(self, enforcer):
        """The very first call should never hit cooldown."""
        await enforcer.check_and_acquire("cooldown_only", "action")
        enforcer.release("cooldown_only")

    @pytest.mark.asyncio
    async def test_cooldown_error_includes_remaining(self, enforcer):
        await enforcer.check_and_acquire("cooldown_only", "action")
        enforcer.release("cooldown_only")

        try:
            await enforcer.check_and_acquire("cooldown_only", "action")
        except PolicyViolationError as e:
            assert "remaining" in e.violation
            assert e.module_id == "cooldown_only"


# ---------------------------------------------------------------------------
# Concurrency enforcement
# ---------------------------------------------------------------------------

class TestConcurrencyEnforcement:
    @pytest.mark.asyncio
    async def test_single_concurrent_succeeds(self, enforcer):
        await enforcer.check_and_acquire("concurrency_only", "action")
        status = enforcer.status()
        assert status["concurrency_only"]["active_calls"] == 1
        enforcer.release("concurrency_only")

    @pytest.mark.asyncio
    async def test_concurrent_limit_blocks(self, enforcer):
        """When max_parallel_calls=1, second call blocks until first releases."""
        await enforcer.check_and_acquire("concurrency_only", "action")

        released = False

        async def delayed_release():
            nonlocal released
            await asyncio.sleep(0.1)
            enforcer.release("concurrency_only")
            released = True

        asyncio.create_task(delayed_release())
        await enforcer.check_and_acquire("concurrency_only", "action2")
        assert released
        enforcer.release("concurrency_only")

    @pytest.mark.asyncio
    async def test_max_two_concurrent(self, enforcer):
        """rate_limited module allows 2 concurrent calls."""
        await enforcer.check_and_acquire("rate_limited", "a1")
        # Wait past cooldown so the second acquire doesn't fail on cooldown.
        await asyncio.sleep(0.55)
        await enforcer.check_and_acquire("rate_limited", "a2")

        status = enforcer.status()
        assert status["rate_limited"]["active_calls"] == 2

        enforcer.release("rate_limited")
        enforcer.release("rate_limited")

    @pytest.mark.asyncio
    async def test_release_decrements_active_calls(self, enforcer):
        await enforcer.check_and_acquire("concurrency_only", "action")
        assert enforcer.status()["concurrency_only"]["active_calls"] == 1

        enforcer.release("concurrency_only")
        assert enforcer.status()["concurrency_only"]["active_calls"] == 0

    @pytest.mark.asyncio
    async def test_release_does_not_go_below_zero(self, enforcer):
        await enforcer.check_and_acquire("concurrency_only", "action")
        enforcer.release("concurrency_only")
        enforcer.release("concurrency_only")  # Extra release
        assert enforcer.status()["concurrency_only"]["active_calls"] == 0


# ---------------------------------------------------------------------------
# Combined cooldown + concurrency
# ---------------------------------------------------------------------------

class TestCombinedPolicy:
    @pytest.mark.asyncio
    async def test_cooldown_checked_before_concurrency(self, enforcer):
        """Cooldown should be checked first — rejects fast before blocking."""
        await enforcer.check_and_acquire("rate_limited", "action")
        enforcer.release("rate_limited")

        # Immediately try again — should fail on cooldown.
        with pytest.raises(PolicyViolationError, match="Cooldown"):
            await enforcer.check_and_acquire("rate_limited", "action")


# ---------------------------------------------------------------------------
# Graceful degradation
# ---------------------------------------------------------------------------

class TestGracefulDegradation:
    @pytest.mark.asyncio
    async def test_no_policy_means_no_enforcement(self, enforcer):
        """Modules with default (empty) policy have no restrictions."""
        for _ in range(10):
            await enforcer.check_and_acquire("unlimited", "action")
        # All 10 should be active
        assert enforcer.status()["unlimited"]["active_calls"] == 10
        for _ in range(10):
            enforcer.release("unlimited")


# ---------------------------------------------------------------------------
# PolicyViolationError
# ---------------------------------------------------------------------------

class TestPolicyViolationError:
    def test_error_attributes(self):
        err = PolicyViolationError(
            module_id="test_mod",
            violation="Cooldown: 0.5s remaining",
        )
        assert err.module_id == "test_mod"
        assert err.violation == "Cooldown: 0.5s remaining"
        assert "test_mod" in str(err)
        assert "Cooldown" in str(err)

    def test_error_is_module_error(self):
        from llmos_bridge.exceptions import ModuleError
        err = PolicyViolationError(module_id="x", violation="test")
        assert isinstance(err, ModuleError)
