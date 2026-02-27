"""Tests — Triggers module security decorator coverage."""
from __future__ import annotations
import pytest
from llmos_bridge.modules.triggers.module import TriggerModule
from llmos_bridge.security.decorators import collect_security_metadata


class TestTriggersSecurity:
    def setup_method(self):
        self.module = TriggerModule()

    def test_register_trigger_requires_process_execute(self):
        meta = collect_security_metadata(self.module._action_register_trigger)
        assert "os.process.execute" in meta.get("permissions", [])
        assert meta.get("audit_level") == "standard"

    def test_delete_trigger_requires_process_execute_and_sensitive(self):
        meta = collect_security_metadata(self.module._action_delete_trigger)
        assert "os.process.execute" in meta.get("permissions", [])
        assert meta.get("risk_level") == "medium"
        assert meta.get("audit_level") == "standard"

    def test_activate_has_audit_trail(self):
        meta = collect_security_metadata(self.module._action_activate_trigger)
        assert meta.get("audit_level") == "standard"

    def test_deactivate_has_audit_trail(self):
        meta = collect_security_metadata(self.module._action_deactivate_trigger)
        assert meta.get("audit_level") == "standard"

    def test_readonly_actions_have_no_metadata(self):
        for action_name in ["_action_list_triggers", "_action_get_trigger"]:
            fn = getattr(self.module, action_name)
            meta = collect_security_metadata(fn)
            assert meta == {}, f"{action_name} should have no security metadata"
