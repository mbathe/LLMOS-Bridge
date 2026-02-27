"""Tests — OmniParser (vision) module security decorator coverage."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from llmos_bridge.modules.perception_vision.omniparser.module import OmniParserModule
from llmos_bridge.security.decorators import collect_security_metadata


class TestVisionSecurity:
    def setup_method(self):
        with patch.object(OmniParserModule, "_check_dependencies"):
            self.module = OmniParserModule()

    def test_parse_screen_requires_device_screen(self):
        meta = collect_security_metadata(self.module._action_parse_screen)
        assert "device.screen" in meta.get("permissions", [])

    def test_capture_and_parse_requires_device_screen(self):
        meta = collect_security_metadata(self.module._action_capture_and_parse)
        assert "device.screen" in meta.get("permissions", [])

    def test_find_element_requires_device_screen(self):
        meta = collect_security_metadata(self.module._action_find_element)
        assert "device.screen" in meta.get("permissions", [])

    def test_get_screen_text_requires_device_screen(self):
        meta = collect_security_metadata(self.module._action_get_screen_text)
        assert "device.screen" in meta.get("permissions", [])
