"""Unit tests — Pluggable vision backend swap."""

from __future__ import annotations

from typing import Any

import pytest

from llmos_bridge.modules.manifest import ActionSpec, ModuleManifest
from llmos_bridge.modules.perception_vision.base import (
    BaseVisionModule,
    VisionElement,
    VisionParseResult,
)
from llmos_bridge.modules.registry import ModuleRegistry


class FakeVisionModule(BaseVisionModule):
    """Custom vision backend for testing."""

    MODULE_ID = "vision"
    VERSION = "0.1.0-fake"

    def _check_dependencies(self) -> None:
        pass  # No deps

    async def parse_screen(
        self,
        screenshot_path: str | None = None,
        screenshot_bytes: bytes | None = None,
        *,
        width: int | None = None,
        height: int | None = None,
    ) -> VisionParseResult:
        return VisionParseResult(
            elements=[
                VisionElement(
                    element_id="fake-0",
                    label="FakeElement",
                    element_type="button",
                    bbox=(0.1, 0.1, 0.2, 0.2),
                    confidence=1.0,
                ),
            ],
            width=width or 800,
            height=height or 600,
            raw_ocr="FakeElement",
            labeled_image_b64=None,
            parse_time_ms=1.0,
            model_id="fake-vision-v0.1",
        )

    async def _action_parse_screen(self, params: dict[str, Any]) -> dict[str, Any]:
        result = await self.parse_screen(
            screenshot_path=params.get("screenshot_path"),
            screenshot_bytes=params.get("screenshot_bytes"),
        )
        return result.to_dict()

    async def _action_capture_and_parse(self, params: dict[str, Any]) -> dict[str, Any]:
        return (await self.parse_screen()).to_dict()

    async def _action_find_element(self, params: dict[str, Any]) -> dict[str, Any]:
        result = await self.parse_screen()
        query = params.get("query", "")
        matches = result.find_by_label(query)
        return {"found": len(matches) > 0, "matches": [m.model_dump() for m in matches]}

    async def _action_get_screen_text(self, params: dict[str, Any]) -> dict[str, Any]:
        result = await self.parse_screen()
        return {"text": result.raw_ocr or ""}

    def get_manifest(self) -> ModuleManifest:
        return ModuleManifest(
            module_id=self.MODULE_ID,
            version=self.VERSION,
            description="Fake vision module for testing",
            actions=[
                ActionSpec(name="parse_screen", description="Parse"),
                ActionSpec(name="capture_and_parse", description="Capture and parse"),
                ActionSpec(name="find_element", description="Find"),
                ActionSpec(name="get_screen_text", description="Text"),
            ],
        )


@pytest.mark.unit
class TestVisionBackendSwap:
    def test_fake_replaces_default(self) -> None:
        registry = ModuleRegistry()
        registry.register(FakeVisionModule)
        module = registry.get("vision")
        assert isinstance(module, FakeVisionModule)
        assert module.VERSION == "0.1.0-fake"

    def test_is_base_vision_module(self) -> None:
        module = FakeVisionModule()
        assert isinstance(module, BaseVisionModule)
        assert module.MODULE_ID == "vision"

    @pytest.mark.asyncio
    async def test_fake_parse_screen(self) -> None:
        module = FakeVisionModule()
        result = await module.parse_screen()
        assert len(result.elements) == 1
        assert result.elements[0].label == "FakeElement"
        assert result.model_id == "fake-vision-v0.1"

    @pytest.mark.asyncio
    async def test_fake_via_execute(self) -> None:
        module = FakeVisionModule()
        result = await module.execute("capture_and_parse", {})
        assert isinstance(result, dict)
        assert result["width"] == 800

    def test_manifest_has_expected_actions(self) -> None:
        module = FakeVisionModule()
        manifest = module.get_manifest()
        names = {a.name for a in manifest.actions}
        assert names == {"parse_screen", "capture_and_parse", "find_element", "get_screen_text"}
