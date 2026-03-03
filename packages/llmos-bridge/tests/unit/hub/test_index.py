"""Tests for hub.index — ModuleIndex (SQLite CRUD)."""

from __future__ import annotations

import time

import pytest

from llmos_bridge.hub.index import InstalledModule, ModuleIndex


@pytest.fixture()
async def index(tmp_path):
    idx = ModuleIndex(tmp_path / "test_modules.db")
    await idx.init()
    yield idx
    await idx.close()


def _make_module(module_id: str = "test_mod", version: str = "1.0.0") -> InstalledModule:
    return InstalledModule(
        module_id=module_id,
        version=version,
        install_path=f"/tmp/{module_id}",
        module_class_path=f"{module_id}.module:TestModule",
        requirements=["requests>=2.0"],
        installed_at=time.time(),
        updated_at=time.time(),
        enabled=True,
        sandbox_level="basic",
    )


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

class TestModuleIndexCRUD:
    @pytest.mark.asyncio
    async def test_add_and_get(self, index):
        mod = _make_module()
        await index.add(mod)
        result = await index.get("test_mod")
        assert result is not None
        assert result.module_id == "test_mod"
        assert result.version == "1.0.0"
        assert result.requirements == ["requests>=2.0"]

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, index):
        result = await index.get("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_remove(self, index):
        await index.add(_make_module())
        await index.remove("test_mod")
        assert await index.get("test_mod") is None

    @pytest.mark.asyncio
    async def test_remove_nonexistent(self, index):
        # Should not raise.
        await index.remove("nonexistent")

    @pytest.mark.asyncio
    async def test_list_all(self, index):
        await index.add(_make_module("mod_a"))
        await index.add(_make_module("mod_b"))
        all_mods = await index.list_all()
        ids = [m.module_id for m in all_mods]
        assert "mod_a" in ids
        assert "mod_b" in ids

    @pytest.mark.asyncio
    async def test_list_all_empty(self, index):
        result = await index.list_all()
        assert result == []

    @pytest.mark.asyncio
    async def test_list_enabled(self, index):
        mod_a = _make_module("mod_a")
        mod_a.enabled = True
        mod_b = _make_module("mod_b")
        mod_b.enabled = False
        await index.add(mod_a)
        await index.add(mod_b)
        # Disable mod_b.
        await index.set_enabled("mod_b", False)

        enabled = await index.list_enabled()
        ids = [m.module_id for m in enabled]
        assert "mod_a" in ids
        assert "mod_b" not in ids

    @pytest.mark.asyncio
    async def test_update_version(self, index):
        await index.add(_make_module("mod_a", "1.0.0"))
        await index.update_version("mod_a", "2.0.0", "/new/path")
        result = await index.get("mod_a")
        assert result is not None
        assert result.version == "2.0.0"
        assert result.install_path == "/new/path"

    @pytest.mark.asyncio
    async def test_set_enabled(self, index):
        await index.add(_make_module())
        await index.set_enabled("test_mod", False)
        result = await index.get("test_mod")
        assert result is not None
        assert result.enabled is False

        await index.set_enabled("test_mod", True)
        result = await index.get("test_mod")
        assert result.enabled is True

    @pytest.mark.asyncio
    async def test_add_replaces_existing(self, index):
        await index.add(_make_module("mod_a", "1.0.0"))
        await index.add(_make_module("mod_a", "2.0.0"))
        result = await index.get("mod_a")
        assert result.version == "2.0.0"

    @pytest.mark.asyncio
    async def test_sandbox_level_preserved(self, index):
        mod = _make_module()
        mod.sandbox_level = "strict"
        await index.add(mod)
        result = await index.get("test_mod")
        assert result.sandbox_level == "strict"

    @pytest.mark.asyncio
    async def test_signature_fingerprint_preserved(self, index):
        mod = _make_module()
        mod.signature_fingerprint = "abc123"
        await index.add(mod)
        result = await index.get("test_mod")
        assert result.signature_fingerprint == "abc123"
