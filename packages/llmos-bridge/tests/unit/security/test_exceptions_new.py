"""Unit tests for PermissionNotGrantedError and RateLimitExceededError.

Verifies that the two new SecurityError subclasses store their fields
correctly and belong to the SecurityError hierarchy.
"""

from __future__ import annotations

import pytest

from llmos_bridge.exceptions import (
    PermissionNotGrantedError,
    RateLimitExceededError,
    SecurityError,
)


@pytest.mark.unit
class TestPermissionNotGrantedError:
    """Tests for PermissionNotGrantedError."""

    def test_is_a_security_error(self) -> None:
        err = PermissionNotGrantedError(
            permission="filesystem.write", module_id="filesystem"
        )
        assert isinstance(err, SecurityError)

    def test_stores_fields(self) -> None:
        err = PermissionNotGrantedError(
            permission="filesystem.write",
            module_id="filesystem",
            action="write_file",
            risk_level="high",
        )
        assert err.permission == "filesystem.write"
        assert err.module_id == "filesystem"
        assert err.action == "write_file"
        assert err.risk_level == "high"


@pytest.mark.unit
class TestRateLimitExceededError:
    """Tests for RateLimitExceededError."""

    def test_is_a_security_error(self) -> None:
        err = RateLimitExceededError(
            action_key="filesystem.write_file", limit=30
        )
        assert isinstance(err, SecurityError)

    def test_stores_fields(self) -> None:
        err = RateLimitExceededError(
            action_key="filesystem.write_file",
            limit=30,
            window="minute",
        )
        assert err.action_key == "filesystem.write_file"
        assert err.limit == 30
        assert err.window == "minute"
