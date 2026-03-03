"""Tests for isolation.venv_manager — per-module virtual environment management."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llmos_bridge.exceptions import VenvCreationError
from llmos_bridge.isolation.venv_manager import VenvManager


@pytest.fixture
def tmp_venv_dir(tmp_path: Path) -> Path:
    return tmp_path / "venvs"


@pytest.fixture
def mgr(tmp_venv_dir: Path) -> VenvManager:
    return VenvManager(base_dir=tmp_venv_dir, prefer_uv=False)


@pytest.fixture
def mgr_uv(tmp_venv_dir: Path) -> VenvManager:
    m = VenvManager(base_dir=tmp_venv_dir, prefer_uv=True)
    m._uv_available = True  # Force uv detected
    return m


# ---------------------------------------------------------------------------
# has_uv
# ---------------------------------------------------------------------------


class TestHasUv:
    def test_detects_uv_on_path(self, mgr: VenvManager):
        with patch("shutil.which", return_value="/usr/bin/uv"):
            assert mgr.has_uv() is True

    def test_no_uv_on_path(self, mgr: VenvManager):
        with patch("shutil.which", return_value=None):
            assert mgr.has_uv() is False

    def test_caches_result(self, mgr: VenvManager):
        with patch("shutil.which", return_value="/usr/bin/uv") as mock:
            mgr.has_uv()
            mgr.has_uv()
            mock.assert_called_once()


# ---------------------------------------------------------------------------
# _requirements_hash
# ---------------------------------------------------------------------------


class TestRequirementsHash:
    def test_deterministic(self, mgr: VenvManager):
        h1 = mgr._requirements_hash(["torch>=2.2", "transformers>=5.0"])
        h2 = mgr._requirements_hash(["torch>=2.2", "transformers>=5.0"])
        assert h1 == h2

    def test_order_independent(self, mgr: VenvManager):
        h1 = mgr._requirements_hash(["torch>=2.2", "transformers>=5.0"])
        h2 = mgr._requirements_hash(["transformers>=5.0", "torch>=2.2"])
        assert h1 == h2

    def test_different_reqs_different_hash(self, mgr: VenvManager):
        h1 = mgr._requirements_hash(["torch>=2.2"])
        h2 = mgr._requirements_hash(["torch>=2.3"])
        assert h1 != h2

    def test_empty_requirements(self, mgr: VenvManager):
        h = mgr._requirements_hash([])
        assert len(h) == 64  # SHA-256 hex


# ---------------------------------------------------------------------------
# _venv_python
# ---------------------------------------------------------------------------


class TestVenvPython:
    def test_linux_path(self, mgr: VenvManager, tmp_path: Path):
        with patch("sys.platform", "linux"):
            p = mgr._venv_python(tmp_path / ".venv")
            assert str(p).endswith("bin/python")

    def test_windows_path(self, mgr: VenvManager, tmp_path: Path):
        with patch("sys.platform", "win32"):
            p = mgr._venv_python(tmp_path / ".venv")
            assert str(p).endswith("Scripts/python.exe") or str(p).endswith("Scripts\\python.exe")


# ---------------------------------------------------------------------------
# ensure_venv — cache behavior
# ---------------------------------------------------------------------------


class TestEnsureVenvCache:
    @pytest.mark.asyncio
    async def test_cache_hit_returns_existing(self, mgr: VenvManager, tmp_venv_dir: Path):
        """If venv exists and hash matches, return immediately."""
        module_dir = tmp_venv_dir / "test_mod"
        venv_dir = module_dir / ".venv"
        venv_dir.mkdir(parents=True)

        # Create a fake python executable.
        python = mgr._venv_python(venv_dir)
        python.parent.mkdir(parents=True, exist_ok=True)
        python.touch()

        # Write matching hash.
        reqs = ["torch>=2.2"]
        hash_file = module_dir / ".requirements.hash"
        hash_file.write_text(mgr._requirements_hash(reqs))

        result = await mgr.ensure_venv("test_mod", reqs)
        assert result == python

    @pytest.mark.asyncio
    async def test_cache_miss_triggers_creation(self, mgr: VenvManager, tmp_venv_dir: Path):
        """If no venv exists, create one."""
        with patch.object(mgr, "_create_with_stdlib", new_callable=AsyncMock) as mock_create:
            # Make the fake python appear after creation.
            async def side_effect(venv_dir, reqs, python_version=""):
                python = mgr._venv_python(venv_dir)
                python.parent.mkdir(parents=True, exist_ok=True)
                python.touch()

            mock_create.side_effect = side_effect

            result = await mgr.ensure_venv("new_mod", ["requests"])
            mock_create.assert_called_once()
            assert result.exists()

    @pytest.mark.asyncio
    async def test_hash_mismatch_recreates(self, mgr: VenvManager, tmp_venv_dir: Path):
        """If hash differs, delete and recreate."""
        module_dir = tmp_venv_dir / "stale_mod"
        venv_dir = module_dir / ".venv"
        venv_dir.mkdir(parents=True)

        # Write old hash.
        hash_file = module_dir / ".requirements.hash"
        hash_file.write_text("old_hash_value")

        with patch.object(mgr, "_create_with_stdlib", new_callable=AsyncMock) as mock_create:
            async def side_effect(vd, reqs, python_version=""):
                python = mgr._venv_python(vd)
                python.parent.mkdir(parents=True, exist_ok=True)
                python.touch()

            mock_create.side_effect = side_effect

            await mgr.ensure_venv("stale_mod", ["new-pkg"])
            mock_create.assert_called_once()


# ---------------------------------------------------------------------------
# ensure_venv — creation paths
# ---------------------------------------------------------------------------


class TestEnsureVenvCreation:
    @pytest.mark.asyncio
    async def test_uses_uv_when_preferred(self, mgr_uv: VenvManager, tmp_venv_dir: Path):
        with patch.object(mgr_uv, "_create_with_uv", new_callable=AsyncMock) as mock_uv:
            async def side_effect(vd, reqs, python_version=""):
                python = mgr_uv._venv_python(vd)
                python.parent.mkdir(parents=True, exist_ok=True)
                python.touch()

            mock_uv.side_effect = side_effect

            await mgr_uv.ensure_venv("uv_mod", ["torch"])
            mock_uv.assert_called_once()

    @pytest.mark.asyncio
    async def test_falls_back_to_stdlib(self, tmp_venv_dir: Path):
        mgr = VenvManager(base_dir=tmp_venv_dir, prefer_uv=True)
        mgr._uv_available = False  # Force no uv

        with patch.object(mgr, "_create_with_stdlib", new_callable=AsyncMock) as mock_stdlib:
            async def side_effect(vd, reqs, python_version=""):
                python = mgr._venv_python(vd)
                python.parent.mkdir(parents=True, exist_ok=True)
                python.touch()

            mock_stdlib.side_effect = side_effect

            await mgr.ensure_venv("stdlib_mod", ["requests"])
            mock_stdlib.assert_called_once()

    @pytest.mark.asyncio
    async def test_creation_failure_raises_venv_error(self, mgr: VenvManager):
        with patch.object(
            mgr, "_create_with_stdlib", new_callable=AsyncMock,
            side_effect=RuntimeError("pip failed"),
        ):
            with pytest.raises(VenvCreationError, match="pip failed"):
                await mgr.ensure_venv("fail_mod", ["bad-pkg"])

    @pytest.mark.asyncio
    async def test_creation_failure_cleans_up(self, mgr: VenvManager, tmp_venv_dir: Path):
        """Partial venv should be cleaned up on failure."""
        async def side_effect(vd, reqs, python_version=""):
            vd.mkdir(parents=True)  # Simulate partial creation
            raise RuntimeError("install failed")

        with patch.object(mgr, "_create_with_stdlib", new_callable=AsyncMock, side_effect=side_effect):
            with pytest.raises(VenvCreationError):
                await mgr.ensure_venv("cleanup_mod", ["bad"])

        # Partial venv should be gone.
        assert not (tmp_venv_dir / "cleanup_mod" / ".venv").exists()


# ---------------------------------------------------------------------------
# remove_venv
# ---------------------------------------------------------------------------


class TestRemoveVenv:
    @pytest.mark.asyncio
    async def test_removes_existing(self, mgr: VenvManager, tmp_venv_dir: Path):
        module_dir = tmp_venv_dir / "rm_mod"
        (module_dir / ".venv").mkdir(parents=True)
        (module_dir / ".requirements.hash").write_text("abc")

        await mgr.remove_venv("rm_mod")
        assert not module_dir.exists()

    @pytest.mark.asyncio
    async def test_noop_for_missing(self, mgr: VenvManager):
        await mgr.remove_venv("nonexistent")  # Should not raise


# ---------------------------------------------------------------------------
# list_venvs / venv_exists / get_python
# ---------------------------------------------------------------------------


class TestListAndQuery:
    def test_list_empty(self, mgr: VenvManager):
        assert mgr.list_venvs() == []

    def test_list_with_venvs(self, mgr: VenvManager, tmp_venv_dir: Path):
        (tmp_venv_dir / "mod_a" / ".venv").mkdir(parents=True)
        (tmp_venv_dir / "mod_b" / ".venv").mkdir(parents=True)
        (tmp_venv_dir / "not_a_venv").mkdir(parents=True)  # No .venv subdir
        assert mgr.list_venvs() == ["mod_a", "mod_b"]

    def test_venv_exists_true(self, mgr: VenvManager, tmp_venv_dir: Path):
        (tmp_venv_dir / "exists_mod" / ".venv").mkdir(parents=True)
        assert mgr.venv_exists("exists_mod") is True

    def test_venv_exists_false(self, mgr: VenvManager):
        assert mgr.venv_exists("nope") is False

    def test_get_python_exists(self, mgr: VenvManager, tmp_venv_dir: Path):
        venv_dir = tmp_venv_dir / "py_mod" / ".venv"
        python = mgr._venv_python(venv_dir)
        python.parent.mkdir(parents=True)
        python.touch()
        result = mgr.get_python("py_mod")
        assert result == python

    def test_get_python_no_venv(self, mgr: VenvManager):
        assert mgr.get_python("missing") is None

    def test_get_python_venv_but_no_binary(self, mgr: VenvManager, tmp_venv_dir: Path):
        (tmp_venv_dir / "broken_mod" / ".venv").mkdir(parents=True)
        assert mgr.get_python("broken_mod") is None
