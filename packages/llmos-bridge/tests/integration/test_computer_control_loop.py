"""Integration tests — ComputerControlModule full perception→action→verify loop.

All I/O (screen capture, GUI actions, model loading) is mocked.
Tests the real module instances wired together via the registry.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llmos_bridge.modules.computer_control.module import ComputerControlModule
from llmos_bridge.modules.perception_vision.base import VisionElement, VisionParseResult
from llmos_bridge.modules.registry import ModuleRegistry


def _make_vision_result(
    elements: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if elements is None:
        elements = [
            {
                "element_id": "e0",
                "label": "Save",
                "element_type": "button",
                "bbox": [0.8, 0.9, 0.9, 0.95],
                "confidence": 0.92,
                "text": "Save",
                "interactable": True,
                "extra": {},
            },
            {
                "element_id": "e1",
                "label": "Cancel",
                "element_type": "button",
                "bbox": [0.6, 0.9, 0.7, 0.95],
                "confidence": 0.89,
                "text": "Cancel",
                "interactable": True,
                "extra": {},
            },
            {
                "element_id": "e2",
                "label": "Name",
                "element_type": "input",
                "bbox": [0.2, 0.3, 0.5, 0.35],
                "confidence": 0.95,
                "text": "",
                "interactable": True,
                "extra": {},
            },
        ]
    return {
        "elements": elements,
        "width": 1920,
        "height": 1080,
        "raw_ocr": "Name: Save Cancel",
        "labeled_image_b64": None,
        "parse_time_ms": 200.0,
        "model_id": "omniparser-v2-mock",
        "error": None,
    }


@pytest.fixture
def registry() -> ModuleRegistry:
    """Build a registry with mock vision and gui modules."""
    reg = ModuleRegistry()

    # Mock vision module that implements execute() properly.
    vision = MagicMock()
    vision.MODULE_ID = "vision"
    vision.execute = AsyncMock(return_value=_make_vision_result())
    vision.is_supported_on_current_platform = MagicMock(return_value=True)
    vision.get_manifest = MagicMock(return_value=MagicMock(module_id="vision", actions=[]))
    reg.register_instance(vision)

    # Mock GUI module.
    gui = MagicMock()
    gui.MODULE_ID = "gui"
    gui.execute = AsyncMock(return_value={"success": True})
    gui.is_supported_on_current_platform = MagicMock(return_value=True)
    gui.get_manifest = MagicMock(return_value=MagicMock(module_id="gui", actions=[]))
    reg.register_instance(gui)

    return reg


@pytest.fixture
def cc_module(registry: ModuleRegistry) -> ComputerControlModule:
    cc = ComputerControlModule()
    cc.set_registry(registry)
    return cc


@pytest.mark.integration
class TestFullWorkflowLoop:
    """Test the complete perception → action → verify loop."""

    @pytest.mark.asyncio
    async def test_click_save_button(self, cc_module: ComputerControlModule, registry: ModuleRegistry) -> None:
        result = await cc_module.execute("click_element", {"target_description": "Save"})
        assert result["clicked"] is True
        assert result["label"] == "Save"
        assert result["element_type"] == "button"
        # Verify GUI was called with correct coordinates.
        gui = registry.get("gui")
        gui.execute.assert_called_once()
        call_args = gui.execute.call_args[0]
        assert call_args[0] == "click_position"

    @pytest.mark.asyncio
    async def test_type_into_name_field(self, cc_module: ComputerControlModule, registry: ModuleRegistry) -> None:
        result = await cc_module.execute("type_into_element", {
            "target_description": "Name",
            "text": "John Doe",
        })
        assert result["typed"] is True
        assert result["text"] == "John Doe"
        gui = registry.get("gui")
        # Click + ctrl+a + delete + type = 4 calls
        assert gui.execute.call_count == 4

    @pytest.mark.asyncio
    async def test_read_screen_shows_all_elements(self, cc_module: ComputerControlModule) -> None:
        result = await cc_module.execute("read_screen", {})
        assert result["element_count"] == 3
        assert result["interactable_count"] == 3
        element_labels = {e["label"] for e in result["elements"]}
        assert "Save" in element_labels
        assert "Cancel" in element_labels
        assert "Name" in element_labels

    @pytest.mark.asyncio
    async def test_get_element_info_with_alternatives(self, cc_module: ComputerControlModule) -> None:
        result = await cc_module.execute("get_element_info", {
            "target_description": "Cancel",
        })
        assert result["found"] is True
        assert result["label"] == "Cancel"

    @pytest.mark.asyncio
    async def test_multi_step_workflow(self, cc_module: ComputerControlModule) -> None:
        result = await cc_module.execute("execute_gui_sequence", {
            "steps": [
                {"action": "click_element", "target": "Name"},
                {"action": "read_screen", "target": ""},
                {"action": "click_element", "target": "Save"},
            ],
        })
        assert result["completed"] == 3
        assert result["total"] == 3
        assert len(result["results"]) == 3


@pytest.mark.integration
class TestSemanticResolution:
    """Test that semantic resolution works correctly across the module boundary."""

    @pytest.mark.asyncio
    async def test_exact_match_on_save(self, cc_module: ComputerControlModule) -> None:
        result = await cc_module.execute("click_element", {"target_description": "Save"})
        assert result["match_strategy"] == "exact"

    @pytest.mark.asyncio
    async def test_substring_match(self, cc_module: ComputerControlModule, registry: ModuleRegistry) -> None:
        vision = registry.get("vision")
        vision.execute.return_value = _make_vision_result([
            {
                "element_id": "e0",
                "label": "Save Changes",
                "element_type": "button",
                "bbox": [0.8, 0.9, 0.9, 0.95],
                "confidence": 0.92,
                "text": "Save Changes",
                "interactable": True,
                "extra": {},
            },
        ])
        result = await cc_module.execute("click_element", {"target_description": "Save"})
        assert result["clicked"] is True
        assert result["match_strategy"] == "substring"

    @pytest.mark.asyncio
    async def test_type_filter_selects_correct_element(self, cc_module: ComputerControlModule) -> None:
        result = await cc_module.execute("get_element_info", {
            "target_description": "Name",
            "element_type": "input",
        })
        assert result["found"] is True
        assert result["element_type"] == "input"


@pytest.mark.integration
class TestErrorRecovery:
    @pytest.mark.asyncio
    async def test_element_not_found_returns_screen_info(self, cc_module: ComputerControlModule) -> None:
        result = await cc_module.execute("click_element", {"target_description": "NonexistentButton"})
        assert result["clicked"] is False
        assert result["screen_elements"] == 3
        assert result["screen_text"] is not None

    @pytest.mark.asyncio
    async def test_sequence_stops_on_failure(self, cc_module: ComputerControlModule, registry: ModuleRegistry) -> None:
        # First step OK, second step fails (element not found on second call).
        vision = registry.get("vision")
        call_count = [0]
        original = _make_vision_result()

        async def _side_effect(action: str, params: dict) -> dict:
            call_count[0] += 1
            if call_count[0] > 1:
                return _make_vision_result([])  # Empty screen
            return original

        vision.execute = AsyncMock(side_effect=_side_effect)

        result = await cc_module.execute("execute_gui_sequence", {
            "steps": [
                {"action": "click_element", "target": "Save"},
                {"action": "click_element", "target": "Missing"},
                {"action": "click_element", "target": "Other"},
            ],
            "stop_on_failure": True,
        })
        assert result["completed"] == 1
        assert result["stopped_at_step"] == 1
