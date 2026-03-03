"""Trust tier system for community modules.

Defines a 4-tier graduated trust model that maps to security constraints.
Trust tiers are computed automatically during installation (based on scan
score and signature status) and can be overridden by administrators.

Tiers::

    unverified  →  Default for new installs.  Strict sandbox.
    verified    →  Passed scanning or signature verified.  Basic sandbox.
    trusted     →  Signed AND high scan score.  Minimal restrictions.
    official    →  LLMOS team maintained.  System-only assignment.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class TrustTier(str, Enum):
    """Trust tier for a community module."""

    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    TRUSTED = "trusted"
    OFFICIAL = "official"


# System modules managed by the LLMOS team — only these can be ``official``.
OFFICIAL_MODULE_IDS: frozenset[str] = frozenset({
    "filesystem",
    "os_exec",
    "database",
    "database_gateway",
    "browser",
    "gui",
    "computer_control",
    "excel",
    "word",
    "powerpoint",
    "api_http",
    "iot",
    "recording",
    "triggers",
    "security",
    "window_tracker",
    "perception_vision",
    "module_manager",
})


@dataclass(frozen=True)
class TrustConstraints:
    """Security constraints applied based on a module's trust tier.

    Attributes:
        default_sandbox_level: Sandbox level assigned at install time.
        auto_grant_permissions: Whether LOW-risk permissions are auto-granted.
        requires_manual_review: Whether the install requires admin confirmation.
    """

    default_sandbox_level: str
    auto_grant_permissions: bool
    requires_manual_review: bool


class TrustPolicy:
    """Maps trust tiers to security constraints and computes tiers."""

    TIER_CONSTRAINTS: dict[TrustTier, TrustConstraints] = {
        TrustTier.UNVERIFIED: TrustConstraints(
            default_sandbox_level="strict",
            auto_grant_permissions=False,
            requires_manual_review=False,
        ),
        TrustTier.VERIFIED: TrustConstraints(
            default_sandbox_level="basic",
            auto_grant_permissions=False,
            requires_manual_review=False,
        ),
        TrustTier.TRUSTED: TrustConstraints(
            default_sandbox_level="basic",
            auto_grant_permissions=True,
            requires_manual_review=False,
        ),
        TrustTier.OFFICIAL: TrustConstraints(
            default_sandbox_level="none",
            auto_grant_permissions=True,
            requires_manual_review=False,
        ),
    }

    @classmethod
    def for_tier(cls, tier: TrustTier) -> TrustConstraints:
        """Return the security constraints for the given trust tier."""
        return cls.TIER_CONSTRAINTS[tier]

    @classmethod
    def compute_tier(
        cls,
        scan_score: float,
        signature_verified: bool = False,
        publisher_known: bool = False,
        module_id: str = "",
    ) -> TrustTier:
        """Automatically compute a trust tier from scan and signature data.

        Rules (evaluated top to bottom, first match wins):

        - **official**: module_id is in :data:`OFFICIAL_MODULE_IDS`
        - **trusted**: signature verified AND scan_score >= 90
        - **verified**: signature verified OR scan_score >= 70
        - **unverified**: everything else

        Args:
            scan_score: Source code scan score (0-100).
            signature_verified: Whether the module signature was verified.
            publisher_known: Whether the publisher identity is confirmed.
            module_id: The module ID (used for official tier check).

        Returns:
            The computed :class:`TrustTier`.
        """
        if module_id and module_id in OFFICIAL_MODULE_IDS:
            return TrustTier.OFFICIAL

        if signature_verified and scan_score >= 90.0:
            return TrustTier.TRUSTED

        if signature_verified or scan_score >= 70.0:
            return TrustTier.VERIFIED

        return TrustTier.UNVERIFIED

    @classmethod
    def validate_tier(cls, tier_str: str) -> TrustTier:
        """Parse and validate a trust tier string.

        Raises:
            ValueError: If the string is not a valid trust tier.
        """
        try:
            return TrustTier(tier_str.lower())
        except ValueError:
            valid = ", ".join(t.value for t in TrustTier)
            raise ValueError(
                f"Invalid trust tier '{tier_str}'. Valid tiers: {valid}"
            )

    @classmethod
    def is_api_assignable(cls, tier: TrustTier) -> bool:
        """Check if a tier can be assigned via the REST API.

        The ``official`` tier is reserved for system assignment only.
        """
        return tier != TrustTier.OFFICIAL
