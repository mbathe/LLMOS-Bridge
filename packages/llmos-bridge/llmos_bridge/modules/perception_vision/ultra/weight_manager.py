"""Model weight downloading and VRAM budget tracking.

Downloads model weights from HuggingFace using the same pattern as
OmniParser's ``_ensure_weights()`` method.  Tracks GPU VRAM allocation
to prevent out-of-memory errors when multiple models coexist.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class ModelSpec:
    """Describes a model that needs to be downloaded and managed."""

    name: str
    """Human-readable name."""

    repo_id: str
    """HuggingFace repository ID (e.g. 'racineai/UI-DETR-1')."""

    local_dir_name: str
    """Subdirectory name under the base model directory."""

    required_files: list[str]
    """Files to check for existence to confirm weights are present."""

    vram_mb: int
    """Estimated VRAM usage when loaded (0 for CPU-only models)."""

    allow_patterns: list[str] | None = None
    """HuggingFace snapshot_download allow_patterns (None = all files)."""


# Pre-defined model specifications.

UI_DETR_SPEC = ModelSpec(
    name="UI-DETR-1",
    repo_id="racineai/UI-DETR-1",
    local_dir_name="ui_detr",
    required_files=["config.json", "model.pth"],
    vram_mb=500,
)

UGROUND_SPEC = ModelSpec(
    name="UGround-V1-2B",
    repo_id="osunlp/UGround-V1-2B",
    local_dir_name="uground_v1",
    required_files=["config.json", "model.safetensors"],
    vram_mb=1500,
)


class WeightManager:
    """Download and verify model weights from HuggingFace.

    Follows the same pattern as OmniParserModule._ensure_weights():
    uses ``huggingface_hub.snapshot_download`` to fetch model files.
    """

    def __init__(
        self,
        base_dir: str = "~/.llmos/models/ultra_vision",
        auto_download: bool = True,
    ) -> None:
        self._base_dir = os.path.expanduser(base_dir)
        self._auto_download = auto_download

    @property
    def base_dir(self) -> str:
        return self._base_dir

    def model_dir(self, spec: ModelSpec) -> str:
        """Return the local directory path for a model."""
        return os.path.join(self._base_dir, spec.local_dir_name)

    def is_available(self, spec: ModelSpec) -> bool:
        """Check if model weights are already downloaded."""
        local_dir = self.model_dir(spec)
        return self._weights_exist(local_dir, spec.required_files)

    def ensure_model(self, spec: ModelSpec) -> str:
        """Ensure model weights are present. Returns the local directory path.

        Downloads from HuggingFace if not present and auto_download is enabled.

        Raises:
            RuntimeError: If weights are missing and auto_download is disabled.
            ImportError: If huggingface_hub is not installed.
        """
        local_dir = self.model_dir(spec)

        if self._weights_exist(local_dir, spec.required_files):
            return local_dir

        if not self._auto_download:
            raise RuntimeError(
                f"Model weights for {spec.name} not found at '{local_dir}'. "
                f"Download from: https://huggingface.co/{spec.repo_id} "
                f"or enable auto_download in config."
            )

        try:
            from huggingface_hub import snapshot_download  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                f"huggingface_hub is required to download {spec.name} weights. "
                f"Install with: pip install huggingface-hub"
            ) from exc

        os.makedirs(local_dir, exist_ok=True)
        snapshot_download(
            spec.repo_id,
            local_dir=local_dir,
            allow_patterns=spec.allow_patterns,
        )

        return local_dir

    @staticmethod
    def _weights_exist(local_dir: str, required_files: list[str]) -> bool:
        """Check if all required files exist in the directory."""
        if not os.path.isdir(local_dir):
            return False
        return all(
            os.path.exists(os.path.join(local_dir, f))
            for f in required_files
        )


class VRAMBudget:
    """Track GPU VRAM allocation to prevent out-of-memory errors.

    Maintains a budget of available VRAM and tracks which models
    have allocated memory.
    """

    def __init__(self, max_mb: int = 3000) -> None:
        self._max_mb = max_mb
        self._allocated: dict[str, int] = {}

    @property
    def max_mb(self) -> int:
        return self._max_mb

    @property
    def used_mb(self) -> int:
        return sum(self._allocated.values())

    @property
    def available_mb(self) -> int:
        return self._max_mb - self.used_mb

    def can_allocate(self, name: str, vram_mb: int) -> bool:
        """Check if the given model can fit in the remaining VRAM budget."""
        if name in self._allocated:
            return True  # Already allocated.
        return (self.used_mb + vram_mb) <= self._max_mb

    def allocate(self, name: str, vram_mb: int) -> None:
        """Register a VRAM allocation.

        Raises:
            RuntimeError: If allocation exceeds the budget.
        """
        if name in self._allocated:
            return  # Already allocated.
        if (self.used_mb + vram_mb) > self._max_mb:
            raise RuntimeError(
                f"Cannot allocate {vram_mb}MB for {name}: "
                f"budget {self._max_mb}MB, used {self.used_mb}MB, "
                f"available {self.available_mb}MB."
            )
        self._allocated[name] = vram_mb

    def release(self, name: str) -> None:
        """Release a VRAM allocation."""
        self._allocated.pop(name, None)

    def is_allocated(self, name: str) -> bool:
        """Check if a model has an active allocation."""
        return name in self._allocated

    @staticmethod
    def query_available_vram() -> int:
        """Query actual available GPU VRAM in MB via torch.cuda.

        Returns 0 if CUDA is not available.
        """
        try:
            import torch  # noqa: PLC0415
            if torch.cuda.is_available():
                free, _ = torch.cuda.mem_get_info()
                return free // (1024 * 1024)
        except (ImportError, RuntimeError):
            pass
        return 0
