"""Visual perception module — OmniParser v2 (Microsoft) backend.

OmniParser is an open-source GUI understanding model that converts
screenshots into a structured list of interactable elements with labels
and bounding boxes.  It combines three pre-trained components:

  1. **YOLO v8** — fine-tuned icon/button detection on 67K screenshots
  2. **Florence-2** — fine-tuned icon captioning (describes what each icon is)
  3. **PaddleOCR / EasyOCR** — text extraction from the screen

Integration approach:
    The user clones the OmniParser repo locally and our module imports it
    via ``sys.path``.  Model weights are auto-downloaded from HuggingFace
    ``microsoft/OmniParser-v2.0`` on first use.

Setup::

    git clone https://github.com/microsoft/OmniParser.git ~/.llmos/omniparser
    cd ~/.llmos/omniparser && pip install -r requirements.txt
    pip install llmos-bridge[vision]

The module wraps OmniParser behind the ``BaseVisionModule`` contract so
users can swap it for any alternative (e.g. GPT-4V, Gemini, a custom model)
by registering a different class as ``MODULE_ID = "vision"``.

Actions:
    - ``parse_screen``        — parse current screen or a given screenshot
    - ``capture_and_parse``   — take a screenshot then parse it
    - ``find_element``        — find a UI element by label/type/description
    - ``get_screen_text``     — extract all text from the current screen
"""

from __future__ import annotations

import base64
import io
import os
import sys
import time
from typing import Any

from llmos_bridge.exceptions import ActionExecutionError, ModuleLoadError
from llmos_bridge.modules.base import Platform
from llmos_bridge.modules.manifest import ActionSpec, ModuleManifest, ParamSpec
from llmos_bridge.modules.perception_vision.base import (
    BaseVisionModule,
    VisionElement,
    VisionParseResult,
)
from llmos_bridge.security.decorators import requires_permission
from llmos_bridge.security.models import Permission

# ── optional dependencies ────────────────────────────────────────────────────
try:
    from PIL import Image as PILImage  # noqa: F401

    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False

try:
    import torch  # noqa: F401

    _TORCH_AVAILABLE = True
except ImportError:
    _TORCH_AVAILABLE = False


# ── lazy OmniParser import ───────────────────────────────────────────────────

def _import_omniparser(omniparser_path: str) -> type:
    """Import the real ``Omniparser`` class from a local clone of the
    Microsoft OmniParser repository via sys.path injection.

    The clone is expected at *omniparser_path* (e.g. ``~/.llmos/omniparser``).
    """
    expanded = os.path.expanduser(omniparser_path)
    if expanded not in sys.path:
        sys.path.insert(0, expanded)
    # Real import from util/omniparser.py in the cloned repo.
    from util.omniparser import Omniparser  # type: ignore[import-untyped]

    return Omniparser


