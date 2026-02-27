"""Tests — GUI module security decorator coverage."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from llmos_bridge.modules.gui.module import GUIModule
from llmos_bridge.security.decorators import collect_security_metadata


class TestGUISecurity:
    def setup_method(self):
        # Skip _check_dependencies — pyautogui may not be installed
        with patch.object(GUIModule, "_check_dependencies"):
            self.module = GUIModule()

    def test_click_position_requires_keyboard_permission(self):
        meta = collect_security_metadata(self.module._action_click_position)
        assert "device.keyboard" in meta.get("permissions", [])

    def test_click_position_has_rate_limit(self):
        meta = collect_security_metadata(self.module._action_click_position)
        assert meta.get("rate_limit", {}).get("calls_per_minute") == 120

    def test_click_image_requires_keyboard_permission(self):
        meta = collect_security_metadata(self.module._action_click_image)
        assert "device.keyboard" in meta.get("permissions", [])

    def test_type_text_requires_keyboard_permission(self):
        meta = collect_security_metadata(self.module._action_type_text)
        assert "device.keyboard" in meta.get("permissions", [])

    def test_type_text_has_rate_limit(self):
        meta = collect_security_metadata(self.module._action_type_text)
        assert meta.get("rate_limit", {}).get("calls_per_minute") == 120

    def test_key_press_requires_keyboard_permission(self):
        meta = collect_security_metadata(self.module._action_key_press)
        assert "device.keyboard" in meta.get("permissions", [])

    def test_scroll_requires_keyboard_permission(self):
        meta = collect_security_metadata(self.module._action_scroll)
        assert "device.keyboard" in meta.get("permissions", [])

    def test_take_screenshot_requires_screen_capture(self):
        meta = collect_security_metadata(self.module._action_take_screenshot)
        assert "device.screen" in meta.get("permissions", [])

    def test_readonly_actions_have_no_permissions(self):
        for action_name in [
            "_action_find_on_screen",
            "_action_get_screen_text",
            "_action_get_window_info",
        ]:
            fn = getattr(self.module, action_name)
            meta = collect_security_metadata(fn)
            assert "permissions" not in meta, f"{action_name} should not have permissions"
