"""Unit tests — ComputerControlModule security decorators."""

from __future__ import annotations

import pytest

from llmos_bridge.modules.computer_control.module import ComputerControlModule
from llmos_bridge.security.decorators import collect_security_metadata


@pytest.fixture
def module() -> ComputerControlModule:
    return ComputerControlModule()


@pytest.mark.unit
class TestSecurityDecorators:
    """Verify that all actions have proper security decorators."""

    def _get_meta(self, module: ComputerControlModule, action: str) -> dict:
        handler = getattr(module, f"_action_{action}")
        meta = collect_security_metadata(handler)
        assert meta is not None, f"No security metadata on _action_{action}"
        return meta

    def test_click_element_has_permissions(self, module: ComputerControlModule) -> None:
        meta = self._get_meta(module, "click_element")
        perms = meta.get("permissions", [])
        assert "device.screen" in perms
        assert "device.keyboard" in perms

    def test_click_element_is_sensitive(self, module: ComputerControlModule) -> None:
        meta = self._get_meta(module, "click_element")
        assert meta.get("risk_level") == "high"

    def test_type_into_element_has_permissions(self, module: ComputerControlModule) -> None:
        meta = self._get_meta(module, "type_into_element")
        perms = meta.get("permissions", [])
        assert "device.screen" in perms
        assert "device.keyboard" in perms

    def test_wait_for_element_has_screen_permission(self, module: ComputerControlModule) -> None:
        meta = self._get_meta(module, "wait_for_element")
        perms = meta.get("permissions", [])
        assert "device.screen" in perms

    def test_read_screen_has_screen_permission(self, module: ComputerControlModule) -> None:
        meta = self._get_meta(module, "read_screen")
        perms = meta.get("permissions", [])
        assert "device.screen" in perms

    def test_find_and_interact_has_permissions(self, module: ComputerControlModule) -> None:
        meta = self._get_meta(module, "find_and_interact")
        perms = meta.get("permissions", [])
        assert "device.screen" in perms
        assert "device.keyboard" in perms

    def test_get_element_info_has_screen_permission(self, module: ComputerControlModule) -> None:
        meta = self._get_meta(module, "get_element_info")
        perms = meta.get("permissions", [])
        assert "device.screen" in perms

    def test_execute_gui_sequence_is_critical(self, module: ComputerControlModule) -> None:
        meta = self._get_meta(module, "execute_gui_sequence")
        assert meta.get("risk_level") == "critical"

    def test_move_to_element_has_permissions(self, module: ComputerControlModule) -> None:
        meta = self._get_meta(module, "move_to_element")
        perms = meta.get("permissions", [])
        assert "device.screen" in perms
        assert "device.keyboard" in perms

    def test_scroll_to_element_has_permissions(self, module: ComputerControlModule) -> None:
        meta = self._get_meta(module, "scroll_to_element")
        perms = meta.get("permissions", [])
        assert "device.screen" in perms
        assert "device.keyboard" in perms

    def test_all_actions_have_audit_trail(self, module: ComputerControlModule) -> None:
        actions = [
            "click_element", "type_into_element", "wait_for_element",
            "read_screen", "find_and_interact", "get_element_info",
            "execute_gui_sequence", "move_to_element", "scroll_to_element",
        ]
        for action in actions:
            meta = self._get_meta(module, action)
            assert meta.get("audit_level") is not None, f"_action_{action} missing audit_trail"
