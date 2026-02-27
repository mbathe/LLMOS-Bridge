"""Unit tests for SecurityManager dataclass aggregate.

Tests that SecurityManager correctly stores and exposes its three subsystems:
PermissionManager, ActionRateLimiter, and AuditLogger.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from llmos_bridge.security.manager import SecurityManager


@pytest.mark.unit
class TestSecurityManager:
    """Tests for SecurityManager dataclass construction and field access."""

    def _make_manager(self) -> SecurityManager:
        return SecurityManager(
            permission_manager=MagicMock(name="PermissionManager"),
            rate_limiter=MagicMock(name="ActionRateLimiter"),
            audit=MagicMock(name="AuditLogger"),
        )

    def test_construction_with_all_fields(self) -> None:
        pm = MagicMock(name="PermissionManager")
        rl = MagicMock(name="ActionRateLimiter")
        audit = MagicMock(name="AuditLogger")
        sm = SecurityManager(
            permission_manager=pm,
            rate_limiter=rl,
            audit=audit,
        )
        assert sm.permission_manager is pm
        assert sm.rate_limiter is rl
        assert sm.audit is audit

    def test_fields_are_accessible(self) -> None:
        sm = self._make_manager()
        # All three fields should be readable without error
        _ = sm.permission_manager
        _ = sm.rate_limiter
        _ = sm.audit

    def test_permission_manager_attribute_exists(self) -> None:
        sm = self._make_manager()
        assert hasattr(sm, "permission_manager")

    def test_rate_limiter_attribute_exists(self) -> None:
        sm = self._make_manager()
        assert hasattr(sm, "rate_limiter")

    def test_audit_attribute_exists(self) -> None:
        sm = self._make_manager()
        assert hasattr(sm, "audit")
