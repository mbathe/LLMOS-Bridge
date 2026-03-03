"""Unit tests — WeightManager + VRAMBudget."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from llmos_bridge.modules.perception_vision.ultra.weight_manager import (
    ModelSpec,
    UI_DETR_SPEC,
    UGROUND_SPEC,
    VRAMBudget,
    WeightManager,
)


# ---------------------------------------------------------------------------
# ModelSpec tests
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestModelSpec:
    def test_ui_detr_spec_fields(self) -> None:
        assert UI_DETR_SPEC.name == "UI-DETR-1"
        assert UI_DETR_SPEC.repo_id == "racineai/UI-DETR-1"
        assert UI_DETR_SPEC.vram_mb == 500

    def test_uground_spec_fields(self) -> None:
        assert UGROUND_SPEC.name == "UGround-V1-2B"
        assert UGROUND_SPEC.repo_id == "osunlp/UGround-V1-2B"
        assert UGROUND_SPEC.vram_mb == 1500

    def test_custom_spec(self) -> None:
        spec = ModelSpec(
            name="Test", repo_id="org/model", local_dir_name="test_dir",
            required_files=["a.bin", "b.json"], vram_mb=200,
        )
        assert spec.local_dir_name == "test_dir"
        assert spec.required_files == ["a.bin", "b.json"]
        assert spec.allow_patterns is None


# ---------------------------------------------------------------------------
# WeightManager tests
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestWeightManager:
    def test_base_dir_expansion(self) -> None:
        wm = WeightManager(base_dir="~/my_models")
        assert wm.base_dir == os.path.expanduser("~/my_models")

    def test_model_dir(self) -> None:
        wm = WeightManager(base_dir="/tmp/models")
        path = wm.model_dir(UI_DETR_SPEC)
        assert path == "/tmp/models/ui_detr"

    def test_is_available_false_when_no_dir(self) -> None:
        wm = WeightManager(base_dir="/nonexistent/path")
        assert wm.is_available(UI_DETR_SPEC) is False

    def test_is_available_true_when_files_exist(self, tmp_path: object) -> None:
        base = str(tmp_path)
        model_dir = os.path.join(base, "ui_detr")
        os.makedirs(model_dir)
        (open(os.path.join(model_dir, "config.json"), "w")).close()
        (open(os.path.join(model_dir, "model.pth"), "w")).close()

        wm = WeightManager(base_dir=base)
        assert wm.is_available(UI_DETR_SPEC) is True

    def test_is_available_false_when_partial(self, tmp_path: object) -> None:
        base = str(tmp_path)
        model_dir = os.path.join(base, "ui_detr")
        os.makedirs(model_dir)
        # config.json missing

        wm = WeightManager(base_dir=base)
        assert wm.is_available(UI_DETR_SPEC) is False

    def test_ensure_model_returns_path_when_present(self, tmp_path: object) -> None:
        base = str(tmp_path)
        model_dir = os.path.join(base, "ui_detr")
        os.makedirs(model_dir)
        (open(os.path.join(model_dir, "config.json"), "w")).close()
        (open(os.path.join(model_dir, "model.pth"), "w")).close()

        wm = WeightManager(base_dir=base)
        result = wm.ensure_model(UI_DETR_SPEC)
        assert result == model_dir

    def test_ensure_model_raises_when_no_auto_download(self) -> None:
        wm = WeightManager(base_dir="/nonexistent", auto_download=False)
        with pytest.raises(RuntimeError, match="not found"):
            wm.ensure_model(UI_DETR_SPEC)

    def test_ensure_model_raises_when_no_huggingface_hub(self) -> None:
        wm = WeightManager(base_dir="/nonexistent", auto_download=True)
        with patch.dict("sys.modules", {"huggingface_hub": None}):
            with pytest.raises((ImportError, RuntimeError)):
                wm.ensure_model(UI_DETR_SPEC)

    @patch("llmos_bridge.modules.perception_vision.ultra.weight_manager.snapshot_download", create=True)
    def test_ensure_model_downloads(self, mock_download: MagicMock, tmp_path: object) -> None:
        base = str(tmp_path)
        wm = WeightManager(base_dir=base, auto_download=True)

        # Simulate huggingface_hub being importable.
        mock_hub = MagicMock()
        mock_hub.snapshot_download = mock_download
        with patch.dict("sys.modules", {"huggingface_hub": mock_hub}):
            # After download, simulate files being present.
            model_dir = os.path.join(base, "ui_detr")
            os.makedirs(model_dir, exist_ok=True)
            (open(os.path.join(model_dir, "config.json"), "w")).close()
            (open(os.path.join(model_dir, "model.pth"), "w")).close()

            result = wm.ensure_model(UI_DETR_SPEC)
            assert result == model_dir


# ---------------------------------------------------------------------------
# VRAMBudget tests
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestVRAMBudget:
    def test_initial_state(self) -> None:
        budget = VRAMBudget(max_mb=3000)
        assert budget.max_mb == 3000
        assert budget.used_mb == 0
        assert budget.available_mb == 3000

    def test_can_allocate_within_budget(self) -> None:
        budget = VRAMBudget(max_mb=3000)
        assert budget.can_allocate("model_a", 500) is True

    def test_can_allocate_over_budget(self) -> None:
        budget = VRAMBudget(max_mb=3000)
        assert budget.can_allocate("model_a", 3500) is False

    def test_allocate_updates_used(self) -> None:
        budget = VRAMBudget(max_mb=3000)
        budget.allocate("model_a", 500)
        assert budget.used_mb == 500
        assert budget.available_mb == 2500

    def test_allocate_idempotent(self) -> None:
        budget = VRAMBudget(max_mb=3000)
        budget.allocate("model_a", 500)
        budget.allocate("model_a", 500)  # No-op.
        assert budget.used_mb == 500

    def test_allocate_raises_over_budget(self) -> None:
        budget = VRAMBudget(max_mb=1000)
        budget.allocate("model_a", 600)
        with pytest.raises(RuntimeError, match="Cannot allocate"):
            budget.allocate("model_b", 600)

    def test_release(self) -> None:
        budget = VRAMBudget(max_mb=3000)
        budget.allocate("model_a", 500)
        budget.release("model_a")
        assert budget.used_mb == 0
        assert budget.available_mb == 3000

    def test_release_nonexistent_is_noop(self) -> None:
        budget = VRAMBudget(max_mb=3000)
        budget.release("nonexistent")  # Should not raise.
        assert budget.used_mb == 0

    def test_is_allocated(self) -> None:
        budget = VRAMBudget(max_mb=3000)
        assert budget.is_allocated("model_a") is False
        budget.allocate("model_a", 500)
        assert budget.is_allocated("model_a") is True

    def test_multiple_allocations(self) -> None:
        budget = VRAMBudget(max_mb=3000)
        budget.allocate("detector", 500)
        budget.allocate("grounder", 1500)
        assert budget.used_mb == 2000
        assert budget.available_mb == 1000

    def test_can_allocate_already_allocated(self) -> None:
        budget = VRAMBudget(max_mb=3000)
        budget.allocate("model_a", 500)
        # Should return True because it's already allocated.
        assert budget.can_allocate("model_a", 500) is True

    @patch("llmos_bridge.modules.perception_vision.ultra.weight_manager.torch", create=True)
    def test_query_available_vram_with_cuda(self, mock_torch: MagicMock) -> None:
        mock_torch.cuda.is_available.return_value = True
        mock_torch.cuda.mem_get_info.return_value = (2048 * 1024 * 1024, 8192 * 1024 * 1024)
        with patch.dict("sys.modules", {"torch": mock_torch}):
            result = VRAMBudget.query_available_vram()
            assert result == 2048

    def test_query_available_vram_no_torch(self) -> None:
        with patch.dict("sys.modules", {"torch": None}):
            result = VRAMBudget.query_available_vram()
            assert result == 0
