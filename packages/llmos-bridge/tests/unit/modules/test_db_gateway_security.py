"""Tests — Database Gateway module security decorator coverage."""
from __future__ import annotations
import pytest
from llmos_bridge.modules.database_gateway.module import DatabaseGatewayModule
from llmos_bridge.security.decorators import collect_security_metadata


class TestDbGatewaySecurity:
    def setup_method(self):
        self.module = object.__new__(DatabaseGatewayModule)
        # Minimal init without triggering SQLAlchemy import
        self.module._max_connections = 10
        self.module._schema_cache_ttl = 300
        self.module._connection_adapters = {}
        self.module._adapter_instances = {}
        self.module._security = None

    def test_connect_requires_database_read(self):
        meta = collect_security_metadata(self.module._action_connect)
        assert "data.database.read" in meta.get("permissions", [])

    def test_introspect_requires_database_read(self):
        meta = collect_security_metadata(self.module._action_introspect)
        assert "data.database.read" in meta.get("permissions", [])

    def test_find_requires_database_read(self):
        meta = collect_security_metadata(self.module._action_find)
        assert "data.database.read" in meta.get("permissions", [])

    def test_create_requires_database_write_and_audit(self):
        meta = collect_security_metadata(self.module._action_create)
        assert "data.database.write" in meta.get("permissions", [])
        assert meta.get("audit_level") == "standard"
        assert meta.get("rate_limit", {}).get("calls_per_minute") == 60

    def test_update_requires_database_write_and_audit(self):
        meta = collect_security_metadata(self.module._action_update)
        assert "data.database.write" in meta.get("permissions", [])
        assert meta.get("audit_level") == "standard"

    def test_delete_requires_database_delete_and_sensitive(self):
        meta = collect_security_metadata(self.module._action_delete)
        assert "data.database.delete" in meta.get("permissions", [])
        assert meta.get("risk_level") == "high"
        assert meta.get("irreversible") is True
        assert meta.get("audit_level") == "detailed"
        assert meta.get("rate_limit", {}).get("calls_per_minute") == 60

    def test_disconnect_has_no_permissions(self):
        meta = collect_security_metadata(self.module._action_disconnect)
        assert "permissions" not in meta

    def test_aggregate_requires_database_read(self):
        meta = collect_security_metadata(self.module._action_aggregate)
        assert "data.database.read" in meta.get("permissions", [])
