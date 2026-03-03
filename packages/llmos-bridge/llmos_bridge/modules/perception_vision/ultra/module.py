"""UltraVision module — GUI-trained perception engine.

Combines three models specifically trained on GUI data:

  1. **UI-DETR-1** — RF-DETR-M GUI element detection (~300ms GPU)
  2. **PP-OCRv5** — 106-language OCR on screen text (~400ms CPU)
  3. **UGround-V1-2B** — Visual grounding for find_element (~500ms GPU)

Key innovation: eliminates the Florence-2 captioning bottleneck (~2-3s
in OmniParser) by using OCR text + heuristic classification instead.

Pipeline:  UI-DETR ∥ OCR → Merge → Classify → SoM → SceneGraph
Total:     ~500-900ms (vs OmniParser ~4500ms)

Activate via config::

    vision:
      backend: "ultra"

Actions:
    - ``parse_screen``        — detect + OCR + classify all elements
    - ``capture_and_parse``   — capture screenshot then parse
    - ``find_element``        — UGround visual grounding (or fallback)
    - ``get_screen_text``     — extract all text via OCR
"""

from __future__ import annotations

import asyncio
import io
import os
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


class UltraVisionModule(BaseVisionModule):
    """GUI-trained perception engine — alternative to OmniParser.

    Uses models trained specifically on GUI screenshots — not
    general-purpose CLIP or natural image models.

    Configuration (env vars):
        LLMOS_ULTRA_VISION_MODEL_DIR       Base directory for model weights
        LLMOS_ULTRA_VISION_DEVICE          Torch device (auto/cpu/cuda/mps)
        LLMOS_ULTRA_VISION_BOX_THRESH      Detection confidence threshold
        LLMOS_ULTRA_VISION_OCR_ENGINE      'paddleocr' or 'easyocr'
        LLMOS_ULTRA_VISION_ENABLE_GROUNDING  Enable UGround for find_element
        LLMOS_ULTRA_VISION_AUTO_DOWNLOAD   Auto-download from HuggingFace
    """

    MODULE_ID = "vision"
    VERSION = "1.0.0"
    SUPPORTED_PLATFORMS = [Platform.LINUX, Platform.WINDOWS, Platform.MACOS]

    def __init__(self) -> None:
        self._model_dir = os.path.expanduser(
            os.environ.get("LLMOS_ULTRA_VISION_MODEL_DIR", "~/.llmos/models/ultra_vision")
        )
        self._device = os.environ.get("LLMOS_ULTRA_VISION_DEVICE", "auto")
        self._box_threshold = float(
            os.environ.get("LLMOS_ULTRA_VISION_BOX_THRESH", "0.3")
        )
        self._ocr_engine_name = os.environ.get("LLMOS_ULTRA_VISION_OCR_ENGINE", "paddleocr")
        self._enable_grounding = (
            os.environ.get("LLMOS_ULTRA_VISION_ENABLE_GROUNDING", "true").lower() == "true"
        )
        self._auto_download = (
            os.environ.get("LLMOS_ULTRA_VISION_AUTO_DOWNLOAD", "true").lower() == "true"
        )

        # Lazy-loaded backends.
        self._detector: Any = None
        self._ocr: Any = None
        self._grounder: Any = None
        self._classifier: Any = None
        self._som_renderer: Any = None
        self._weight_manager: Any = None
        self._vram_budget: Any = None
        self._cache: Any = None

        super().__init__()

    async def on_stop(self) -> None:
        """Release GPU models and clear cache on module shutdown."""
        self._detector = None
        self._ocr = None
        self._grounder = None
        self._classifier = None
        self._som_renderer = None
        self._weight_manager = None
        self._vram_budget = None
        if self._cache is not None:
            self._cache.clear()
            self._cache = None

    # ------------------------------------------------------------------
    # Lazy initialisation
    # ------------------------------------------------------------------

    def _get_weight_manager(self) -> Any:
        if self._weight_manager is not None:
            return self._weight_manager
        from llmos_bridge.modules.perception_vision.ultra.weight_manager import (  # noqa: PLC0415
            WeightManager,
        )
        self._weight_manager = WeightManager(
            base_dir=self._model_dir,
            auto_download=self._auto_download,
        )
        return self._weight_manager

    def _get_vram_budget(self) -> Any:
        if self._vram_budget is not None:
            return self._vram_budget
        from llmos_bridge.modules.perception_vision.ultra.weight_manager import VRAMBudget  # noqa: PLC0415
        max_vram = int(os.environ.get("LLMOS_ULTRA_VISION_MAX_VRAM_MB", "3000"))
        self._vram_budget = VRAMBudget(max_mb=max_vram)
        return self._vram_budget

    def _get_cache(self) -> Any:
        if self._cache is not None:
            return self._cache
        try:
            from llmos_bridge.config import get_settings  # noqa: PLC0415
            from llmos_bridge.modules.perception_vision.cache import PerceptionCache  # noqa: PLC0415
            cfg = get_settings().vision
            if cfg.cache_max_entries > 0:
                self._cache = PerceptionCache(
                    max_entries=cfg.cache_max_entries,
                    ttl_seconds=cfg.cache_ttl_seconds,
                )
        except Exception:
            pass
        return self._cache

    def _get_detector(self) -> Any:
        if self._detector is not None:
            return self._detector
        from llmos_bridge.modules.perception_vision.ultra.backends.detector import (  # noqa: PLC0415
            UIDetrDetector,
        )
        from llmos_bridge.modules.perception_vision.ultra.weight_manager import (  # noqa: PLC0415
            UI_DETR_SPEC,
        )
        wm = self._get_weight_manager()
        model_path = wm.ensure_model(UI_DETR_SPEC)
        budget = self._get_vram_budget()
        budget.allocate("ui-detr", UI_DETR_SPEC.vram_mb)
        self._detector = UIDetrDetector(model_path=model_path)
        self._detector.load(device=self._device)
        return self._detector

    def _get_ocr(self) -> Any:
        if self._ocr is not None:
            return self._ocr
        if self._ocr_engine_name == "paddleocr":
            try:
                from llmos_bridge.modules.perception_vision.ultra.backends.ocr import (  # noqa: PLC0415
                    PPOCRv5Engine,
                )
                self._ocr = PPOCRv5Engine()
                self._ocr.load()
                return self._ocr
            except Exception:
                self._ocr = None  # Fallback to EasyOCR.

        try:
            from llmos_bridge.modules.perception_vision.ultra.backends.ocr import (  # noqa: PLC0415
                EasyOCRFallback,
            )
            self._ocr = EasyOCRFallback()
            self._ocr.load()
            return self._ocr
        except ImportError as exc:
            raise ModuleLoadError(
                module_id=self.MODULE_ID,
                reason=(
                    "No OCR engine available. Install paddleocr or easyocr: "
                    "pip install paddleocr paddlepaddle  OR  pip install easyocr"
                ),
            ) from exc

    def _get_grounder(self) -> Any:
        if self._grounder is not None:
            return self._grounder
        if not self._enable_grounding:
            return None
        try:
            from llmos_bridge.modules.perception_vision.ultra.backends.grounder import (  # noqa: PLC0415
                UGroundGrounder,
            )
            from llmos_bridge.modules.perception_vision.ultra.weight_manager import (  # noqa: PLC0415
                UGROUND_SPEC,
            )
            budget = self._get_vram_budget()
            if not budget.can_allocate("uground", UGROUND_SPEC.vram_mb):
                return None
            wm = self._get_weight_manager()
            model_path = wm.ensure_model(UGROUND_SPEC)
            idle_timeout = float(
                os.environ.get("LLMOS_ULTRA_VISION_GROUNDING_IDLE_TIMEOUT", "60.0")
            )
            budget.allocate("uground", UGROUND_SPEC.vram_mb)
            self._grounder = UGroundGrounder(
                model_path=model_path,
                idle_timeout=idle_timeout,
            )
            self._grounder.load(device=self._device)
            return self._grounder
        except Exception:
            return None

    def _get_classifier(self) -> Any:
        if self._classifier is not None:
            return self._classifier
        from llmos_bridge.modules.perception_vision.ultra.classifier import (  # noqa: PLC0415
            ElementClassifier,
        )
        self._classifier = ElementClassifier()
        return self._classifier

    def _get_som_renderer(self) -> Any:
        if self._som_renderer is not None:
            return self._som_renderer
        from llmos_bridge.modules.perception_vision.ultra.som import SoMRenderer  # noqa: PLC0415
        self._som_renderer = SoMRenderer()
        return self._som_renderer

    # ------------------------------------------------------------------
    # BaseModule contract
    # ------------------------------------------------------------------

    def _check_dependencies(self) -> None:
        if not _PIL_AVAILABLE:
            from llmos_bridge.logging import get_logger  # noqa: PLC0415
            get_logger(__name__).warning(
                "ultra_vision_dep_missing",
                dep="Pillow",
                install="pip install llmos-bridge[ultra-vision]",
            )

    def get_manifest(self) -> ModuleManifest:
        return ModuleManifest(
            module_id=self.MODULE_ID,
            version=self.VERSION,
            description=(
                "UltraVision perception module — GUI-trained element detection "
                "(UI-DETR-1), 106-language OCR (PP-OCRv5), and visual grounding "
                "(UGround-V1-2B). ~5-9x faster than OmniParser by eliminating "
                "the Florence-2 captioning bottleneck. All models are trained "
                "specifically on GUI screenshots."
            ),
            platforms=[p.value for p in self.SUPPORTED_PLATFORMS],
            actions=[
                ActionSpec(
                    name="parse_screen",
                    description=(
                        "Parse a screenshot using GUI-trained detection + OCR. "
                        "Returns structured UI elements with labels and bounding boxes."
                    ),
                    params=[
                        ParamSpec("screenshot_path", "string", "Absolute path to a PNG/JPEG screenshot.", required=False),
                        ParamSpec("box_threshold", "number", "Override detection confidence threshold.", required=False),
                    ],
                    returns_description="VisionParseResult dict with elements[], width, height, raw_ocr",
                    permission_required="screen_capture",
                    platforms=[p.value for p in self.SUPPORTED_PLATFORMS],
                ),
                ActionSpec(
                    name="capture_and_parse",
                    description="Capture the current screen and parse it into UI elements.",
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
                        "Find a UI element by natural language query using UGround "
                        "visual grounding (or parse+match fallback)."
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
                    description="Extract all visible text from the current screen using OCR.",
                    params=[
                        ParamSpec("screenshot_path", "string", "Optional path to an existing screenshot.", required=False),
                    ],
                    returns_description="{'text': str, 'line_count': int}",
                    permission_required="screen_capture",
                    platforms=[p.value for p in self.SUPPORTED_PLATFORMS],
                ),
            ],
            tags=["vision", "perception", "gui", "ocr", "ultra", "grounding"],
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

        image = self._load_image(screenshot_bytes=screenshot_bytes)
        w, h = image.size

        # Try UGround visual grounding first (direct query → bbox).
        grounder = self._get_grounder()
        if grounder is not None:
            try:
                result = grounder.ground(image, query)
                if result is not None:
                    px = int((result.bbox[0] + result.bbox[2]) / 2 * w)
                    py = int((result.bbox[1] + result.bbox[3]) / 2 * h)
                    elem = VisionElement(
                        element_id="g0000",
                        label=query,
                        element_type=element_type or "button",
                        bbox=result.bbox,
                        confidence=result.confidence,
                        text=query,
                        interactable=True,
                        extra={"source": "uground_grounding"},
                    )
                    return {
                        "found": True,
                        "element": elem.model_dump(),
                        "pixel_x": px,
                        "pixel_y": py,
                    }
            except Exception:
                pass  # Fallback to parse+match.

        # Fallback: full parse + substring matching.
        parse_result = await self.parse_screen(screenshot_bytes=screenshot_bytes)
        candidates = parse_result.find_by_label(query)
        if element_type:
            candidates = [e for e in candidates if e.element_type == element_type]

        if not candidates:
            return {"found": False, "element": None, "pixel_x": None, "pixel_y": None}

        best = candidates[0]
        px, py = best.pixel_center(parse_result.width, parse_result.height)
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
        """Parse a screenshot using the UltraVision pipeline.

        Pipeline: Cache check → UI-DETR ∥ OCR → Merge → Classify → SoM → SceneGraph
        """
        self._assert_pil_available()

        t0 = time.perf_counter()

        # Check cache first.
        cache = self._get_cache()
        if cache is not None and screenshot_bytes is not None:
            cached = cache.get(screenshot_bytes)
            if cached is not None:
                return cached

        # Load image.
        image = self._load_image(
            screenshot_path=screenshot_path,
            screenshot_bytes=screenshot_bytes,
        )
        img_width, img_height = image.size

        if not _TORCH_AVAILABLE:
            return self._parse_pil_only(image, img_width, img_height, t0)

        threshold = box_threshold if box_threshold is not None else self._box_threshold

        # Run detection and OCR in parallel.
        loop = asyncio.get_event_loop()
        detection_future = loop.run_in_executor(
            None, self._run_detection, image, threshold,
        )
        ocr_future = loop.run_in_executor(
            None, self._run_ocr, image,
        )

        detection_output, ocr_output = await asyncio.gather(
            detection_future, ocr_future,
        )

        # Merge + classify.
        classifier = self._get_classifier()
        elements: list[VisionElement] = []

        for i, det in enumerate(detection_output.detections):
            cls_result = classifier.classify(
                det, ocr_output.boxes, img_width, img_height,
            )
            elements.append(VisionElement(
                element_id=f"e{i:04d}",
                label=cls_result.label,
                element_type=cls_result.element_type,
                bbox=det.bbox,
                confidence=det.confidence,
                text=cls_result.label if cls_result.element_type in ("text", "button", "link", "input") else None,
                interactable=cls_result.interactable,
                extra={"source": f"ultra_{detection_output.model_id}"},
            ))

        # Add OCR-only text regions that don't overlap with detections.
        existing_bboxes = [e.bbox for e in elements]
        for j, ocr_box in enumerate(ocr_output.boxes):
            if not self._overlaps_any(ocr_box.bbox, existing_bboxes, threshold=0.3):
                elements.append(VisionElement(
                    element_id=f"t{j:04d}",
                    label=ocr_box.text,
                    element_type="text",
                    bbox=ocr_box.bbox,
                    confidence=ocr_box.confidence,
                    text=ocr_box.text,
                    interactable=False,
                    extra={"source": f"ultra_{ocr_output.engine_id}"},
                ))

        # SoM overlay.
        labeled_image_b64: str | None = None
        try:
            som = self._get_som_renderer()
            labeled_image_b64 = som.render_to_base64(image, elements)
        except Exception:
            pass

        elapsed_ms = (time.perf_counter() - t0) * 1000
        result = VisionParseResult(
            elements=elements,
            width=img_width,
            height=img_height,
            raw_ocr=ocr_output.full_text,
            labeled_image_b64=labeled_image_b64,
            parse_time_ms=elapsed_ms,
            model_id=f"ultra-vision-{detection_output.model_id}",
        )

        # Build scene graph.
        try:
            from llmos_bridge.modules.perception_vision.scene_graph import SceneGraphBuilder  # noqa: PLC0415
            graph = SceneGraphBuilder().build(result)
            result.scene_graph_text = graph.to_compact_text()
        except Exception:
            pass

        # Store in cache.
        if cache is not None and screenshot_bytes is not None:
            cache.put(screenshot_bytes, result)

        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _run_detection(self, image: Any, threshold: float) -> Any:
        """Run detection synchronously (for thread executor)."""
        try:
            detector = self._get_detector()
            return detector.detect(image, confidence_threshold=threshold)
        except Exception as exc:
            from llmos_bridge.modules.perception_vision.ultra.backends.detector import (  # noqa: PLC0415
                DetectionOutput,
            )
            return DetectionOutput(
                detections=[], image_width=image.size[0], image_height=image.size[1],
                model_id="error", inference_time_ms=0,
            )

    def _run_ocr(self, image: Any) -> Any:
        """Run OCR synchronously (for thread executor)."""
        try:
            ocr = self._get_ocr()
            return ocr.recognize(image)
        except Exception:
            from llmos_bridge.modules.perception_vision.ultra.backends.ocr import OCROutput  # noqa: PLC0415
            return OCROutput(boxes=[], full_text="", inference_time_ms=0, engine_id="error")

    @staticmethod
    def _overlaps_any(
        bbox: tuple[float, float, float, float],
        existing: list[tuple[float, float, float, float]],
        threshold: float = 0.3,
    ) -> bool:
        """Check if a bbox overlaps significantly with any existing bbox."""
        for other in existing:
            x1 = max(bbox[0], other[0])
            y1 = max(bbox[1], other[1])
            x2 = min(bbox[2], other[2])
            y2 = min(bbox[3], other[3])
            inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
            area_bbox = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
            if area_bbox > 0 and (inter / area_bbox) >= threshold:
                return True
        return False

    def _assert_pil_available(self) -> None:
        if not _PIL_AVAILABLE:
            raise ModuleLoadError(
                module_id=self.MODULE_ID,
                reason=(
                    "Pillow is required for UltraVision. "
                    "Install with: pip install llmos-bridge[ultra-vision]"
                ),
            )

    def _load_image(
        self,
        screenshot_path: str | None = None,
        screenshot_bytes: bytes | None = None,
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
        self, image: Any, width: int, height: int, t0: float,
    ) -> VisionParseResult:
        """Fallback: OCR-only when torch is unavailable."""
        raw_text: str | None = None
        error: str | None = None
        try:
            import pytesseract  # noqa: PLC0415
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
            model_id="ultra-vision/pil-fallback",
            error=error or (
                "Torch not available for UltraVision. "
                "Install with: pip install torch"
            ),
        )

    @staticmethod
    async def _capture_screen(
        monitor: int = 0, region: dict[str, int] | None = None,
    ) -> bytes:
        """Capture the screen using mss and return PNG bytes."""
        try:
            import mss  # noqa: PLC0415
            import mss.tools  # noqa: PLC0415
        except ImportError as exc:
            raise ModuleLoadError(
                module_id="vision",
                reason="mss is required for screen capture. Install with: pip install mss",
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
                area = monitors[min(monitor + 1, len(monitors) - 1)]

            screenshot = sct.grab(area)
            return mss.tools.to_png(screenshot.rgb, screenshot.size)
