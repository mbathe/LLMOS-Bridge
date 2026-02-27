"""Unit tests — ComputerControlModule (all vision + GUI calls mocked)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from llmos_bridge.exceptions import ActionExecutionError
from llmos_bridge.modules.computer_control.module import ComputerControlModule


def _mock_vision_result(
    elements: list[dict] | None = None,
    raw_ocr: str = "Submit Cancel Help",
) -> dict:
    if elements is None:
        elements = [
            {
                "element_id": "e0",
                "label": "Submit",
                "element_type": "button",
                "bbox": [0.5, 0.5, 0.6, 0.55],
                "confidence": 0.95,
                "text": "Submit",
                "interactable": True,
                "extra": {},
            },
        ]
    return {
        "elements": elements,
        "width": 1920,
        "height": 1080,
        "raw_ocr": raw_ocr,
        "labeled_image_b64": None,
        "parse_time_ms": 150.0,
        "model_id": "test/mock",
        "error": None,
    }


def _empty_vision_result() -> dict:
    return _mock_vision_result(elements=[], raw_ocr="")


@pytest.fixture
def mock_registry() -> MagicMock:
    registry = MagicMock()

    vision = MagicMock()
    vision.MODULE_ID = "vision"
    vision.execute = AsyncMock(return_value=_mock_vision_result())

    gui = MagicMock()
    gui.MODULE_ID = "gui"
    gui.execute = AsyncMock(return_value={"clicked": True, "x": 960, "y": 540})

    def _is_available(mod_id: str) -> bool:
        return mod_id in ("vision", "gui")

    def _get(mod_id: str) -> MagicMock:
        if mod_id == "vision":
            return vision
        if mod_id == "gui":
            return gui
        return MagicMock()

    registry.is_available = MagicMock(side_effect=_is_available)
    registry.get = MagicMock(side_effect=_get)
    registry._vision = vision
    registry._gui = gui
    return registry


@pytest.fixture
def module(mock_registry: MagicMock) -> ComputerControlModule:
    cc = ComputerControlModule()
    cc.set_registry(mock_registry)
    return cc


# --------------------------------------------------------------------------
# click_element
# --------------------------------------------------------------------------


@pytest.mark.unit
class TestClickElement:
    @pytest.mark.asyncio
    async def test_click_found_element(self, module: ComputerControlModule, mock_registry: MagicMock) -> None:
        result = await module.execute("click_element", {"target_description": "Submit"})
        assert result["clicked"] is True
        assert result["match_strategy"] in ("exact", "substring")
        mock_registry._gui.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_click_not_found(self, module: ComputerControlModule, mock_registry: MagicMock) -> None:
        mock_registry._vision.execute.return_value = _empty_vision_result()
        result = await module.execute("click_element", {"target_description": "NonExistent"})
        assert result["clicked"] is False
        assert "error" in result

    @pytest.mark.asyncio
    async def test_click_double(self, module: ComputerControlModule, mock_registry: MagicMock) -> None:
        result = await module.execute("click_element", {
            "target_description": "Submit",
            "click_type": "double",
        })
        assert result["clicked"] is True
        mock_registry._gui.execute.assert_called_once()
        call_args = mock_registry._gui.execute.call_args
        assert call_args[0][0] == "double_click"

    @pytest.mark.asyncio
    async def test_click_right(self, module: ComputerControlModule, mock_registry: MagicMock) -> None:
        result = await module.execute("click_element", {
            "target_description": "Submit",
            "click_type": "right",
        })
        assert result["clicked"] is True
        call_args = mock_registry._gui.execute.call_args
        assert call_args[0][0] == "right_click"

    @pytest.mark.asyncio
    async def test_click_with_type_filter(self, module: ComputerControlModule) -> None:
        result = await module.execute("click_element", {
            "target_description": "Submit",
            "element_type": "button",
        })
        assert result["clicked"] is True


# --------------------------------------------------------------------------
# type_into_element
# --------------------------------------------------------------------------


@pytest.mark.unit
class TestTypeIntoElement:
    @pytest.mark.asyncio
    async def test_type_found(self, module: ComputerControlModule, mock_registry: MagicMock) -> None:
        result = await module.execute("type_into_element", {
            "target_description": "Submit",
            "text": "hello",
        })
        assert result["typed"] is True
        assert result["text"] == "hello"
        assert result["length"] == 5
        # Click + clear (ctrl+a, delete) + type = at least 4 gui calls
        assert mock_registry._gui.execute.call_count >= 3

    @pytest.mark.asyncio
    async def test_type_not_found(self, module: ComputerControlModule, mock_registry: MagicMock) -> None:
        mock_registry._vision.execute.return_value = _empty_vision_result()
        result = await module.execute("type_into_element", {
            "target_description": "Input",
            "text": "test",
        })
        assert result["typed"] is False

    @pytest.mark.asyncio
    async def test_type_without_clear(self, module: ComputerControlModule, mock_registry: MagicMock) -> None:
        result = await module.execute("type_into_element", {
            "target_description": "Submit",
            "text": "hello",
            "clear_first": False,
        })
        assert result["typed"] is True
        # Click + type = 2 gui calls (no clear)
        assert mock_registry._gui.execute.call_count == 2


# --------------------------------------------------------------------------
# wait_for_element
# --------------------------------------------------------------------------


@pytest.mark.unit
class TestWaitForElement:
    @pytest.mark.asyncio
    async def test_found_immediately(self, module: ComputerControlModule) -> None:
        result = await module.execute("wait_for_element", {
            "target_description": "Submit",
            "timeout": 5.0,
        })
        assert result["found"] is True
        assert "wait_time_ms" in result

    @pytest.mark.asyncio
    async def test_not_found_timeout(self, module: ComputerControlModule, mock_registry: MagicMock) -> None:
        mock_registry._vision.execute.return_value = _empty_vision_result()
        result = await module.execute("wait_for_element", {
            "target_description": "Loading",
            "timeout": 1.0,
            "poll_interval": 0.5,
        })
        assert result["found"] is False
        assert "wait_time_ms" in result


# --------------------------------------------------------------------------
# read_screen
# --------------------------------------------------------------------------


@pytest.mark.unit
class TestReadScreen:
    @pytest.mark.asyncio
    async def test_read_screen(self, module: ComputerControlModule) -> None:
        result = await module.execute("read_screen", {})
        assert "elements" in result
        assert result["element_count"] == 1
        assert result["interactable_count"] == 1
        assert "parse_time_ms" in result

    @pytest.mark.asyncio
    async def test_read_screen_empty(self, module: ComputerControlModule, mock_registry: MagicMock) -> None:
        mock_registry._vision.execute.return_value = _empty_vision_result()
        result = await module.execute("read_screen", {})
        assert result["element_count"] == 0
        assert result["elements"] == []

    @pytest.mark.asyncio
    async def test_read_screen_with_screenshot(self, module: ComputerControlModule, mock_registry: MagicMock) -> None:
        """read_screen with include_screenshot=True returns the annotated image."""
        result_with_image = _mock_vision_result()
        result_with_image["labeled_image_b64"] = "iVBORw0KGgoAAAANSUhEUg_FAKE"
        mock_registry._vision.execute.return_value = result_with_image
        result = await module.execute("read_screen", {"include_screenshot": True})
        assert "screenshot_b64" in result
        assert result["screenshot_b64"] == "iVBORw0KGgoAAAANSUhEUg_FAKE"

    @pytest.mark.asyncio
    async def test_read_screen_without_screenshot_default(self, module: ComputerControlModule, mock_registry: MagicMock) -> None:
        """read_screen with default params does NOT include screenshot_b64."""
        result_with_image = _mock_vision_result()
        result_with_image["labeled_image_b64"] = "iVBORw0KGgoAAAANSUhEUg_FAKE"
        mock_registry._vision.execute.return_value = result_with_image
        result = await module.execute("read_screen", {})
        assert "screenshot_b64" not in result

    @pytest.mark.asyncio
    async def test_read_screen_screenshot_none_available(self, module: ComputerControlModule) -> None:
        """read_screen with include_screenshot=True but no image available."""
        result = await module.execute("read_screen", {"include_screenshot": True})
        assert "screenshot_b64" not in result


# --------------------------------------------------------------------------
# find_and_interact
# --------------------------------------------------------------------------


@pytest.mark.unit
class TestFindAndInteract:
    @pytest.mark.asyncio
    async def test_interact_click(self, module: ComputerControlModule) -> None:
        result = await module.execute("find_and_interact", {
            "target_description": "Submit",
            "interaction": "click",
        })
        assert result["interacted"] is True
        assert result["interaction"] == "click"

    @pytest.mark.asyncio
    async def test_interact_not_found(self, module: ComputerControlModule, mock_registry: MagicMock) -> None:
        mock_registry._vision.execute.return_value = _empty_vision_result()
        result = await module.execute("find_and_interact", {
            "target_description": "Nonexistent",
        })
        assert result["interacted"] is False


# --------------------------------------------------------------------------
# get_element_info
# --------------------------------------------------------------------------


@pytest.mark.unit
class TestGetElementInfo:
    @pytest.mark.asyncio
    async def test_found(self, module: ComputerControlModule) -> None:
        result = await module.execute("get_element_info", {
            "target_description": "Submit",
        })
        assert result["found"] is True
        assert "pixel_x" in result
        assert "pixel_y" in result

    @pytest.mark.asyncio
    async def test_not_found(self, module: ComputerControlModule, mock_registry: MagicMock) -> None:
        mock_registry._vision.execute.return_value = _empty_vision_result()
        result = await module.execute("get_element_info", {
            "target_description": "Missing",
        })
        assert result["found"] is False


# --------------------------------------------------------------------------
# execute_gui_sequence
# --------------------------------------------------------------------------


@pytest.mark.unit
class TestExecuteGuiSequence:
    @pytest.mark.asyncio
    async def test_single_step(self, module: ComputerControlModule) -> None:
        result = await module.execute("execute_gui_sequence", {
            "steps": [{"action": "click_element", "target": "Submit"}],
        })
        assert result["completed"] == 1
        assert result["total"] == 1

    @pytest.mark.asyncio
    async def test_multi_step(self, module: ComputerControlModule) -> None:
        result = await module.execute("execute_gui_sequence", {
            "steps": [
                {"action": "click_element", "target": "Submit"},
                {"action": "read_screen", "target": ""},
            ],
        })
        assert result["completed"] == 2

    @pytest.mark.asyncio
    async def test_stop_on_failure(self, module: ComputerControlModule, mock_registry: MagicMock) -> None:
        mock_registry._vision.execute.return_value = _empty_vision_result()
        result = await module.execute("execute_gui_sequence", {
            "steps": [
                {"action": "click_element", "target": "Nonexistent"},
                {"action": "click_element", "target": "Other"},
            ],
            "stop_on_failure": True,
        })
        assert result["completed"] == 0
        assert "stopped_at_step" in result


# --------------------------------------------------------------------------
# move_to_element
# --------------------------------------------------------------------------


@pytest.mark.unit
class TestMoveToElement:
    @pytest.mark.asyncio
    async def test_move_found(self, module: ComputerControlModule) -> None:
        result = await module.execute("move_to_element", {
            "target_description": "Submit",
        })
        assert result["moved"] is True

    @pytest.mark.asyncio
    async def test_move_not_found(self, module: ComputerControlModule, mock_registry: MagicMock) -> None:
        mock_registry._vision.execute.return_value = _empty_vision_result()
        result = await module.execute("move_to_element", {
            "target_description": "Missing",
        })
        assert result["moved"] is False


# --------------------------------------------------------------------------
# scroll_to_element
# --------------------------------------------------------------------------


@pytest.mark.unit
class TestScrollToElement:
    @pytest.mark.asyncio
    async def test_found_immediately(self, module: ComputerControlModule) -> None:
        result = await module.execute("scroll_to_element", {
            "target_description": "Submit",
        })
        assert result["found"] is True
        assert result["scrolls_needed"] == 0

    @pytest.mark.asyncio
    async def test_not_found_after_scrolls(self, module: ComputerControlModule, mock_registry: MagicMock) -> None:
        mock_registry._vision.execute.return_value = _empty_vision_result()
        result = await module.execute("scroll_to_element", {
            "target_description": "Missing",
            "max_scrolls": 2,
        })
        assert result["found"] is False
        assert result["scrolls_needed"] == 2


# --------------------------------------------------------------------------
# Error cases
# --------------------------------------------------------------------------


@pytest.mark.unit
class TestErrorCases:
    @pytest.mark.asyncio
    async def test_no_registry_raises(self) -> None:
        cc = ComputerControlModule()
        # No set_registry call
        with pytest.raises(ActionExecutionError):
            await cc.execute("read_screen", {})

    @pytest.mark.asyncio
    async def test_no_vision_module_raises(self) -> None:
        cc = ComputerControlModule()
        registry = MagicMock()
        registry.is_available = MagicMock(return_value=False)
        cc.set_registry(registry)
        with pytest.raises(ActionExecutionError):
            await cc.execute("read_screen", {})

    @pytest.mark.asyncio
    async def test_no_gui_module_raises(self) -> None:
        cc = ComputerControlModule()
        registry = MagicMock()

        vision = MagicMock()
        vision.execute = AsyncMock(return_value=_mock_vision_result())

        def _is_available(mod_id: str) -> bool:
            return mod_id == "vision"

        def _get(mod_id: str) -> MagicMock:
            return vision

        registry.is_available = MagicMock(side_effect=_is_available)
        registry.get = MagicMock(side_effect=_get)
        cc.set_registry(registry)

        with pytest.raises(ActionExecutionError):
            await cc.execute("click_element", {"target_description": "Submit"})


# --------------------------------------------------------------------------
# Manifest
# --------------------------------------------------------------------------


@pytest.mark.unit
class TestManifest:
    def test_manifest_has_9_actions(self, module: ComputerControlModule) -> None:
        manifest = module.get_manifest()
        assert manifest.module_id == "computer_control"
        assert len(manifest.actions) == 9

    def test_manifest_action_names(self, module: ComputerControlModule) -> None:
        manifest = module.get_manifest()
        names = {a.name for a in manifest.actions}
        expected = {
            "click_element", "type_into_element", "wait_for_element",
            "read_screen", "find_and_interact", "get_element_info",
            "execute_gui_sequence", "move_to_element", "scroll_to_element",
        }
        assert names == expected
