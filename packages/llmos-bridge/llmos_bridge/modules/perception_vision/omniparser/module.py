"""Visual perception module — OmniParser (Microsoft) backend.

OmniParser is an open-source GUI understanding model that converts
screenshots into a structured list of interactable elements with labels
and bounding boxes.  It combines:
  - YOLO-based icon detection
  - Florence-2 / BLIP-2 visual captioning
  - PaddleOCR / EasyOCR for text extraction

This module wraps OmniParser behind the ``BaseVisionModule`` contract so
users can swap it for any alternative (e.g. GPT-4V, Gemini, a custom model)
by registering a different class as ``MODULE_ID = "vision"``.

Optional dependency — install with::

    pip install llmos-bridge[vision]

Without the dependency, the module loads but raises ``ModuleLoadError``
on first action call, providing a clear error message.

Model weights:
    The module expects OmniParser weights at ``~/.llmos/models/omniparser/``
    (configurable via ``LLMOS_OMNIPARSER_MODEL_DIR`` env var).
    On first run it will instruct the user to download the weights from
    HuggingFace: ``microsoft/OmniParser-v2.0``

Actions:
    - ``parse_screen``        — parse current screen or a given screenshot
    - ``capture_and_parse``   — take a screenshot then parse it
    - ``find_element``        — find a UI element by label/type/description
    - ``get_screen_text``     — extract all text from the current screen (fast OCR)
"""

from __future__ import annotations

import base64
import io
import os
import time
import uuid
from typing import Any

from llmos_bridge.exceptions import ActionExecutionError, ModuleLoadError
from llmos_bridge.modules.base import Platform
from llmos_bridge.security.decorators import requires_permission
from llmos_bridge.security.models import Permission
from llmos_bridge.modules.manifest import ActionSpec, ModuleManifest
from llmos_bridge.modules.perception_vision.base import (
    BaseVisionModule,
    VisionElement,
    VisionParseResult,
)

# ── optional dependencies ────────────────────────────────────────────────────
try:
    from PIL import Image as PILImage

    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False

try:
    import torch  # noqa: F401

    _TORCH_AVAILABLE = True
except ImportError:
    _TORCH_AVAILABLE = False

_OMNIPARSER_AVAILABLE = False
_OMNIPARSER_IMPORT_ERROR: str | None = None

try:
    # OmniParser v2 ships as a standalone utils module.
    # Users install it via: pip install omniparser  OR  clone + pip install -e .
    from omniparser import OmniParser as _OmniParserAPI  # type: ignore[import]

    _OMNIPARSER_AVAILABLE = True
except ImportError as _err:
    _OMNIPARSER_IMPORT_ERROR = str(_err)

# ─────────────────────────────────────────────────────────────────────────────

_DEFAULT_MODEL_DIR = os.path.expanduser(
    os.environ.get("LLMOS_OMNIPARSER_MODEL_DIR", "~/.llmos/models/omniparser")
)


