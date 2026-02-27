"""Tests — @rate_limited decorator applied to I/O-heavy actions."""
from __future__ import annotations
import pytest
from llmos_bridge.security.decorators import collect_security_metadata


class TestRateLimitedApplied:
    def test_filesystem_write_file_rate_limited(self):
        from llmos_bridge.modules.filesystem.module import FilesystemModule
        module = FilesystemModule()
        meta = collect_security_metadata(module._action_write_file)
        assert meta.get("rate_limit", {}).get("calls_per_minute") == 60

    def test_filesystem_delete_file_rate_limited(self):
        from llmos_bridge.modules.filesystem.module import FilesystemModule
        module = FilesystemModule()
        meta = collect_security_metadata(module._action_delete_file)
        assert meta.get("rate_limit", {}).get("calls_per_minute") == 60

    def test_os_exec_run_command_rate_limited(self):
        from llmos_bridge.modules.os_exec.module import OSExecModule
        module = OSExecModule()
        meta = collect_security_metadata(module._action_run_command)
        assert meta.get("rate_limit", {}).get("calls_per_minute") == 30

    def test_db_gateway_create_rate_limited(self):
        from llmos_bridge.modules.database_gateway.module import DatabaseGatewayModule
        module = object.__new__(DatabaseGatewayModule)
        module._max_connections = 10
        module._schema_cache_ttl = 300
        module._connection_adapters = {}
        module._adapter_instances = {}
        meta = collect_security_metadata(module._action_create)
        assert meta.get("rate_limit", {}).get("calls_per_minute") == 60

    def test_api_http_post_rate_limited(self):
        from llmos_bridge.modules.api_http.module import ApiHttpModule
        module = object.__new__(ApiHttpModule)
        module._sessions = {}
        meta = collect_security_metadata(module._action_http_post)
        assert meta.get("rate_limit", {}).get("calls_per_minute") == 30

    def test_gui_click_rate_limited(self):
        from unittest.mock import patch
        from llmos_bridge.modules.gui.module import GUIModule
        with patch.object(GUIModule, "_check_dependencies"):
            module = GUIModule()
        meta = collect_security_metadata(module._action_click_position)
        assert meta.get("rate_limit", {}).get("calls_per_minute") == 120

    def test_gui_type_text_rate_limited(self):
        from unittest.mock import patch
        from llmos_bridge.modules.gui.module import GUIModule
        with patch.object(GUIModule, "_check_dependencies"):
            module = GUIModule()
        meta = collect_security_metadata(module._action_type_text)
        assert meta.get("rate_limit", {}).get("calls_per_minute") == 120

    def test_filesystem_read_file_not_rate_limited(self):
        from llmos_bridge.modules.filesystem.module import FilesystemModule
        module = FilesystemModule()
        meta = collect_security_metadata(module._action_read_file)
        assert "rate_limit" not in meta
