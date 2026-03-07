"""Tests for PackageStorage — file-based .tar.gz storage."""

from __future__ import annotations

from pathlib import Path

import pytest

from llmos_hub.storage import PackageStorage


@pytest.fixture()
def storage(tmp_path):
    return PackageStorage(tmp_path / "packages")


class TestPackageStorage:
    async def test_save_and_load_roundtrip(self, storage):
        data = b"fake tarball content"
        rel_path, checksum = await storage.save("my_mod", "1.0.0", data)
        assert "my_mod" in rel_path
        assert "1.0.0" in rel_path
        assert len(checksum) == 64  # SHA-256 hex

        loaded = await storage.load(rel_path)
        assert loaded == data

    async def test_load_missing_file(self, storage):
        with pytest.raises(FileNotFoundError):
            await storage.load("nonexistent/path.tar.gz")

    async def test_delete(self, storage):
        await storage.save("del_mod", "1.0.0", b"data")
        await storage.delete("del_mod")
        # Should not exist anymore
        with pytest.raises(FileNotFoundError):
            await storage.load("del_mod/1.0.0/del_mod-1.0.0.tar.gz")

    async def test_save_creates_directory(self, storage):
        rel_path, _ = await storage.save("new_mod", "0.1.0", b"content")
        full_path = storage._root / rel_path
        assert full_path.exists()
