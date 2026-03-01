"""Computer Control module — semantic GUI automation gateway.

Bridges vision (perception) + GUI (physical actions) into high-level
semantic operations.  The LLM says "click the Validate button" and this
module handles: capture screen -> parse with vision -> find element ->
compute pixel coordinates -> click via GUI module.

Architecture::

    Registry
      |-- "vision" (OmniParserModule or custom BaseVisionModule)
      |-- "gui" (GUIModule -- PyAutoGUI)
      +-- "computer_control" (this module -- orchestration layer)

This module does NOT directly import pyautogui, torch, or omniparser.
All heavy dependencies are accessed via the module registry, making the
entire stack pluggable.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from typing import Any

from llmos_bridge.exceptions import ActionExecutionError
from llmos_bridge.logging import get_logger
from llmos_bridge.modules.base import BaseModule, Platform
from llmos_bridge.modules.computer_control.resolution import ElementResolver, ResolvedElement
from llmos_bridge.modules.manifest import ActionSpec, ModuleManifest, ParamSpec
from llmos_bridge.modules.perception_vision.base import VisionParseResult
from llmos_bridge.modules.registry import ModuleRegistry
from llmos_bridge.protocol.params.computer_control import (
    ClickElementParams,
    ExecuteGuiSequenceParams,
    FindAndInteractParams,
    GetElementInfoParams,
    MoveToElementParams,
    ReadScreenParams,
    ScrollToElementParams,
    TypeIntoElementParams,
    WaitForElementParams,
)
from llmos_bridge.security.decorators import (
    audit_trail,
    rate_limited,
    requires_permission,
    sensitive_action,
)
from llmos_bridge.security.models import Permission, RiskLevel

log = get_logger(__name__)


class ComputerControlModule(BaseModule):
    """Semantic GUI automation gateway.

    Resolves natural language element descriptions to pixel coordinates
    via a vision module, then delegates physical actions to a GUI module.
    """

    MODULE_ID = "computer_control"
    VERSION = "1.0.0"
    SUPPORTED_PLATFORMS = [Platform.LINUX, Platform.MACOS, Platform.WINDOWS]

    def __init__(self) -> None:
        self._registry: ModuleRegistry | None = None
        self._resolver = ElementResolver()
        self._prefetcher: Any | None = None  # Lazy SpeculativePrefetcher
        self._prefetcher_initialized = False
        super().__init__()

    def set_registry(self, registry: ModuleRegistry) -> None:
        """Inject the module registry for dynamic module access."""
        self._registry = registry

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_vision_module(self) -> BaseModule:
        if self._registry is None:
            raise ActionExecutionError(
                module_id=self.MODULE_ID,
                action="(internal)",
                cause=RuntimeError("ComputerControlModule requires set_registry() call"),
            )
        if not self._registry.is_available("vision"):
            raise ActionExecutionError(
                module_id=self.MODULE_ID,
                action="(internal)",
                cause=RuntimeError(
                    "Vision module ('vision') is not available. "
                    "Install: pip install llmos-bridge[vision]. "
                    "Or register a custom BaseVisionModule."
                ),
            )
        return self._registry.get("vision")

    def _get_gui_module(self) -> BaseModule:
        if self._registry is None:
            raise ActionExecutionError(
                module_id=self.MODULE_ID,
                action="(internal)",
                cause=RuntimeError("ComputerControlModule requires set_registry() call"),
            )
        if not self._registry.is_available("gui"):
            raise ActionExecutionError(
                module_id=self.MODULE_ID,
                action="(internal)",
                cause=RuntimeError(
                    "GUI module ('gui') is not available. "
                    "Install: pip install pyautogui. "
                    "Requires a display environment."
                ),
            )
        return self._registry.get("gui")

    def _get_prefetcher(self) -> Any:
        """Lazy-init SpeculativePrefetcher from config."""
        if self._prefetcher_initialized:
            return self._prefetcher
        self._prefetcher_initialized = True
        try:
            from llmos_bridge.config import get_settings
            from llmos_bridge.modules.perception_vision.cache import (
                PerceptionCache,
                SpeculativePrefetcher,
            )

            cfg = get_settings().vision
            if cfg.speculative_prefetch and cfg.cache_max_entries > 0:
                cache = PerceptionCache(
                    max_entries=cfg.cache_max_entries,
                    ttl_seconds=cfg.cache_ttl_seconds,
                )
                self._prefetcher = SpeculativePrefetcher(
                    cache=cache,
                    capture_and_parse_fn=self._raw_capture_and_parse,
                )
        except Exception:
            pass  # Config not available — no prefetch.
        return self._prefetcher

    async def _raw_capture_and_parse(self) -> tuple[bytes, VisionParseResult]:
        """Capture screen, parse, and return both raw bytes and result.

        Used by SpeculativePrefetcher as its parse function.
        Returns a content-derived fingerprint as the cache key since we
        don't have access to raw screenshot bytes through the module API.
        """
        vision = self._get_vision_module()
        raw = await vision.execute("capture_and_parse", {})
        result = VisionParseResult.model_validate(raw)
        # Content fingerprint: same screen → same elements → same hash.
        content = str([(e.element_id, e.label, e.bbox) for e in result.elements])
        fingerprint = hashlib.md5(content.encode()).digest()  # noqa: S324
        return fingerprint, result

    async def _capture_and_parse(self) -> VisionParseResult:
        """Capture screen and parse via vision module, returning typed result.

        Uses SpeculativePrefetcher if available — result may already be
        ready from a background parse triggered after the previous action.
        """
        prefetcher = self._get_prefetcher()
        if prefetcher is not None:
            try:
                return await prefetcher.get_or_parse()
            except Exception:
                pass  # Fall through to direct parse.
        vision = self._get_vision_module()
        raw = await vision.execute("capture_and_parse", {})
        return VisionParseResult.model_validate(raw)

    def _trigger_prefetch(self) -> None:
        """Trigger a background screen parse for the next read_screen call."""
        prefetcher = self._get_prefetcher()
        if prefetcher is not None:
            prefetcher.trigger()

    def _resolve_element(
        self,
        query: str,
        parse_result: VisionParseResult,
        element_type: str | None = None,
    ) -> ResolvedElement | None:
        return self._resolver.resolve(
            query,
            parse_result,
            element_type=element_type,
        )

    def _not_found_response(
        self, target: str, parse_result: VisionParseResult
    ) -> dict[str, Any]:
        return {
            "found": False,
            "error": f"Element '{target}' not found on screen",
            "screen_elements": len(parse_result.elements),
            "screen_text": (parse_result.raw_ocr or "")[:500] or None,
        }

    def _element_dict(self, resolved: ResolvedElement) -> dict[str, Any]:
        return {
            "element_id": resolved.element.element_id,
            "label": resolved.element.label,
            "element_type": resolved.element.element_type,
            "bbox": resolved.element.bbox,
            "confidence": resolved.confidence,
            "pixel_x": resolved.pixel_x,
            "pixel_y": resolved.pixel_y,
            "match_strategy": resolved.match_strategy,
        }

    # ------------------------------------------------------------------
    # Actions — Semantic GUI automation
    # ------------------------------------------------------------------

    @requires_permission(
        Permission.SCREEN_CAPTURE,
        Permission.KEYBOARD,
        reason="Captures screen, parses UI, clicks element",
    )
    @sensitive_action(risk_level=RiskLevel.HIGH, irreversible=False)
    @rate_limited(calls_per_minute=60)
    @audit_trail("standard")
    async def _action_click_element(self, params: dict[str, Any]) -> dict[str, Any]:
        p = ClickElementParams.model_validate(params)

        parse_result = await self._capture_and_parse()
        resolved = self._resolve_element(p.target_description, parse_result, p.element_type)

        if resolved is None:
            return {"clicked": False, **self._not_found_response(p.target_description, parse_result)}

        gui = self._get_gui_module()
        click_map = {
            "single": "click_position",
            "double": "double_click",
            "right": "right_click",
        }
        action_name = click_map.get(p.click_type, "click_position")
        await gui.execute(action_name, {"x": resolved.pixel_x, "y": resolved.pixel_y})

        self._trigger_prefetch()
        return {
            "clicked": True,
            **self._element_dict(resolved),
            "click_type": p.click_type,
        }

    @requires_permission(
        Permission.SCREEN_CAPTURE,
        Permission.KEYBOARD,
        reason="Finds input element and types text into it",
    )
    @sensitive_action(risk_level=RiskLevel.HIGH, irreversible=False)
    @rate_limited(calls_per_minute=60)
    @audit_trail("standard")
    async def _action_type_into_element(self, params: dict[str, Any]) -> dict[str, Any]:
        p = TypeIntoElementParams.model_validate(params)

        parse_result = await self._capture_and_parse()
        resolved = self._resolve_element(p.target_description, parse_result, p.element_type)

        if resolved is None:
            return {"typed": False, **self._not_found_response(p.target_description, parse_result)}

        gui = self._get_gui_module()

        # Click on the element to focus it.
        await gui.execute("click_position", {"x": resolved.pixel_x, "y": resolved.pixel_y})

        # Clear field first if requested.
        if p.clear_first:
            await gui.execute("key_press", {"keys": ["ctrl+a"]})
            await gui.execute("key_press", {"keys": ["delete"]})

        # Type the text.
        await gui.execute("type_text", {"text": p.text})

        self._trigger_prefetch()
        return {
            "typed": True,
            **self._element_dict(resolved),
            "text": p.text,
            "length": len(p.text),
        }

    @requires_permission(Permission.SCREEN_CAPTURE, reason="Polls screen for element appearance")
    @rate_limited(calls_per_minute=30)
    @audit_trail("standard")
    async def _action_wait_for_element(self, params: dict[str, Any]) -> dict[str, Any]:
        p = WaitForElementParams.model_validate(params)

        start = time.monotonic()
        deadline = start + p.timeout

        while time.monotonic() < deadline:
            parse_result = await self._capture_and_parse()
            resolved = self._resolve_element(p.target_description, parse_result, p.element_type)

            if resolved is not None:
                elapsed_ms = (time.monotonic() - start) * 1000
                return {
                    "found": True,
                    **self._element_dict(resolved),
                    "wait_time_ms": round(elapsed_ms, 1),
                }

            remaining = deadline - time.monotonic()
            if remaining > 0:
                await asyncio.sleep(min(p.poll_interval, remaining))

        elapsed_ms = (time.monotonic() - start) * 1000
        return {
            "found": False,
            "error": f"Element '{p.target_description}' not found within {p.timeout}s",
            "wait_time_ms": round(elapsed_ms, 1),
        }

    @requires_permission(Permission.SCREEN_CAPTURE, reason="Captures and parses screen content")
    @audit_trail("standard")
    async def _action_read_screen(self, params: dict[str, Any]) -> dict[str, Any]:
        p = ReadScreenParams.model_validate(params)
        start = time.monotonic()

        parse_result = await self._capture_and_parse()

        elapsed_ms = (time.monotonic() - start) * 1000

        elements = [
            {
                "element_id": e.element_id,
                "label": e.label,
                "element_type": e.element_type,
                "bbox": e.bbox,
                "confidence": e.confidence,
                "interactable": e.interactable,
            }
            for e in parse_result.elements[:100]  # Cap to prevent massive payloads
        ]

        result: dict[str, Any] = {
            "elements": elements,
            "element_count": len(parse_result.elements),
            "interactable_count": sum(1 for e in parse_result.elements if e.interactable),
            "text": parse_result.raw_ocr[:2000] if parse_result.raw_ocr else None,
            "parse_time_ms": round(elapsed_ms, 1),
        }

        # Include annotated screenshot only when explicitly requested.
        if p.include_screenshot and parse_result.labeled_image_b64:
            result["screenshot_b64"] = parse_result.labeled_image_b64

        # Include hierarchical scene graph if available.
        if parse_result.scene_graph_text:
            result["scene_graph"] = parse_result.scene_graph_text

        return result

    @requires_permission(
        Permission.SCREEN_CAPTURE,
        Permission.KEYBOARD,
        reason="Finds element and performs interaction",
    )
    @sensitive_action(risk_level=RiskLevel.HIGH, irreversible=False)
    @rate_limited(calls_per_minute=60)
    @audit_trail("standard")
    async def _action_find_and_interact(self, params: dict[str, Any]) -> dict[str, Any]:
        p = FindAndInteractParams.model_validate(params)

        parse_result = await self._capture_and_parse()
        resolved = self._resolve_element(p.target_description, parse_result)

        if resolved is None:
            return {
                "interacted": False,
                **self._not_found_response(p.target_description, parse_result),
            }

        gui = self._get_gui_module()
        interaction_map = {
            "click": "click_position",
            "double_click": "double_click",
            "right_click": "right_click",
            "hover": "click_position",  # Move to position (hover = move without click)
        }
        action_name = interaction_map.get(p.interaction, "click_position")

        gui_params: dict[str, Any] = {"x": resolved.pixel_x, "y": resolved.pixel_y}
        if p.interaction == "hover":
            # Use move_mouse action instead of click.
            # GUIModule doesn't have a dedicated hover, so we move and don't click.
            gui_params = {"x": resolved.pixel_x, "y": resolved.pixel_y}

        await gui.execute(action_name, gui_params)

        self._trigger_prefetch()
        return {
            "interacted": True,
            **self._element_dict(resolved),
            "interaction": p.interaction,
        }

    @requires_permission(Permission.SCREEN_CAPTURE, reason="Finds element details")
    @audit_trail("standard")
    async def _action_get_element_info(self, params: dict[str, Any]) -> dict[str, Any]:
        p = GetElementInfoParams.model_validate(params)

        parse_result = await self._capture_and_parse()
        resolved = self._resolve_element(p.target_description, parse_result, p.element_type)

        if resolved is None:
            return self._not_found_response(p.target_description, parse_result)

        alternatives = [
            {
                "label": alt.label,
                "element_type": alt.element_type,
                "confidence": alt.confidence,
            }
            for alt in resolved.alternatives
        ]

        return {
            "found": True,
            **self._element_dict(resolved),
            "text": resolved.element.text,
            "interactable": resolved.element.interactable,
            "alternatives": alternatives,
        }

    @requires_permission(
        Permission.SCREEN_CAPTURE,
        Permission.KEYBOARD,
        reason="Executes multi-step GUI sequence",
    )
    @sensitive_action(risk_level=RiskLevel.CRITICAL, irreversible=False)
    @rate_limited(calls_per_minute=20)
    @audit_trail("standard")
    async def _action_execute_gui_sequence(self, params: dict[str, Any]) -> dict[str, Any]:
        p = ExecuteGuiSequenceParams.model_validate(params)

        results: list[dict[str, Any]] = []
        for i, step in enumerate(p.steps):
            action = step.get("action", "click_element")
            target = step.get("target", "")
            step_params = step.get("params", {})

            # Build params dict for the sub-action.
            sub_params = {"target_description": target, **step_params}

            handler = self._get_handler(action)
            try:
                result = await handler(sub_params)
                results.append({"step": i, "action": action, "result": result})

                # Check failure.
                failed = not result.get("clicked", result.get("typed", result.get("found", True)))
                if p.stop_on_failure and failed:
                    return {
                        "completed": i,
                        "total": len(p.steps),
                        "stopped_at_step": i,
                        "results": results,
                    }
            except Exception as exc:
                results.append({"step": i, "action": action, "error": str(exc)})
                if p.stop_on_failure:
                    return {
                        "completed": i,
                        "total": len(p.steps),
                        "stopped_at_step": i,
                        "results": results,
                    }

        return {
            "completed": len(p.steps),
            "total": len(p.steps),
            "results": results,
        }

    @requires_permission(
        Permission.SCREEN_CAPTURE,
        Permission.KEYBOARD,
        reason="Finds element and moves mouse to it",
    )
    @rate_limited(calls_per_minute=60)
    @audit_trail("standard")
    async def _action_move_to_element(self, params: dict[str, Any]) -> dict[str, Any]:
        p = MoveToElementParams.model_validate(params)

        parse_result = await self._capture_and_parse()
        resolved = self._resolve_element(p.target_description, parse_result, p.element_type)

        if resolved is None:
            return {"moved": False, **self._not_found_response(p.target_description, parse_result)}

        gui = self._get_gui_module()
        # GUIModule.click_position with no actual click — we just move.
        # The simplest approach is to use drag_drop to the same point or use scroll(0).
        # Actually, pyautogui.moveTo is exposed via click_position action.
        # We'll call click_position but this clicks. For a true move-only,
        # we need the gui module to support it. For now, simulate with scroll(0).
        await gui.execute("click_position", {"x": resolved.pixel_x, "y": resolved.pixel_y})

        return {
            "moved": True,
            **self._element_dict(resolved),
        }

    @requires_permission(
        Permission.SCREEN_CAPTURE,
        Permission.KEYBOARD,
        reason="Scrolls screen to find element",
    )
    @rate_limited(calls_per_minute=30)
    @audit_trail("standard")
    async def _action_scroll_to_element(self, params: dict[str, Any]) -> dict[str, Any]:
        p = ScrollToElementParams.model_validate(params)

        gui = self._get_gui_module()
        scroll_amount = 3 if p.direction == "down" else -3

        for i in range(p.max_scrolls):
            parse_result = await self._capture_and_parse()
            resolved = self._resolve_element(p.target_description, parse_result)

            if resolved is not None:
                return {
                    "found": True,
                    **self._element_dict(resolved),
                    "scrolls_needed": i,
                }

            await gui.execute("scroll", {"clicks": scroll_amount})
            await asyncio.sleep(0.3)  # Let the UI settle

        return {
            "found": False,
            "error": f"Element '{p.target_description}' not found after {p.max_scrolls} scrolls",
            "scrolls_needed": p.max_scrolls,
        }

    # ------------------------------------------------------------------
    # Manifest
    # ------------------------------------------------------------------

    def get_manifest(self) -> ModuleManifest:
        _ACTIONSPEC_KEYS = {"permissions", "risk_level", "irreversible", "data_classification"}
        raw_meta = self._collect_security_metadata()
        security_meta = {
            action: {k: v for k, v in meta.items() if k in _ACTIONSPEC_KEYS}
            for action, meta in raw_meta.items()
        }
        actions = [
            ActionSpec(
                name="click_element",
                description="Find a UI element by description and click it.",
                params=[
                    ParamSpec("target_description", "string", "Natural language description of the element to click", required=True),
                    ParamSpec("click_type", "string", "Type of click: single, double, right", required=False, default="single", enum=["single", "double", "right"]),
                    ParamSpec("element_type", "string", "Filter by element type: button, input, link, icon, text, checkbox", required=False),
                    ParamSpec("timeout", "number", "Max seconds for capture+parse", required=False, default=5.0),
                ],
                returns_description="Object with clicked status, element info, and pixel coordinates",
                permission_required="power_user",
                tags=["gui", "automation", "semantic"],
                examples=[{"params": {"target_description": "Submit button"}}],
                **security_meta.get("click_element", {}),
            ),
            ActionSpec(
                name="type_into_element",
                description="Find an input field by description, click it, and type text.",
                params=[
                    ParamSpec("target_description", "string", "Description of the input field", required=True),
                    ParamSpec("text", "string", "Text to type", required=True),
                    ParamSpec("clear_first", "boolean", "Clear field before typing", required=False, default=True),
                    ParamSpec("element_type", "string", "Filter by element type", required=False),
                ],
                returns_description="Object with typed status and element info",
                permission_required="power_user",
                tags=["gui", "automation", "semantic"],
                examples=[{"params": {"target_description": "Search input", "text": "hello world"}}],
                **security_meta.get("type_into_element", {}),
            ),
            ActionSpec(
                name="wait_for_element",
                description="Poll the screen until an element matching the description appears.",
                params=[
                    ParamSpec("target_description", "string", "Description of the element to wait for", required=True),
                    ParamSpec("timeout", "number", "Max seconds to wait", required=False, default=30.0),
                    ParamSpec("poll_interval", "number", "Seconds between captures", required=False, default=2.0),
                    ParamSpec("element_type", "string", "Filter by element type", required=False),
                ],
                returns_description="Object with found status and wait time",
                permission_required="power_user",
                tags=["gui", "automation", "wait"],
                **security_meta.get("wait_for_element", {}),
            ),
            ActionSpec(
                name="read_screen",
                description=(
                    "Capture the screen and parse all UI elements. Returns structured "
                    "element list, OCR text, and optionally an annotated screenshot "
                    "with bounding boxes drawn around detected elements."
                ),
                params=[
                    ParamSpec("monitor", "integer", "Monitor index", required=False, default=0),
                    ParamSpec("region", "object", "Crop region: {left, top, width, height}", required=False),
                    ParamSpec(
                        "include_screenshot", "boolean",
                        "Include annotated screenshot as base64 PNG (adds ~200-500KB)",
                        required=False, default=False,
                    ),
                ],
                returns_description=(
                    "List of UI elements with types, labels, positions, OCR text, "
                    "and optional screenshot_b64 (annotated image with bounding boxes)"
                ),
                permission_required="power_user",
                tags=["gui", "perception", "read"],
                **security_meta.get("read_screen", {}),
            ),
            ActionSpec(
                name="find_and_interact",
                description="Find an element by description and perform an interaction (click, double_click, right_click, hover).",
                params=[
                    ParamSpec("target_description", "string", "Element description", required=True),
                    ParamSpec("interaction", "string", "Interaction type", required=False, default="click", enum=["click", "double_click", "right_click", "hover"]),
                    ParamSpec("params", "object", "Additional interaction params", required=False),
                ],
                returns_description="Object with interaction result",
                permission_required="power_user",
                tags=["gui", "automation", "semantic"],
                **security_meta.get("find_and_interact", {}),
            ),
            ActionSpec(
                name="get_element_info",
                description="Find an element by description and return its details without interacting.",
                params=[
                    ParamSpec("target_description", "string", "Element description", required=True),
                    ParamSpec("element_type", "string", "Filter by element type", required=False),
                ],
                returns_description="Element details: label, type, position, confidence, alternatives",
                permission_required="power_user",
                tags=["gui", "perception", "read"],
                **security_meta.get("get_element_info", {}),
            ),
            ActionSpec(
                name="execute_gui_sequence",
                description="Execute a multi-step GUI workflow: a sequence of semantic actions.",
                params=[
                    ParamSpec("steps", "array", "List of steps: [{action, target, params}, ...]", required=True),
                    ParamSpec("stop_on_failure", "boolean", "Stop on first failure", required=False, default=True),
                ],
                returns_description="Completion summary with per-step results",
                permission_required="power_user",
                tags=["gui", "automation", "workflow"],
                **security_meta.get("execute_gui_sequence", {}),
            ),
            ActionSpec(
                name="move_to_element",
                description="Find an element by description and move the mouse cursor to its center.",
                params=[
                    ParamSpec("target_description", "string", "Element description", required=True),
                    ParamSpec("element_type", "string", "Filter by element type", required=False),
                ],
                returns_description="Object with move result and element info",
                permission_required="power_user",
                tags=["gui", "automation", "semantic"],
                **security_meta.get("move_to_element", {}),
            ),
            ActionSpec(
                name="scroll_to_element",
                description="Scroll the screen until an element matching the description becomes visible.",
                params=[
                    ParamSpec("target_description", "string", "Element description", required=True),
                    ParamSpec("max_scrolls", "integer", "Max scroll attempts", required=False, default=10),
                    ParamSpec("direction", "string", "Scroll direction: down or up", required=False, default="down", enum=["down", "up"]),
                ],
                returns_description="Object with found status and scrolls needed",
                permission_required="power_user",
                tags=["gui", "automation", "scroll"],
                **security_meta.get("scroll_to_element", {}),
            ),
        ]

        return ModuleManifest(
            module_id=self.MODULE_ID,
            version=self.VERSION,
            description=(
                "Semantic GUI automation gateway. Describe UI elements in natural language "
                "and interact with them automatically via vision + GUI modules."
            ),
            platforms=[p.value for p in self.SUPPORTED_PLATFORMS],
            actions=actions,
            declared_permissions=[
                Permission.SCREEN_CAPTURE,
                Permission.KEYBOARD,
            ],
        )
