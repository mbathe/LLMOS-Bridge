"""Tests — Browser module security decorator coverage."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from llmos_bridge.modules.browser.module import BrowserModule
from llmos_bridge.security.decorators import collect_security_metadata


class TestBrowserSecurity:
    def setup_method(self):
        # Skip _check_dependencies — playwright may not be installed
        with patch.object(BrowserModule, "_check_dependencies"):
            self.module = BrowserModule()

    def test_open_browser_requires_browser_permission(self):
        meta = collect_security_metadata(self.module._action_open_browser)
        assert "app.browser" in meta.get("permissions", [])

    def test_open_browser_has_audit_trail(self):
        meta = collect_security_metadata(self.module._action_open_browser)
        assert meta.get("audit_level") == "standard"

    def test_navigate_requires_browser_permission(self):
        meta = collect_security_metadata(self.module._action_navigate_to)
        assert "app.browser" in meta.get("permissions", [])

    def test_click_element_requires_browser_permission(self):
        meta = collect_security_metadata(self.module._action_click_element)
        assert "app.browser" in meta.get("permissions", [])

    def test_fill_input_requires_browser_permission(self):
        meta = collect_security_metadata(self.module._action_fill_input)
        assert "app.browser" in meta.get("permissions", [])

    def test_select_option_requires_browser_permission(self):
        meta = collect_security_metadata(self.module._action_select_option)
        assert "app.browser" in meta.get("permissions", [])

    def test_execute_script_requires_browser_and_sensitive(self):
        meta = collect_security_metadata(self.module._action_execute_script)
        assert "app.browser" in meta.get("permissions", [])
        assert meta.get("risk_level") == "high"
        assert meta.get("audit_level") == "detailed"

    def test_readonly_actions_have_no_permissions(self):
        for action_name in [
            "_action_get_page_content",
            "_action_get_element_text",
            "_action_take_screenshot",
            "_action_wait_for_element",
        ]:
            fn = getattr(self.module, action_name)
            meta = collect_security_metadata(fn)
            assert "permissions" not in meta, f"{action_name} should not have permissions"