# ─────────────────────────────────────────────────────────────────────────────


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
        LLMOS_OMNIPARSER_PATH          Path to the cloned OmniParser repo
                                       (default: ~/.llmos/omniparser)
        LLMOS_OMNIPARSER_MODEL_DIR     Path to model weights directory
                                       (default: ~/.llmos/models/omniparser)
        LLMOS_OMNIPARSER_DEVICE        torch device, e.g. "cpu", "cuda", "mps"
                                       (default: auto-detect)
        LLMOS_OMNIPARSER_BOX_THRESH    Detection confidence threshold [0-1]
                                       (default: 0.05)
        LLMOS_OMNIPARSER_IOU_THRESH    NMS IoU threshold [0-1]
                                       (default: 0.1)
        LLMOS_OMNIPARSER_CAPTION_MODEL Caption model: "florence2" or "blip2"
                                       (default: florence2)
        LLMOS_OMNIPARSER_USE_PADDLEOCR Use PaddleOCR instead of EasyOCR
                                       (default: true)
        LLMOS_OMNIPARSER_AUTO_DOWNLOAD Auto-download weights from HuggingFace
                                       (default: true)
    """

    MODULE_ID = "vision"
    VERSION = "2.0.0"
    SUPPORTED_PLATFORMS = [Platform.LINUX, Platform.WINDOWS, Platform.MACOS]

    def __init__(self) -> None:
        self._omniparser_path = os.environ.get(
            "LLMOS_OMNIPARSER_PATH",
            os.path.expanduser("~/.llmos/omniparser"),
        )
        self._model_dir = os.path.expanduser(
            os.environ.get("LLMOS_OMNIPARSER_MODEL_DIR", "~/.llmos/models/omniparser")
        )
        self._device = os.environ.get("LLMOS_OMNIPARSER_DEVICE", "auto")
        self._box_thresh = float(os.environ.get("LLMOS_OMNIPARSER_BOX_THRESH", "0.05"))
        self._iou_thresh = float(os.environ.get("LLMOS_OMNIPARSER_IOU_THRESH", "0.1"))
        self._caption_model = os.environ.get("LLMOS_OMNIPARSER_CAPTION_MODEL", "florence2")
        self._use_paddleocr = (
            os.environ.get("LLMOS_OMNIPARSER_USE_PADDLEOCR", "true").lower() == "true"
        )
        self._auto_download = (
            os.environ.get("LLMOS_OMNIPARSER_AUTO_DOWNLOAD", "true").lower() == "true"
        )
        self._api: Any | None = None  # Lazy-loaded Omniparser instance
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
                "Combines YOLO v8 detection, Florence-2 captioning, and OCR to "
                "parse GUI screenshots into structured lists of interactable "
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
                    params=[
                        ParamSpec("screenshot_path", "string", "Absolute path to a PNG/JPEG screenshot file.", required=False),
                        ParamSpec("box_threshold", "number", "Override detection confidence threshold.", required=False),
                    ],
                    returns_description="VisionParseResult dict with elements[], width, height, raw_ocr",
                    permission_required="screen_capture",
                    platforms=[p.value for p in self.SUPPORTED_PLATFORMS],
                ),
                ActionSpec(
                    name="capture_and_parse",
                    description=(
                        "Capture the current screen and immediately parse it into UI elements."
                    ),
                    params=[
                        ParamSpec("monitor", "integer", "Monitor index (0=primary). Default: 0.", required=False, default=0),
                        ParamSpec("region", "object", "Optional crop region: {left, top, width, height}.", required=False),
                        ParamSpec("box_threshold", "number", "Override detection confidence threshold.", required=False),
                    ],
                    returns_description="VisionParseResult dict with elements[], width, height, raw_ocr",
                    permission_required="screen_capture",
                    platforms=[p.value for p in self.SUPPORTED_PLATFORMS],
                ),
                ActionSpec(
                    name="find_element",
                    description=(
                        "Parse the screen and find a specific UI element by label or type. "
                        "Returns the first matching element and its pixel coordinates."
                    ),
                    params=[
                        ParamSpec("query", "string", "Label substring or description to search for.", required=True),
                        ParamSpec("element_type", "string", "Filter by type: icon, button, text, input, link.", required=False),
                        ParamSpec("screenshot_path", "string", "Optional path to an existing screenshot.", required=False),
                    ],
                    returns_description=(
                        "{'found': bool, 'element': VisionElement | null, "
                        "'pixel_x': int, 'pixel_y': int}"
                    ),
                    permission_required="screen_capture",
                    platforms=[p.value for p in self.SUPPORTED_PLATFORMS],
                ),
                ActionSpec(
                    name="get_screen_text",
                    description=(
                        "Extract all visible text from the current screen using OCR. "
                        "Returns concatenated text without element bounding boxes."
                    ),
                    params=[
                        ParamSpec("screenshot_path", "string", "Optional path to an existing screenshot.", required=False),
                    ],
                    returns_description="{'text': str, 'line_count': int}",
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
        """Parse a screenshot using the real OmniParser v2 pipeline.

        Pipeline: YOLO detection → OCR → overlap removal → Florence-2 captioning.

        Falls back to a lightweight PIL/OCR-only result when OmniParser
        code or dependencies are not available.
        """
        self._assert_pil_available()

        t0 = time.perf_counter()

        # Load image as PIL.
        image = self._load_image(screenshot_path=screenshot_path, screenshot_bytes=screenshot_bytes)
        img_width, img_height = image.size

        if not self._is_omniparser_available():
            # Graceful degradation: OCR-only path (no bounding boxes).
            return self._parse_pil_only(image, img_width, img_height, t0)

        # Full OmniParser path.
        api = self._get_api()

        # OmniParser.parse() expects a base64-encoded image string.
        image_b64 = self._pil_to_base64(image)

        try:
            som_image_b64, parsed_content_list = api.parse(image_b64)
        except Exception as exc:
            raise ActionExecutionError(
                module_id=self.MODULE_ID,
                action="parse_screen",
                cause=exc,
            ) from exc

        elements = self._convert_omniparser_output(parsed_content_list)
        raw_ocr = self._extract_raw_ocr(elements)
        elapsed_ms = (time.perf_counter() - t0) * 1000

        return VisionParseResult(
            elements=elements,
            width=img_width,
            height=img_height,
            raw_ocr=raw_ocr,
            labeled_image_b64=som_image_b64,  # Already base64 from OmniParser
            parse_time_ms=elapsed_ms,
            model_id="omniparser-v2",
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

    def _is_omniparser_available(self) -> bool:
        """Check if the OmniParser clone is present on disk."""
        expanded = os.path.expanduser(self._omniparser_path)
        return os.path.isdir(expanded) and os.path.exists(
            os.path.join(expanded, "util", "omniparser.py")
        )

    def _ensure_weights(self) -> None:
        """Download model weights from HuggingFace if not already present.

        Downloads from ``microsoft/OmniParser-v2.0``:
          - ``icon_detect/model.pt``     — YOLO v8 (UI element detection)
          - ``icon_caption_florence/``    — Florence-2 (icon captioning)
        """
        som_model = os.path.join(self._model_dir, "icon_detect", "model.pt")
        caption_safetensors = os.path.join(
            self._model_dir, "icon_caption_florence", "model.safetensors"
        )

        if os.path.exists(som_model) and os.path.exists(caption_safetensors):
            return  # Weights already present.

        try:
            from huggingface_hub import snapshot_download  # noqa: PLC0415
        except ImportError as exc:
            raise ModuleLoadError(
                module_id=self.MODULE_ID,
                reason=(
                    "huggingface_hub is required for automatic weight download. "
                    "Install with: pip install huggingface-hub  "
                    "Or download weights manually from: "
                    "https://huggingface.co/microsoft/OmniParser-v2.0"
                ),
            ) from exc

        from llmos_bridge.logging import get_logger  # noqa: PLC0415

        log = get_logger(__name__)
        log.info("omniparser_downloading_weights", model_dir=self._model_dir)

        os.makedirs(self._model_dir, exist_ok=True)
        snapshot_download(
            "microsoft/OmniParser-v2.0",
            allow_patterns=["icon_detect/*", "icon_caption/*"],
            local_dir=self._model_dir,
        )

        # OmniParser expects 'icon_caption_florence' but HuggingFace downloads as 'icon_caption'.
        src = os.path.join(self._model_dir, "icon_caption")
        dst = os.path.join(self._model_dir, "icon_caption_florence")
        if os.path.isdir(src) and not os.path.isdir(dst):
            os.rename(src, dst)

        log.info("omniparser_weights_ready", model_dir=self._model_dir)

    def _get_api(self) -> Any:
        """Lazy-load the real OmniParser API with pre-trained model weights."""
        if self._api is not None:
            return self._api

        # Auto-download weights from HuggingFace if enabled.
        if self._auto_download:
            self._ensure_weights()

        # Verify the OmniParser clone exists.
        expanded = os.path.expanduser(self._omniparser_path)
        omni_py = os.path.join(expanded, "util", "omniparser.py")
        if not os.path.exists(omni_py):
            raise ModuleLoadError(
                module_id=self.MODULE_ID,
                reason=(
                    f"OmniParser repository not found at '{self._omniparser_path}'. "
                    "Clone it with:\n"
                    f"  git clone https://github.com/microsoft/OmniParser.git {self._omniparser_path}\n"
                    f"  cd {self._omniparser_path} && pip install -r requirements.txt"
                ),
            )

        # Verify model weights exist.
        som_model_path = os.path.join(self._model_dir, "icon_detect", "model.pt")
        caption_model_path = os.path.join(self._model_dir, "icon_caption_florence")
        if not os.path.exists(som_model_path):
            raise ModuleLoadError(
                module_id=self.MODULE_ID,
                reason=(
                    f"YOLO model weights not found at '{som_model_path}'. "
                    "Download from: https://huggingface.co/microsoft/OmniParser-v2.0 "
                    f"or enable auto_download_weights in config."
                ),
            )

        # Import and instantiate the real OmniParser.
        OmniparserClass = _import_omniparser(self._omniparser_path)

        # Build config dict matching the real OmniParser constructor signature.
        # Note: 'BOX_TRESHOLD' is the original typo in OmniParser's code.
        config = {
            "som_model_path": som_model_path,
            "caption_model_name": self._caption_model,
            "caption_model_path": caption_model_path,
            "BOX_TRESHOLD": self._box_thresh,
        }

        try:
            self._api = OmniparserClass(config)
        except Exception as exc:
            raise ModuleLoadError(
                module_id=self.MODULE_ID,
                reason=f"Failed to initialize OmniParser: {exc}",
            ) from exc

        return self._api

    def _resolve_device(self) -> str:
        if self._device != "auto":
            return self._device
        if _TORCH_AVAILABLE:
            import torch  # noqa: PLC0415

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

    @staticmethod
    def _pil_to_base64(image: Any) -> str:
        """Convert a PIL Image to a base64-encoded PNG string.

        This is the format expected by ``Omniparser.parse()``.
        """
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode()

    def _parse_pil_only(
        self, image: Any, width: int, height: int, t0: float
    ) -> VisionParseResult:
        """Return an empty-elements result with a best-effort OCR extraction.

        Used when OmniParser is not available (code not cloned or deps missing).
        """
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
            error=error or (
                "OmniParser not available — clone the repo with: "
                f"git clone https://github.com/microsoft/OmniParser.git {self._omniparser_path}"
            ),
        )

    @staticmethod
    def _convert_omniparser_output(
        parsed_content_list: list[dict[str, Any]],
    ) -> list[VisionElement]:
        """Convert the real OmniParser v2 output to ``VisionElement`` objects.

        OmniParser returns elements in this format::

            {
                'type': 'text' | 'icon',
                'bbox': [x1, y1, x2, y2],       # normalised [0, 1]
                'interactivity': True | False,
                'content': 'Button label or caption',
                'source': 'box_ocr_content_ocr' | 'box_yolo_content_yolo' | 'box_yolo_content_ocr'
            }
        """
        elements: list[VisionElement] = []
        for i, item in enumerate(parsed_content_list):
            bbox_raw = item.get("bbox", [0, 0, 0, 0])
            content = item.get("content", "")
            elem_type = item.get("type", "icon")
            interactable = item.get("interactivity", elem_type == "icon")
            source = item.get("source", "")

            elements.append(
                VisionElement(
                    element_id=f"e{i:04d}",
                    label=str(content),
                    element_type=str(elem_type),
                    bbox=(
                        float(bbox_raw[0]),
                        float(bbox_raw[1]),
                        float(bbox_raw[2]),
                        float(bbox_raw[3]),
                    ),
                    confidence=1.0,  # OmniParser v2 does not provide per-element scores
                    text=str(content) if elem_type == "text" else None,
                    interactable=bool(interactable),
                    extra={"source": source},
                )
            )
        return elements

    @staticmethod
    def _extract_raw_ocr(elements: list[VisionElement]) -> str:
        """Concatenate all text fields from elements into a single string."""
        texts = [e.text for e in elements if e.text]
        return " ".join(texts) if texts else ""

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
