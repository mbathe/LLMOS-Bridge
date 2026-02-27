"""Tests — Security module security decorator coverage."""
from __future__ import annotations

import pytest

from llmos_bridge.modules.security.module import SecurityModule
from llmos_bridge.security.decorators import collect_security_metadata


class TestSecurityModuleSecurity:
    def setup_method(self):
        self.module = SecurityModule()

    def test_request_permission_has_detailed_audit(self):
        meta = collect_security_metadata(self.module._action_request_permission)
        assert meta.get("audit_level") == "detailed"

    def test_revoke_permission_has_high_risk_and_detailed_audit(self):
        meta = collect_security_metadata(self.module._action_revoke_permission)
        assert meta.get("risk_level") == "high"
        assert meta.get("audit_level") == "detailed"

    def test_readonly_actions_have_no_metadata(self):
        for action_name in (
            "_action_check_permission",
            "_action_get_security_status",
            "_action_list_audit_events",
        ):
            fn = getattr(self.module, action_name)
            meta = collect_security_metadata(fn)
            assert meta == {}, f"{action_name} should have no security metadata"

    def test_list_permissions_has_no_metadata(self):
        meta = collect_security_metadata(self.module._action_list_permissions)
        assert meta == {}
