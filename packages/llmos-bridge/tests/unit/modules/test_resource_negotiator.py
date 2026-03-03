"""Tests for Module Spec v3 — Dynamic resource negotiation.

Tests the ResourceNegotiator: estimate_cost integration, limit checking,
resource tracking, and negotiation results.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from llmos_bridge.modules.base import BaseModule, Platform, ResourceEstimate
from llmos_bridge.modules.manifest import (
    ActionSpec,
    ModuleManifest,
    ResourceLimits,
)
from llmos_bridge.modules.resource_negotiator import (
    NegotiationResult,
    ResourceNegotiator,
    ResourceRequest,
)


# ---------------------------------------------------------------------------
# Test module with resource awareness
# ---------------------------------------------------------------------------


class HeavyModule(BaseModule):
    MODULE_ID = "heavy"
    VERSION = "1.0.0"
    SUPPORTED_PLATFORMS = [Platform.ALL]

    async def estimate_cost(self, action: str, params: dict) -> ResourceEstimate:
        if action == "train_model":
            return ResourceEstimate(
                estimated_duration_seconds=300.0,
                estimated_memory_mb=4096.0,
                estimated_cpu_percent=95.0,
                confidence=0.85,
            )
        elif action == "small_query":
            return ResourceEstimate(
                estimated_duration_seconds=0.1,
                estimated_memory_mb=10.0,
                confidence=0.95,
            )
        return ResourceEstimate()

    def get_manifest(self) -> ModuleManifest:
        return ModuleManifest(
            module_id=self.MODULE_ID,
            version=self.VERSION,
            description="Heavy computation module",
            resource_limits=ResourceLimits(
                max_memory_mb=2048,
                max_execution_seconds=600.0,
                max_concurrent_actions=2,
            ),
            actions=[
                ActionSpec(name="train_model", description="Train a model"),
                ActionSpec(name="small_query", description="Small query"),
            ],
        )


class LightModule(BaseModule):
    MODULE_ID = "light"
    VERSION = "1.0.0"
    SUPPORTED_PLATFORMS = [Platform.ALL]

    def get_manifest(self) -> ModuleManifest:
        return ModuleManifest(
            module_id=self.MODULE_ID,
            version=self.VERSION,
            description="Light module (no limits)",
            actions=[
                ActionSpec(name="ping", description="Ping"),
            ],
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestResourceNegotiator:
    @pytest.fixture()
    def registry(self):
        from llmos_bridge.modules.registry import ModuleRegistry

        r = ModuleRegistry()
        r.register_instance(HeavyModule())
        r.register_instance(LightModule())
        return r

    @pytest.fixture()
    def negotiator(self, registry):
        return ResourceNegotiator(registry)

    @pytest.mark.asyncio
    async def test_grant_within_limits(self, negotiator):
        result = await negotiator.negotiate("heavy", "small_query", {})
        assert result.granted is True
        assert result.adjusted_estimate is not None
        assert result.adjusted_estimate.estimated_memory_mb == 10.0

    @pytest.mark.asyncio
    async def test_deny_exceeds_memory(self, negotiator):
        result = await negotiator.negotiate("heavy", "train_model", {})
        # 4096 MB > 2048 MB limit, confidence > 0.7 → defer.
        assert result.granted is False
        assert result.defer is True
        assert "Memory limit exceeded" in result.reason

    @pytest.mark.asyncio
    async def test_grant_no_limits_module(self, negotiator):
        result = await negotiator.negotiate("light", "ping", {})
        assert result.granted is True

    @pytest.mark.asyncio
    async def test_grant_unknown_module(self, negotiator):
        result = await negotiator.negotiate("ghost", "whatever", {})
        assert result.granted is True

    @pytest.mark.asyncio
    async def test_resource_tracking(self, negotiator):
        est = ResourceEstimate(estimated_memory_mb=100.0, estimated_duration_seconds=5.0)
        negotiator.acquire("heavy", est)

        status = negotiator.status()
        assert "heavy" in status
        assert status["heavy"]["memory_mb"] == 100.0
        assert status["heavy"]["duration_s"] == 5.0

    @pytest.mark.asyncio
    async def test_resource_release(self, negotiator):
        est = ResourceEstimate(estimated_memory_mb=100.0, estimated_duration_seconds=5.0)
        negotiator.acquire("heavy", est)
        negotiator.release("heavy", est)

        status = negotiator.status()
        assert status["heavy"]["memory_mb"] == 0.0
        assert status["heavy"]["duration_s"] == 0.0

    @pytest.mark.asyncio
    async def test_release_below_zero_clamped(self, negotiator):
        est = ResourceEstimate(estimated_memory_mb=100.0)
        negotiator.release("heavy", est)

        status = negotiator.status()
        assert status["heavy"]["memory_mb"] == 0.0

    @pytest.mark.asyncio
    async def test_empty_status(self, negotiator):
        status = negotiator.status()
        assert status == {}


class TestNegotiationResult:
    def test_default_granted(self):
        result = NegotiationResult()
        assert result.granted is True
        assert result.defer is False
        assert result.retry_after == 0.0

    def test_deferred(self):
        result = NegotiationResult(granted=False, defer=True, retry_after=5.0)
        assert not result.granted
        assert result.defer
        assert result.retry_after == 5.0

    def test_denied(self):
        result = NegotiationResult(granted=False, reason="Over limit")
        assert not result.granted
        assert result.reason == "Over limit"


class TestResourceRequest:
    def test_basic_request(self):
        req = ResourceRequest(
            module_id="heavy",
            action="train_model",
            estimate=ResourceEstimate(estimated_memory_mb=4096.0),
        )
        assert req.module_id == "heavy"
        assert req.action == "train_model"
        assert req.estimate.estimated_memory_mb == 4096.0
