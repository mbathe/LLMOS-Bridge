"""Unit tests — security.models (Permission, RiskLevel, DataClassification, PermissionScope, PermissionGrant, PERMISSION_RISK)."""

import time

import pytest

from llmos_bridge.security.models import (
    PERMISSION_RISK,
    DataClassification,
    Permission,
    PermissionGrant,
    PermissionScope,
    RiskLevel,
)


@pytest.mark.unit
class TestPermissionConstants:
    """Permission class exposes well-known permission identifiers as plain strings."""

    def test_filesystem_read(self) -> None:
        assert Permission.FILESYSTEM_READ == "filesystem.read"
        assert isinstance(Permission.FILESYSTEM_READ, str)

    def test_filesystem_write(self) -> None:
        assert Permission.FILESYSTEM_WRITE == "filesystem.write"

    def test_process_execute(self) -> None:
        assert Permission.PROCESS_EXECUTE == "os.process.execute"

    def test_credentials(self) -> None:
        assert Permission.CREDENTIALS == "data.credentials"

    def test_gpio_write(self) -> None:
        assert Permission.GPIO_WRITE == "iot.gpio.write"


@pytest.mark.unit
class TestRiskLevel:
    """RiskLevel is a str enum with four ordered severity levels."""

    def test_values(self) -> None:
        assert RiskLevel.LOW.value == "low"
        assert RiskLevel.MEDIUM.value == "medium"
        assert RiskLevel.HIGH.value == "high"
        assert RiskLevel.CRITICAL.value == "critical"

    def test_is_string(self) -> None:
        """RiskLevel members can be used as plain strings."""
        assert isinstance(RiskLevel.LOW, str)
        assert RiskLevel.HIGH == "high"


@pytest.mark.unit
class TestDataClassification:
    """DataClassification is a str enum with four sensitivity tiers."""

    def test_values(self) -> None:
        assert DataClassification.PUBLIC.value == "public"
        assert DataClassification.INTERNAL.value == "internal"
        assert DataClassification.CONFIDENTIAL.value == "confidential"
        assert DataClassification.RESTRICTED.value == "restricted"


@pytest.mark.unit
class TestPermissionScope:
    """PermissionScope distinguishes session-scoped from permanent grants."""

    def test_values(self) -> None:
        assert PermissionScope.SESSION.value == "session"
        assert PermissionScope.PERMANENT.value == "permanent"


@pytest.mark.unit
class TestPermissionGrant:
    """PermissionGrant is a frozen dataclass recording a granted permission."""

    def test_construction_and_defaults(self) -> None:
        before = time.time()
        grant = PermissionGrant(
            permission=Permission.FILESYSTEM_READ,
            module_id="filesystem",
            scope=PermissionScope.SESSION,
        )
        after = time.time()

        assert grant.permission == "filesystem.read"
        assert grant.module_id == "filesystem"
        assert grant.scope is PermissionScope.SESSION
        assert grant.granted_by == "user"
        assert grant.reason == ""
        assert grant.expires_at is None
        assert before <= grant.granted_at <= after

    def test_to_dict(self) -> None:
        grant = PermissionGrant(
            permission=Permission.NETWORK_SEND,
            module_id="api_http",
            scope=PermissionScope.PERMANENT,
            granted_at=1000.0,
            granted_by="admin",
            reason="needed for API calls",
            expires_at=2000.0,
        )
        d = grant.to_dict()

        assert d == {
            "permission": "network.send",
            "module_id": "api_http",
            "scope": "permanent",
            "granted_at": 1000.0,
            "granted_by": "admin",
            "reason": "needed for API calls",
            "expires_at": 2000.0,
        }

    def test_is_expired_when_past(self) -> None:
        grant = PermissionGrant(
            permission=Permission.CAMERA,
            module_id="perception",
            scope=PermissionScope.SESSION,
            expires_at=0.0,  # epoch — always in the past
        )
        assert grant.is_expired() is True

    def test_is_expired_when_future(self) -> None:
        grant = PermissionGrant(
            permission=Permission.CAMERA,
            module_id="perception",
            scope=PermissionScope.SESSION,
            expires_at=time.time() + 3600,
        )
        assert grant.is_expired() is False


@pytest.mark.unit
class TestPermissionRisk:
    """PERMISSION_RISK maps all well-known permissions to a RiskLevel."""

    def test_has_expected_entries(self) -> None:
        assert len(PERMISSION_RISK) == 26

    def test_spot_check_mappings(self) -> None:
        assert PERMISSION_RISK[Permission.FILESYSTEM_READ] is RiskLevel.LOW
        assert PERMISSION_RISK[Permission.FILESYSTEM_DELETE] is RiskLevel.HIGH
        assert PERMISSION_RISK[Permission.KEYBOARD] is RiskLevel.CRITICAL
        assert PERMISSION_RISK[Permission.ADMIN] is RiskLevel.CRITICAL
        assert PERMISSION_RISK[Permission.ACTUATOR] is RiskLevel.HIGH
        assert PERMISSION_RISK[Permission.SCREEN_CAPTURE] is RiskLevel.MEDIUM
