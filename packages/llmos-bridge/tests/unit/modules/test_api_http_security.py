"""Tests — API HTTP module security decorator coverage."""
from __future__ import annotations
import pytest
from llmos_bridge.modules.api_http.module import ApiHttpModule
from llmos_bridge.security.decorators import collect_security_metadata


class TestApiHttpSecurity:
    def setup_method(self):
        self.module = object.__new__(ApiHttpModule)
        self.module._sessions = {}
        self.module._security = None

    def test_http_get_requires_network_read(self):
        meta = collect_security_metadata(self.module._action_http_get)
        assert "network.read" in meta.get("permissions", [])

    def test_http_head_requires_network_read(self):
        meta = collect_security_metadata(self.module._action_http_head)
        assert "network.read" in meta.get("permissions", [])

    def test_http_post_requires_network_send_and_rate_limited(self):
        meta = collect_security_metadata(self.module._action_http_post)
        assert "network.send" in meta.get("permissions", [])
        assert meta.get("rate_limit", {}).get("calls_per_minute") == 30

    def test_http_put_requires_network_send_and_rate_limited(self):
        meta = collect_security_metadata(self.module._action_http_put)
        assert "network.send" in meta.get("permissions", [])
        assert meta.get("rate_limit", {}).get("calls_per_minute") == 30

    def test_http_delete_requires_network_send_and_sensitive(self):
        meta = collect_security_metadata(self.module._action_http_delete)
        assert "network.send" in meta.get("permissions", [])
        assert meta.get("risk_level") == "medium"
        assert meta.get("rate_limit", {}).get("calls_per_minute") == 30

    def test_download_file_requires_network_read(self):
        meta = collect_security_metadata(self.module._action_download_file)
        assert "network.read" in meta.get("permissions", [])

    def test_oauth2_requires_network_send_and_credentials(self):
        meta = collect_security_metadata(self.module._action_oauth2_get_token)
        perms = meta.get("permissions", [])
        assert "network.send" in perms
        assert "data.credentials" in perms
        assert meta.get("risk_level") == "high"
        assert meta.get("data_classification") == "confidential"

    def test_send_email_requires_email_send_and_sensitive(self):
        meta = collect_security_metadata(self.module._action_send_email)
        assert "app.email.send" in meta.get("permissions", [])
        assert meta.get("risk_level") == "high"
        assert meta.get("irreversible") is True
        assert meta.get("audit_level") == "detailed"
        assert meta.get("rate_limit", {}).get("calls_per_minute") == 30

    def test_read_email_requires_email_read(self):
        meta = collect_security_metadata(self.module._action_read_email)
        assert "app.email.read" in meta.get("permissions", [])
        assert meta.get("data_classification") == "confidential"

    def test_webhook_trigger_requires_network_send_and_audit(self):
        meta = collect_security_metadata(self.module._action_webhook_trigger)
        assert "network.send" in meta.get("permissions", [])
        assert meta.get("audit_level") == "standard"