class OmniParserModule(BaseVisionModule):
    """Default visual perception module — wraps Microsoft OmniParser v2.

    The module loads model weights lazily on first action call.
    All heavy dependencies (torch, transformers, ultralytics, PIL) are
    optional — the module registers and reports its manifest without them,
    and raises a clear error if they are missing when an action is called.

    Replace this module:
        Register any ``BaseVisionModule`` subclass as ``MODULE_ID = "vision"``
        and it will take precedence.

    Configuration (env vars):
        LLMOS_OMNIPARSER_MODEL_DIR   Path to OmniParser weight directory
                                     (default: ~/.llmos/models/omniparser)
        LLMOS_OMNIPARSER_DEVICE      torch device, e.g. "cpu", "cuda", "mps"
                                     (default: auto-detect)
        LLMOS_OMNIPARSER_BOX_THRESH  Detection confidence threshold [0-1]
                                     (default: 0.05)
        LLMOS_OMNIPARSER_IOU_THRESH  NMS IoU threshold [0-1]
                                     (default: 0.1)
    """

    MODULE_ID = "vision"
    VERSION = "1.0.0"
    SUPPORTED_PLATFORMS = [Platform.LINUX, Platform.WINDOWS, Platform.MACOS]

    def __init__(self) -> None:
        self._model_dir = _DEFAULT_MODEL_DIR
        self._device = os.environ.get("LLMOS_OMNIPARSER_DEVICE", "auto")
        self._box_thresh = float(os.environ.get("LLMOS_OMNIPARSER_BOX_THRESH", "0.05"))
        self._iou_thresh = float(os.environ.get("LLMOS_OMNIPARSER_IOU_THRESH", "0.1"))
        self._api: Any | None = None  # lazy-loaded _OmniParserAPI
        super().__init__()

    # ------------------------------------------------------------------
    # BaseModule contract
    # ------------------------------------------------------------------

    def _check_dependencies(self) -> None:
        """Warn on missing optional deps — do not raise here (lazy loading)."""
        if not _PIL_AVAILABLE:
            from llmos_bridge.logging import get_logger

            get_logger(__name__).warning(
                "omniparser_dep_missing",
                dep="Pillow",
                install="pip install llmos-bridge[vision]",
            )

    def get_manifest(self) -> ModuleManifest:
        return ModuleManifest(
            module_id=self.MODULE_ID,
            version=self.VERSION,
            description=(
                "Visual perception module powered by Microsoft OmniParser v2. "
                "Parses GUI screenshots into structured lists of interactable "
                "elements (icons, buttons, text fields) with bounding boxes and labels. "
                "Replace with any BaseVisionModule subclass for custom vision backends."
            ),
            platforms=[p.value for p in self.SUPPORTED_PLATFORMS],
            actions=[
                ActionSpec(
                    name="parse_screen",
                    description=(
                        "Parse a screenshot and return a structured list of UI elements "
                        "with labels, bounding boxes and confidence scores."
                    ),
                    params_schema={
                        "type": "object",
                        "properties": {
                            "screenshot_path": {
                                "type": "string",
                                "description": "Absolute path to a PNG/JPEG screenshot file.",
                            },
                            "box_threshold": {
                                "type": "number",
                                "description": "Override detection confidence threshold.",
                            },
                        },
                    },
                    returns="VisionParseResult dict with elements[], width, height, raw_ocr",
                    permission_required="screen_capture",
                    platforms=[p.value for p in self.SUPPORTED_PLATFORMS],
                ),
                ActionSpec(
                    name="capture_and_parse",
                    description=(
                        "Capture the current screen and immediately parse it into UI elements."
                    ),
                    params_schema={
                        "type": "object",
                        "properties": {
                            "monitor": {
                                "type": "integer",
                                "description": "Monitor index (0=primary). Default: 0.",
                            },
                            "region": {
                                "type": "object",
                                "description": "Optional crop region: {left, top, width, height}.",
                            },
                            "box_threshold": {
                                "type": "number",
                                "description": "Override detection confidence threshold.",
                            },
                        },
                    },
                    returns="VisionParseResult dict with elements[], width, height, raw_ocr",
                    permission_required="screen_capture",
                    platforms=[p.value for p in self.SUPPORTED_PLATFORMS],
                ),
                ActionSpec(
                    name="find_element",
                    description=(
                        "Parse the screen and find a specific UI element by label or type. "
                        "Returns the first matching element and its pixel coordinates."
                    ),
                    params_schema={
                        "type": "object",
                        "required": ["query"],
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "Label substring or description to search for.",
                            },
                            "element_type": {
                                "type": "string",
                                "description": "Filter by type: icon, button, text, input, link.",
                            },
                            "screenshot_path": {
                                "type": "string",
                                "description": "Optional path to an existing screenshot.",
                            },
                        },
                    },
                    returns=(
                        "{'found': bool, 'element': VisionElement | null, "
                        "'pixel_x': int, 'pixel_y': int}"
                    ),
                    permission_required="screen_capture",
                    platforms=[p.value for p in self.SUPPORTED_PLATFORMS],
                ),
                ActionSpec(
                    name="get_screen_text",
                    description=(
                        "Extract all visible text from the current screen using fast OCR. "
                        "Returns concatenated text without element bounding boxes."
                    ),
                    params_schema={
                        "type": "object",
                        "properties": {
                            "screenshot_path": {
                                "type": "string",
                                "description": "Optional path to an existing screenshot.",
                            },
                        },
                    },
                    returns="{'text': str, 'line_count': int}",
                    permission_required="screen_capture",
                    platforms=[p.value for p in self.SUPPORTED_PLATFORMS],
                ),
            ],
            tags=["vision", "perception", "gui", "ocr", "omniparser"],
            declared_permissions=["screen_capture"],
        )

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    @requires_permission(Permission.SCREEN_CAPTURE, reason="Parses screen content")
    async def _action_parse_screen(self, params: dict[str, Any]) -> dict[str, Any]:
        screenshot_path: str | None = params.get("screenshot_path")
        box_threshold: float | None = params.get("box_threshold")

        if screenshot_path is None:
            # Capture the screen on the fly.
            screenshot_bytes = await self._capture_screen(monitor=0)
        else:
            with open(screenshot_path, "rb") as f:
                screenshot_bytes = f.read()

        result = await self.parse_screen(
            screenshot_bytes=screenshot_bytes,
            box_threshold=box_threshold,
        )
        return result.to_dict()

    @requires_permission(Permission.SCREEN_CAPTURE, reason="Captures and parses screen")
    async def _action_capture_and_parse(self, params: dict[str, Any]) -> dict[str, Any]:
        monitor: int = params.get("monitor", 0)
        region: dict[str, int] | None = params.get("region")
        box_threshold: float | None = params.get("box_threshold")

        screenshot_bytes = await self._capture_screen(monitor=monitor, region=region)
        result = await self.parse_screen(
            screenshot_bytes=screenshot_bytes,
            box_threshold=box_threshold,
        )
        return result.to_dict()

    @requires_permission(Permission.SCREEN_CAPTURE, reason="Finds element on screen")
    async def _action_find_element(self, params: dict[str, Any]) -> dict[str, Any]:
        query: str = params["query"]
        element_type: str | None = params.get("element_type")
        screenshot_path: str | None = params.get("screenshot_path")

        if screenshot_path:
            with open(screenshot_path, "rb") as f:
                screenshot_bytes = f.read()
        else:
            screenshot_bytes = await self._capture_screen(monitor=0)

        result = await self.parse_screen(screenshot_bytes=screenshot_bytes)

        candidates = result.find_by_label(query)
        if element_type:
            candidates = [e for e in candidates if e.element_type == element_type]

        if not candidates:
            return {"found": False, "element": None, "pixel_x": None, "pixel_y": None}

        best = candidates[0]
        px, py = best.pixel_center(result.width, result.height)
        return {
            "found": True,
            "element": best.model_dump(),
            "pixel_x": px,
            "pixel_y": py,
        }

    @requires_permission(Permission.SCREEN_CAPTURE, reason="Extracts text from screen")
    async def _action_get_screen_text(self, params: dict[str, Any]) -> dict[str, Any]:
        screenshot_path: str | None = params.get("screenshot_path")

        if screenshot_path:
            with open(screenshot_path, "rb") as f:
                screenshot_bytes = f.read()
        else:
            screenshot_bytes = await self._capture_screen(monitor=0)

        result = await self.parse_screen(screenshot_bytes=screenshot_bytes)
        text = result.raw_ocr or " ".join(
            e.text for e in result.elements if e.text
        )
        lines = [ln for ln in text.splitlines() if ln.strip()]
        return {"text": text, "line_count": len(lines)}

    # ------------------------------------------------------------------
    # BaseVisionModule.parse_screen implementation
    # ------------------------------------------------------------------

    async def parse_screen(
        self,
        screenshot_path: str | None = None,
        screenshot_bytes: bytes | None = None,
        *,
        width: int | None = None,
        height: int | None = None,
        box_threshold: float | None = None,
    ) -> VisionParseResult:
        """Parse a screenshot using OmniParser v2.

        Falls back to a lightweight PIL/OCR-only result when OmniParser
        model weights or dependencies are not available.
        """
        self._assert_pil_available()

        t0 = time.perf_counter()

        # Load image.
        image = self._load_image(screenshot_path=screenshot_path, screenshot_bytes=screenshot_bytes)
        img_width, img_height = image.size

        if not _OMNIPARSER_AVAILABLE or not _TORCH_AVAILABLE:
            # Graceful degradation: PIL-only path (no bounding boxes).
            return self._parse_pil_only(image, img_width, img_height, t0)

        # Full OmniParser path.
        api = self._get_api()
        threshold = box_threshold if box_threshold is not None else self._box_thresh

        try:
            parse_result = api.process(
                image=image,
                box_threshold=threshold,
                iou_threshold=self._iou_thresh,
            )
            elements = self._convert_elements(parse_result, img_width, img_height)
            raw_ocr = self._extract_raw_ocr(elements)
            labeled_b64 = self._encode_labeled_image(parse_result)
        except Exception as exc:
            raise ActionExecutionError(
                module_id=self.MODULE_ID,
                action="parse_screen",
                cause=exc,
            ) from exc

        elapsed_ms = (time.perf_counter() - t0) * 1000
        return VisionParseResult(
            elements=elements,
            width=img_width,
            height=img_height,
            raw_ocr=raw_ocr,
            labeled_image_b64=labeled_b64,
            parse_time_ms=elapsed_ms,
            model_id=f"omniparser-v2/{self.VERSION}",
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _assert_pil_available(self) -> None:
        if not _PIL_AVAILABLE:
            raise ModuleLoadError(
                module_id=self.MODULE_ID,
                reason=(
                    "Pillow is required for visual perception. "
                    "Install it with: pip install llmos-bridge[vision]"
                ),
            )

    def _get_api(self) -> Any:
        """Lazy-load the OmniParser API and model weights."""
        if self._api is not None:
            return self._api

        if not _OMNIPARSER_AVAILABLE:
            raise ModuleLoadError(
                module_id=self.MODULE_ID,
                reason=(
                    f"OmniParser is not installed ({_OMNIPARSER_IMPORT_ERROR}). "
                    "Install with: pip install llmos-bridge[vision] "
                    "or: pip install omniparser  (requires model weights at "
                    f"{self._model_dir})"
                ),
            )

        device = self._resolve_device()
        try:
            self._api = _OmniParserAPI(
                model_dir=self._model_dir,
                device=device,
            )
        except Exception as exc:
            raise ModuleLoadError(
                module_id=self.MODULE_ID,
                reason=(
                    f"Failed to load OmniParser weights from '{self._model_dir}': {exc}. "
                    "Download weights from: "
                    "https://huggingface.co/microsoft/OmniParser-v2.0 "
                    f"and place them in: {self._model_dir}"
                ),
            ) from exc

        return self._api

    def _resolve_device(self) -> str:
        if self._device != "auto":
            return self._device
        if _TORCH_AVAILABLE:
            import torch  # noqa: PLC0415 (local import intentional)

            if torch.cuda.is_available():
                return "cuda"
            if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                return "mps"
        return "cpu"

    def _load_image(
        self,
        screenshot_path: str | None,
        screenshot_bytes: bytes | None,
    ) -> Any:  # PIL.Image.Image
        from PIL import Image  # noqa: PLC0415

        if screenshot_bytes is not None:
            return Image.open(io.BytesIO(screenshot_bytes)).convert("RGB")
        if screenshot_path is not None:
            return Image.open(screenshot_path).convert("RGB")
        raise ActionExecutionError(
            module_id=self.MODULE_ID,
            action="parse_screen",
            cause=ValueError("Either screenshot_path or screenshot_bytes must be provided."),
        )

    def _parse_pil_only(
        self, image: Any, width: int, height: int, t0: float
    ) -> VisionParseResult:
        """Return an empty-elements result with a best-effort OCR extraction."""
        raw_text: str | None = None
        error: str | None = None
        try:
            import pytesseract  # noqa: PLC0415 (optional)

            raw_text = pytesseract.image_to_string(image)
        except Exception as exc:
            error = f"OCR skipped: {exc}"

        elapsed_ms = (time.perf_counter() - t0) * 1000
        return VisionParseResult(
            elements=[],
            width=width,
            height=height,
            raw_ocr=raw_text,
            labeled_image_b64=None,
            parse_time_ms=elapsed_ms,
            model_id="omniparser-v2/pil-fallback",
            error=error or "OmniParser not available — elements list is empty.",
        )

    @staticmethod
    def _convert_elements(
        parse_result: Any, img_width: int, img_height: int
    ) -> list[VisionElement]:
        """Convert OmniParser raw output to ``VisionElement`` objects."""
        elements: list[VisionElement] = []

        # OmniParser v2 returns a list of dicts with keys:
        #   bbox (xyxy normalised), label, type, confidence, text (optional)
        raw_items = getattr(parse_result, "elements", None) or []
        for i, item in enumerate(raw_items):
            if isinstance(item, dict):
                bbox_raw = item.get("bbox", [0, 0, 0, 0])
            else:
                bbox_raw = getattr(item, "bbox", [0, 0, 0, 0])

            # Normalise to [0, 1] if values are in pixel space.
            x1, y1, x2, y2 = bbox_raw
            if max(x1, y1, x2, y2) > 1.0:
                x1 /= img_width
                x2 /= img_width
                y1 /= img_height
                y2 /= img_height

            label = (item.get("label") if isinstance(item, dict) else getattr(item, "label", ""))
            element_type = (
                item.get("type", "icon") if isinstance(item, dict)
                else getattr(item, "type", "icon")
            )
            confidence = float(
                item.get("confidence", 1.0) if isinstance(item, dict)
                else getattr(item, "confidence", 1.0)
            )
            text = (item.get("text") if isinstance(item, dict) else getattr(item, "text", None))

            elements.append(
                VisionElement(
                    element_id=f"e{i:04d}",
                    label=str(label or ""),
                    element_type=str(element_type or "icon"),
                    bbox=(float(x1), float(y1), float(x2), float(y2)),
                    confidence=min(max(confidence, 0.0), 1.0),
                    text=str(text) if text else None,
                )
            )

        return elements

    @staticmethod
    def _extract_raw_ocr(elements: list[VisionElement]) -> str:
        """Concatenate all text fields from elements into a single string."""
        texts = [e.text for e in elements if e.text]
        return " ".join(texts) if texts else ""

    @staticmethod
    def _encode_labeled_image(parse_result: Any) -> str | None:
        """Return base64-encoded labeled image from parse_result, or None."""
        labeled = getattr(parse_result, "labeled_image", None)
        if labeled is None:
            return None
        try:
            if not _PIL_AVAILABLE:
                return None
            from PIL import Image  # noqa: PLC0415

            if isinstance(labeled, Image.Image):
                buf = io.BytesIO()
                labeled.save(buf, format="PNG")
                return base64.b64encode(buf.getvalue()).decode()
        except Exception:
            pass
        return None

    @staticmethod
    async def _capture_screen(
        monitor: int = 0, region: dict[str, int] | None = None
    ) -> bytes:
        """Capture the screen using mss and return PNG bytes."""
        try:
            import mss  # noqa: PLC0415
            import mss.tools  # noqa: PLC0415
        except ImportError as exc:
            raise ModuleLoadError(
                module_id="vision",
                reason=(
                    "mss is required for screen capture. "
                    "Install with: pip install llmos-bridge[vision]"
                ),
            ) from exc

        with mss.mss() as sct:
            if region:
                area = {
                    "left": region.get("left", 0),
                    "top": region.get("top", 0),
                    "width": region.get("width", 1920),
                    "height": region.get("height", 1080),
                }
            else:
                monitors = sct.monitors
                # monitors[0] is the combined virtual screen; monitors[1+] are physical.
                area = monitors[min(monitor + 1, len(monitors) - 1)]

            screenshot = sct.grab(area)
            return mss.tools.to_png(screenshot.rgb, screenshot.size)
