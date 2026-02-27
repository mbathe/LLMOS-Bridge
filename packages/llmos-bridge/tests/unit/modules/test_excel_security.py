"""Tests — Excel module security decorator coverage."""
from __future__ import annotations

import pytest

from llmos_bridge.modules.excel.module import ExcelModule
from llmos_bridge.security.decorators import collect_security_metadata


class TestExcelSecurity:
    def setup_method(self):
        self.module = ExcelModule()

    def test_create_workbook_requires_write_permission(self):
        meta = collect_security_metadata(self.module._action_create_workbook)
        assert "filesystem.write" in meta.get("permissions", [])

    def test_write_cell_requires_write_permission(self):
        meta = collect_security_metadata(self.module._action_write_cell)
        assert "filesystem.write" in meta.get("permissions", [])

    def test_write_range_requires_write_permission(self):
        meta = collect_security_metadata(self.module._action_write_range)
        assert "filesystem.write" in meta.get("permissions", [])

    def test_save_workbook_has_audit_trail(self):
        meta = collect_security_metadata(self.module._action_save_workbook)
        assert meta.get("audit_level") == "standard"
        assert "filesystem.write" in meta.get("permissions", [])

    def test_delete_sheet_requires_write_permission(self):
        meta = collect_security_metadata(self.module._action_delete_sheet)
        assert "filesystem.write" in meta.get("permissions", [])

    def test_merge_cells_requires_write_permission(self):
        meta = collect_security_metadata(self.module._action_merge_cells)
        assert "filesystem.write" in meta.get("permissions", [])

    def test_create_chart_requires_write_permission(self):
        meta = collect_security_metadata(self.module._action_create_chart)
        assert "filesystem.write" in meta.get("permissions", [])

    def test_readonly_actions_have_no_permissions(self):
        readonly_actions = [
            "_action_open_workbook",
            "_action_get_workbook_info",
            "_action_list_sheets",
            "_action_get_sheet_info",
            "_action_read_cell",
            "_action_read_range",
        ]
        for action_name in readonly_actions:
            fn = getattr(self.module, action_name)
            meta = collect_security_metadata(fn)
            assert "permissions" not in meta, f"{action_name} should not have permissions"
