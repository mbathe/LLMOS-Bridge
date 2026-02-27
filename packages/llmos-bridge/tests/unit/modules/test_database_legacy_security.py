"""Tests — Database module (legacy) security decorator coverage."""
from __future__ import annotations

import pytest

from llmos_bridge.modules.database.module import DatabaseModule
from llmos_bridge.security.decorators import collect_security_metadata


class TestDatabaseLegacySecurity:
    def setup_method(self):
        self.module = DatabaseModule()

    def test_connect_requires_database_read(self):
        meta = collect_security_metadata(self.module._action_connect)
        assert "data.database.read" in meta.get("permissions", [])

    def test_execute_query_requires_write_and_high_risk_and_detailed_audit(self):
        meta = collect_security_metadata(self.module._action_execute_query)
        assert "data.database.write" in meta.get("permissions", [])
        assert meta.get("risk_level") == "high"
        assert meta.get("audit_level") == "detailed"

    def test_insert_record_requires_write_and_standard_audit(self):
        meta = collect_security_metadata(self.module._action_insert_record)
        assert "data.database.write" in meta.get("permissions", [])
        assert meta.get("audit_level") == "standard"

    def test_delete_record_requires_delete_and_high_risk_and_detailed_audit(self):
        meta = collect_security_metadata(self.module._action_delete_record)
        assert "data.database.delete" in meta.get("permissions", [])
        assert meta.get("risk_level") == "high"
        assert meta.get("audit_level") == "detailed"

    def test_list_tables_requires_database_read(self):
        meta = collect_security_metadata(self.module._action_list_tables)
        assert "data.database.read" in meta.get("permissions", [])

    def test_begin_transaction_requires_database_write(self):
        meta = collect_security_metadata(self.module._action_begin_transaction)
        assert "data.database.write" in meta.get("permissions", [])

    def test_disconnect_has_no_permissions(self):
        meta = collect_security_metadata(self.module._action_disconnect)
        assert "permissions" not in meta

    def test_commit_and_rollback_have_audit_but_no_permissions(self):
        for action_name in ("_action_commit_transaction", "_action_rollback_transaction"):
            fn = getattr(self.module, action_name)
            meta = collect_security_metadata(fn)
            assert "permissions" not in meta, f"{action_name} should not have permissions"
            assert meta.get("audit_level") == "standard", f"{action_name} should have standard audit"
