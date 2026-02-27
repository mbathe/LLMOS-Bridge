"""Tests — PowerPoint module security decorator coverage."""
from __future__ import annotations

import pytest

from llmos_bridge.modules.powerpoint.module import PowerPointModule
from llmos_bridge.security.decorators import collect_security_metadata


class TestPowerPointSecurity:
    def setup_method(self):
        self.module = PowerPointModule()

    def test_create_presentation_requires_write_permission(self):
        meta = collect_security_metadata(self.module._action_create_presentation)
        assert "filesystem.write" in meta.get("permissions", [])

    def test_add_slide_requires_write_permission(self):
        meta = collect_security_metadata(self.module._action_add_slide)
        assert "filesystem.write" in meta.get("permissions", [])

    def test_delete_slide_is_sensitive(self):
        meta = collect_security_metadata(self.module._action_delete_slide)
        assert meta.get("risk_level") == "medium"
        assert "filesystem.write" in meta.get("permissions", [])

    def test_save_presentation_has_audit_trail(self):
        meta = collect_security_metadata(self.module._action_save_presentation)
        assert meta.get("audit_level") == "standard"
        assert "filesystem.write" in meta.get("permissions", [])

    def test_add_shape_requires_write_permission(self):
        meta = collect_security_metadata(self.module._action_add_shape)
        assert "filesystem.write" in meta.get("permissions", [])

    def test_add_chart_requires_write_permission(self):
        meta = collect_security_metadata(self.module._action_add_chart)
        assert "filesystem.write" in meta.get("permissions", [])

    def test_add_table_requires_write_permission(self):
        meta = collect_security_metadata(self.module._action_add_table)
        assert "filesystem.write" in meta.get("permissions", [])

    def test_readonly_actions_have_no_permissions(self):
        readonly_actions = [
            "_action_open_presentation",
            "_action_get_presentation_info",
            "_action_list_slides",
            "_action_read_slide",
        ]
        for action_name in readonly_actions:
            fn = getattr(self.module, action_name)
            meta = collect_security_metadata(fn)
            assert "permissions" not in meta, f"{action_name} should not have permissions"
