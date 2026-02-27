"""Unit tests for the 5 new AuditEvent members and their topic routing.

Verifies that PERMISSION_GRANTED, PERMISSION_REVOKED, PERMISSION_CHECK_FAILED,
RATE_LIMIT_EXCEEDED and SENSITIVE_ACTION_INVOKED exist in the enum and are
routed to the correct EventBus topic via the _EVENT_TOPIC mapping.
"""

from __future__ import annotations

import pytest

from llmos_bridge.events.bus import TOPIC_PERMISSIONS, TOPIC_SECURITY
from llmos_bridge.security.audit import AuditEvent, _EVENT_TOPIC


@pytest.mark.unit
class TestAuditNewEventMembers:
    """Verify the 5 new AuditEvent enum members exist."""

    def test_all_five_new_members_exist(self) -> None:
        new_members = [
            "PERMISSION_GRANTED",
            "PERMISSION_REVOKED",
            "PERMISSION_CHECK_FAILED",
            "RATE_LIMIT_EXCEEDED",
            "SENSITIVE_ACTION_INVOKED",
        ]
        for name in new_members:
            assert hasattr(AuditEvent, name), f"AuditEvent.{name} missing"


@pytest.mark.unit
class TestAuditNewEventTopicRouting:
    """Verify topic routing for the 5 new AuditEvent members."""

    def test_permission_granted_routes_to_permissions(self) -> None:
        assert _EVENT_TOPIC[AuditEvent.PERMISSION_GRANTED] == TOPIC_PERMISSIONS

    def test_permission_revoked_routes_to_permissions(self) -> None:
        assert _EVENT_TOPIC[AuditEvent.PERMISSION_REVOKED] == TOPIC_PERMISSIONS

    def test_rate_limit_exceeded_routes_to_permissions(self) -> None:
        assert _EVENT_TOPIC[AuditEvent.RATE_LIMIT_EXCEEDED] == TOPIC_PERMISSIONS

    def test_sensitive_action_invoked_routes_to_security(self) -> None:
        assert _EVENT_TOPIC[AuditEvent.SENSITIVE_ACTION_INVOKED] == TOPIC_SECURITY
        assert _EVENT_TOPIC[AuditEvent.SENSITIVE_ACTION_INVOKED] != TOPIC_PERMISSIONS
