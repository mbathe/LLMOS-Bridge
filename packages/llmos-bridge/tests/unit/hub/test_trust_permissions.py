"""Tests for trust tier → permission profile mapping (Phase 2)."""

from __future__ import annotations

import pytest

from llmos_bridge.hub.trust import (
    TIER_TO_PROFILE,
    TrustConstraints,
    TrustPolicy,
    TrustTier,
)


class TestTierToProfile:
    def test_unverified_maps_to_readonly(self):
        assert TrustPolicy.permission_profile_for_tier(TrustTier.UNVERIFIED) == "readonly"

    def test_verified_maps_to_local_worker(self):
        assert TrustPolicy.permission_profile_for_tier(TrustTier.VERIFIED) == "local_worker"

    def test_trusted_maps_to_power_user(self):
        assert TrustPolicy.permission_profile_for_tier(TrustTier.TRUSTED) == "power_user"

    def test_official_maps_to_unrestricted(self):
        assert TrustPolicy.permission_profile_for_tier(TrustTier.OFFICIAL) == "unrestricted"

    def test_all_tiers_have_mapping(self):
        for tier in TrustTier:
            profile = TrustPolicy.permission_profile_for_tier(tier)
            assert isinstance(profile, str)
            assert len(profile) > 0


class TestShouldAutoGrant:
    def test_unverified_no_auto_grant(self):
        assert TrustPolicy.should_auto_grant(TrustTier.UNVERIFIED) is False

    def test_verified_no_auto_grant(self):
        assert TrustPolicy.should_auto_grant(TrustTier.VERIFIED) is False

    def test_trusted_auto_grants(self):
        assert TrustPolicy.should_auto_grant(TrustTier.TRUSTED) is True

    def test_official_auto_grants(self):
        assert TrustPolicy.should_auto_grant(TrustTier.OFFICIAL) is True


class TestTrustConstraintsSandboxLevel:
    def test_unverified_strict_sandbox(self):
        constraints = TrustPolicy.for_tier(TrustTier.UNVERIFIED)
        assert constraints.default_sandbox_level == "strict"

    def test_verified_basic_sandbox(self):
        constraints = TrustPolicy.for_tier(TrustTier.VERIFIED)
        assert constraints.default_sandbox_level == "basic"

    def test_trusted_basic_sandbox(self):
        constraints = TrustPolicy.for_tier(TrustTier.TRUSTED)
        assert constraints.default_sandbox_level == "basic"

    def test_official_no_sandbox(self):
        constraints = TrustPolicy.for_tier(TrustTier.OFFICIAL)
        assert constraints.default_sandbox_level == "none"


class TestComputeTierWithSignature:
    def test_high_score_plus_signature_gives_trusted(self):
        tier = TrustPolicy.compute_tier(scan_score=95.0, signature_verified=True)
        assert tier == TrustTier.TRUSTED

    def test_low_score_plus_signature_gives_verified(self):
        tier = TrustPolicy.compute_tier(scan_score=50.0, signature_verified=True)
        assert tier == TrustTier.VERIFIED

    def test_high_score_no_signature_gives_verified(self):
        tier = TrustPolicy.compute_tier(scan_score=80.0, signature_verified=False)
        assert tier == TrustTier.VERIFIED

    def test_low_score_no_signature_gives_unverified(self):
        tier = TrustPolicy.compute_tier(scan_score=40.0, signature_verified=False)
        assert tier == TrustTier.UNVERIFIED

    def test_official_module_always_official(self):
        tier = TrustPolicy.compute_tier(
            scan_score=0.0, signature_verified=False, module_id="filesystem"
        )
        assert tier == TrustTier.OFFICIAL
