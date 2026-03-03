"""Tests for hub.trust — TrustTier, TrustPolicy, TrustConstraints."""

from __future__ import annotations

import pytest

from llmos_bridge.hub.trust import (
    TrustTier,
    TrustConstraints,
    TrustPolicy,
    OFFICIAL_MODULE_IDS,
)


class TestTrustTier:
    def test_enum_values(self):
        assert TrustTier.UNVERIFIED == "unverified"
        assert TrustTier.VERIFIED == "verified"
        assert TrustTier.TRUSTED == "trusted"
        assert TrustTier.OFFICIAL == "official"

    def test_enum_from_string(self):
        assert TrustTier("unverified") == TrustTier.UNVERIFIED
        assert TrustTier("official") == TrustTier.OFFICIAL

    def test_invalid_string_raises(self):
        with pytest.raises(ValueError):
            TrustTier("invalid")


class TestTrustConstraints:
    def test_unverified_is_strict(self):
        c = TrustPolicy.for_tier(TrustTier.UNVERIFIED)
        assert c.default_sandbox_level == "strict"
        assert c.auto_grant_permissions is False

    def test_verified_is_basic(self):
        c = TrustPolicy.for_tier(TrustTier.VERIFIED)
        assert c.default_sandbox_level == "basic"
        assert c.auto_grant_permissions is False

    def test_trusted_auto_grants(self):
        c = TrustPolicy.for_tier(TrustTier.TRUSTED)
        assert c.auto_grant_permissions is True

    def test_official_no_sandbox(self):
        c = TrustPolicy.for_tier(TrustTier.OFFICIAL)
        assert c.default_sandbox_level == "none"
        assert c.auto_grant_permissions is True

    def test_constraints_are_frozen(self):
        c = TrustPolicy.for_tier(TrustTier.UNVERIFIED)
        with pytest.raises(AttributeError):
            c.default_sandbox_level = "none"


class TestComputeTier:
    def test_official_module(self):
        tier = TrustPolicy.compute_tier(
            scan_score=100.0,
            signature_verified=True,
            module_id="filesystem",
        )
        assert tier == TrustTier.OFFICIAL

    def test_official_by_module_id_only(self):
        """Official tier is based on module_id, not scan/signature."""
        tier = TrustPolicy.compute_tier(
            scan_score=0.0,
            signature_verified=False,
            module_id="os_exec",
        )
        assert tier == TrustTier.OFFICIAL

    def test_trusted_signature_and_high_score(self):
        tier = TrustPolicy.compute_tier(
            scan_score=95.0,
            signature_verified=True,
        )
        assert tier == TrustTier.TRUSTED

    def test_trusted_requires_both(self):
        """Trusted needs BOTH signature AND score >= 90."""
        tier = TrustPolicy.compute_tier(
            scan_score=95.0,
            signature_verified=False,
        )
        assert tier == TrustTier.VERIFIED  # Not trusted without signature

    def test_verified_by_signature_only(self):
        tier = TrustPolicy.compute_tier(
            scan_score=50.0,
            signature_verified=True,
        )
        assert tier == TrustTier.VERIFIED

    def test_verified_by_high_score_only(self):
        tier = TrustPolicy.compute_tier(
            scan_score=75.0,
            signature_verified=False,
        )
        assert tier == TrustTier.VERIFIED

    def test_unverified_low_score_no_signature(self):
        tier = TrustPolicy.compute_tier(
            scan_score=50.0,
            signature_verified=False,
        )
        assert tier == TrustTier.UNVERIFIED

    def test_unverified_zero_score(self):
        tier = TrustPolicy.compute_tier(scan_score=0.0)
        assert tier == TrustTier.UNVERIFIED

    def test_boundary_score_70(self):
        tier = TrustPolicy.compute_tier(scan_score=70.0)
        assert tier == TrustTier.VERIFIED

    def test_boundary_score_69(self):
        tier = TrustPolicy.compute_tier(scan_score=69.9)
        assert tier == TrustTier.UNVERIFIED

    def test_boundary_score_90_with_signature(self):
        tier = TrustPolicy.compute_tier(scan_score=90.0, signature_verified=True)
        assert tier == TrustTier.TRUSTED

    def test_boundary_score_89_with_signature(self):
        tier = TrustPolicy.compute_tier(scan_score=89.9, signature_verified=True)
        assert tier == TrustTier.VERIFIED


class TestValidateTier:
    def test_valid_string(self):
        assert TrustPolicy.validate_tier("unverified") == TrustTier.UNVERIFIED
        assert TrustPolicy.validate_tier("VERIFIED") == TrustTier.VERIFIED
        assert TrustPolicy.validate_tier("Trusted") == TrustTier.TRUSTED

    def test_invalid_raises_value_error(self):
        with pytest.raises(ValueError, match="Invalid trust tier"):
            TrustPolicy.validate_tier("super_trusted")


class TestApiAssignable:
    def test_official_not_assignable(self):
        assert TrustPolicy.is_api_assignable(TrustTier.OFFICIAL) is False

    def test_others_are_assignable(self):
        assert TrustPolicy.is_api_assignable(TrustTier.UNVERIFIED) is True
        assert TrustPolicy.is_api_assignable(TrustTier.VERIFIED) is True
        assert TrustPolicy.is_api_assignable(TrustTier.TRUSTED) is True


class TestOfficialModuleIds:
    def test_known_system_modules_present(self):
        assert "filesystem" in OFFICIAL_MODULE_IDS
        assert "os_exec" in OFFICIAL_MODULE_IDS
        assert "browser" in OFFICIAL_MODULE_IDS
        assert "security" in OFFICIAL_MODULE_IDS

    def test_community_modules_absent(self):
        assert "web_search" not in OFFICIAL_MODULE_IDS
        assert "data_pipeline" not in OFFICIAL_MODULE_IDS
