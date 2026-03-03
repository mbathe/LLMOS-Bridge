"""Visual grounding backend — abstract interface + UGround-V1-2B implementation.

UGround-V1-2B (osunlp/UGround-V1-2B) is trained on 10M GUI element
screenshots.  Given a natural language query ("the search button") and a
screenshot, it returns the bounding box of the matching element — without
needing to enumerate all elements first.

81.5% accuracy on ScreenSpot benchmark.
"""

from __future__ import annotations

import re
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class GroundingResult:
    """Result from visual grounding — a bbox for the queried element."""

    bbox: tuple[float, float, float, float]
    """Normalised bounding box (x1, y1, x2, y2) in [0, 1]."""

    confidence: float
    """Grounding confidence score in [0, 1]."""

    query: str
    """The original natural language query."""


class BaseGrounder(ABC):
    """Abstract interface for visual grounding models.

    Visual grounding: natural language query + screenshot → bounding box.
    """

    @abstractmethod
    def load(self, device: str = "auto") -> None:
        """Load model weights onto the specified device."""
        ...

    @abstractmethod
    def ground(self, image: Any, query: str) -> GroundingResult | None:
        """Find the element described by *query* in *image*.

        Args:
            image: PIL Image (RGB).
            query: Natural language description (e.g. "the Submit button").

        Returns:
            GroundingResult with normalised bbox, or None if not found.
        """
        ...

    @abstractmethod
    def unload(self) -> None:
        """Release model from GPU/CPU memory."""
        ...

    @property
    @abstractmethod
    def is_loaded(self) -> bool:
        """Whether the model weights are currently in memory."""
        ...

    @property
    @abstractmethod
    def vram_estimate_mb(self) -> int:
        """Estimated VRAM usage in megabytes when loaded."""
        ...


# ---------------------------------------------------------------------------
# Concrete: UGround-V1-2B (osunlp/UGround-V1-2B)
# ---------------------------------------------------------------------------

# Regex patterns to parse bbox from model output.
_BOX_PATTERNS = [
    # <box>x1 y1 x2 y2</box>
    re.compile(r"<box>\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*</box>"),
    # [x1, y1, x2, y2]
    re.compile(r"\[\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*\]"),
    # (x1, y1, x2, y2)
    re.compile(r"\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*\)"),
    # Raw coordinates: x1 y1 x2 y2 (four numbers on a line)
    re.compile(r"(?:^|\n)\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)"),
]


def _parse_bbox_from_text(text: str) -> tuple[float, float, float, float] | None:
    """Extract normalised bbox from model text output."""
    for pattern in _BOX_PATTERNS:
        m = pattern.search(text)
        if m:
            coords = [float(m.group(i)) for i in range(1, 5)]
            # If coords > 1, assume pixel coords — normalise.
            if any(c > 1.0 for c in coords):
                # UGround typically outputs in [0, 1000] scale.
                max_val = max(coords)
                scale = 1000.0 if max_val <= 1000 else max_val
                coords = [c / scale for c in coords]
            x1, y1, x2, y2 = coords
            # Ensure ordering.
            if x1 > x2:
                x1, x2 = x2, x1
            if y1 > y2:
                y1, y2 = y2, y1
            return (
                max(0.0, min(1.0, x1)),
                max(0.0, min(1.0, y1)),
                max(0.0, min(1.0, x2)),
                max(0.0, min(1.0, y2)),
            )
    return None


class UGroundGrounder(BaseGrounder):
    """Visual grounding using UGround-V1-2B (Qwen2-VL-2B architecture).

    Trained on 10M GUI element screenshots — directly resolves natural
    language queries to bounding boxes.

    Lazy-loaded and auto-unloaded after idle timeout to free VRAM.

    Requires: torch, transformers (built from source recommended),
              qwen-vl-utils (optional but recommended),
              bitsandbytes (optional, for INT4 quantization).
    VRAM: ~1500MB at INT4, ~4000MB at FP16.
    """

    VRAM_ESTIMATE_MB = 1500  # INT4 quantised

    def __init__(
        self,
        model_path: str | None = None,
        repo_id: str = "osunlp/UGround-V1-2B",
        use_4bit: bool = True,
        idle_timeout: float = 60.0,
    ) -> None:
        self._model_path = model_path
        self._repo_id = repo_id
        self._use_4bit = use_4bit
        self._idle_timeout = idle_timeout
        self._model: Any = None
        self._processor: Any = None
        self._device: str = "cpu"
        self._last_use: float = 0.0
        self._unload_timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def load(self, device: str = "auto") -> None:
        import torch  # noqa: PLC0415

        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self._device = device

        model_source = self._model_path or self._repo_id

        load_kwargs: dict[str, Any] = {
            "trust_remote_code": True,
        }

        if self._use_4bit and device != "cpu":
            try:
                from transformers import BitsAndBytesConfig  # noqa: PLC0415
                load_kwargs["quantization_config"] = BitsAndBytesConfig(
                    load_in_4bit=True,
                    bnb_4bit_compute_dtype=torch.float16,
                    bnb_4bit_quant_type="nf4",
                )
            except ImportError:
                load_kwargs["torch_dtype"] = torch.float16
        elif device != "cpu":
            load_kwargs["torch_dtype"] = torch.float16

        # UGround-V1-2B is a Qwen2-VL model — use the correct class.
        try:
            from transformers import Qwen2VLForConditionalGeneration  # noqa: PLC0415
            model_cls = Qwen2VLForConditionalGeneration
        except ImportError:
            # Older transformers — fall back to AutoModelForCausalLM.
            from transformers import AutoModelForCausalLM  # noqa: PLC0415
            model_cls = AutoModelForCausalLM

        from transformers import AutoProcessor  # noqa: PLC0415

        self._processor = AutoProcessor.from_pretrained(
            model_source, trust_remote_code=True,
        )

        if "quantization_config" not in load_kwargs and device != "cpu":
            load_kwargs.setdefault("device_map", device)

        self._model = model_cls.from_pretrained(
            model_source, **load_kwargs,
        )
        if "quantization_config" not in load_kwargs and "device_map" not in load_kwargs:
            self._model = self._model.to(device)
        self._model.eval()

        self._last_use = time.monotonic()

    def ground(self, image: Any, query: str) -> GroundingResult | None:
        if self._model is None:
            raise RuntimeError("UGround not loaded. Call load() first.")

        import torch  # noqa: PLC0415

        self._touch()

        # Build Qwen2-VL chat messages with image + grounding query.
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image", "image": image},
                    {"type": "text", "text": (
                        f"In this UI screenshot, what are the bounding box "
                        f"coordinates of the element described as: '{query}'?"
                    )},
                ],
            },
        ]

        # Try qwen-vl-utils for proper image preprocessing (recommended).
        try:
            from qwen_vl_utils import process_vision_info  # noqa: PLC0415
            text = self._processor.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True,
            )
            image_inputs, video_inputs = process_vision_info(messages)
            inputs = self._processor(
                text=[text],
                images=image_inputs,
                videos=video_inputs,
                padding=True,
                return_tensors="pt",
            )
        except ImportError:
            # Without qwen-vl-utils, fall back to direct processor call.
            text = (
                f"In this UI screenshot, what are the bounding box "
                f"coordinates of the element described as: '{query}'?"
            )
            inputs = self._processor(
                text=text, images=image, return_tensors="pt",
            )

        inputs = {
            k: v.to(self._device) if hasattr(v, "to") else v
            for k, v in inputs.items()
        }

        with torch.inference_mode():
            generated_ids = self._model.generate(
                **inputs,
                max_new_tokens=128,
                do_sample=False,
            )

        # Decode only the generated tokens (skip the prompt).
        generated_ids_trimmed = [
            out_ids[len(in_ids):]
            for in_ids, out_ids in zip(inputs["input_ids"], generated_ids)
        ]
        response_text = self._processor.batch_decode(
            generated_ids_trimmed,
            skip_special_tokens=True,
            clean_up_tokenization_spaces=False,
        )[0]

        bbox = _parse_bbox_from_text(response_text)
        if bbox is None:
            return None

        return GroundingResult(
            bbox=bbox,
            confidence=0.8,  # UGround doesn't provide per-query confidence.
            query=query,
        )

    def _touch(self) -> None:
        """Update last-use timestamp and reset auto-unload timer."""
        self._last_use = time.monotonic()
        with self._lock:
            if self._unload_timer is not None:
                self._unload_timer.cancel()
            self._unload_timer = threading.Timer(
                self._idle_timeout, self._auto_unload,
            )
            self._unload_timer.daemon = True
            self._unload_timer.start()

    def _auto_unload(self) -> None:
        """Auto-unload if idle for longer than timeout."""
        if time.monotonic() - self._last_use >= self._idle_timeout:
            self.unload()

    def unload(self) -> None:
        with self._lock:
            if self._unload_timer is not None:
                self._unload_timer.cancel()
                self._unload_timer = None

        if self._model is not None:
            del self._model
            del self._processor
            self._model = None
            self._processor = None
            try:
                import torch  # noqa: PLC0415
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except ImportError:
                pass

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    @property
    def vram_estimate_mb(self) -> int:
        return self.VRAM_ESTIMATE_MB
