"""Integration tests — FilesystemModule against a real temp directory."""

from __future__ import annotations

from pathlib import Path

import pytest

from llmos_bridge.modules.filesystem import FilesystemModule


@pytest.fixture
def module() -> FilesystemModule:
    return FilesystemModule()


@pytest.mark.integration
class TestReadFile:
    async def test_read_existing_file(self, module: FilesystemModule, tmp_path: Path) -> None:
        f = tmp_path / "hello.txt"
        f.write_text("Hello World\nLine 2\n", encoding="utf-8")

        result = await module._action_read_file({"path": str(f)})
        assert result["content"] == "Hello World\nLine 2\n"
        assert result["size_bytes"] > 0

    async def test_read_with_line_range(self, module: FilesystemModule, tmp_path: Path) -> None:
        f = tmp_path / "multi.txt"
        f.write_text("line1\nline2\nline3\nline4\n")
        result = await module._action_read_file({"path": str(f), "start_line": 2, "end_line": 3})
        assert "line2" in result["content"]
        assert "line4" not in result["content"]

    async def test_read_nonexistent_raises(self, module: FilesystemModule, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            await module._action_read_file({"path": str(tmp_path / "ghost.txt")})

    async def test_read_directory_raises(self, module: FilesystemModule, tmp_path: Path) -> None:
        with pytest.raises(IsADirectoryError):
            await module._action_read_file({"path": str(tmp_path)})


@pytest.mark.integration
class TestWriteFile:
    async def test_write_creates_file(self, module: FilesystemModule, tmp_path: Path) -> None:
        path = tmp_path / "output.txt"
        result = await module._action_write_file(
            {"path": str(path), "content": "written content"}
        )
        assert path.read_text() == "written content"
        assert result["bytes_written"] > 0

    async def test_write_with_create_dirs(self, module: FilesystemModule, tmp_path: Path) -> None:
        path = tmp_path / "deep" / "nested" / "file.txt"
        await module._action_write_file(
            {"path": str(path), "content": "deep", "create_dirs": True}
        )
        assert path.read_text() == "deep"

    async def test_write_no_overwrite_raises(self, module: FilesystemModule, tmp_path: Path) -> None:
        path = tmp_path / "existing.txt"
        path.write_text("original")
        with pytest.raises(FileExistsError):
            await module._action_write_file(
                {"path": str(path), "content": "new", "overwrite": False}
            )


@pytest.mark.integration
class TestCopyMove:
    async def test_copy_file(self, module: FilesystemModule, tmp_path: Path) -> None:
        src = tmp_path / "src.txt"
        dst = tmp_path / "dst.txt"
        src.write_text("data")
        await module._action_copy_file({"source": str(src), "destination": str(dst)})
        assert dst.read_text() == "data"
        assert src.exists()  # Source still exists

    async def test_move_file(self, module: FilesystemModule, tmp_path: Path) -> None:
        src = tmp_path / "move_src.txt"
        dst = tmp_path / "move_dst.txt"
        src.write_text("movable")
        await module._action_move_file({"source": str(src), "destination": str(dst)})
        assert dst.read_text() == "movable"
        assert not src.exists()  # Source removed


@pytest.mark.integration
class TestDeleteFile:
    async def test_delete_file(self, module: FilesystemModule, tmp_path: Path) -> None:
        f = tmp_path / "to_delete.txt"
        f.write_text("bye")
        await module._action_delete_file({"path": str(f)})
        assert not f.exists()

    async def test_delete_directory_recursive(self, module: FilesystemModule, tmp_path: Path) -> None:
        d = tmp_path / "subdir"
        d.mkdir()
        (d / "file.txt").write_text("content")
        await module._action_delete_file({"path": str(d), "recursive": True})
        assert not d.exists()

    async def test_delete_directory_non_recursive_raises(
        self, module: FilesystemModule, tmp_path: Path
    ) -> None:
        d = tmp_path / "non_empty"
        d.mkdir()
        (d / "x.txt").write_text("x")
        with pytest.raises(IsADirectoryError):
            await module._action_delete_file({"path": str(d), "recursive": False})


@pytest.mark.integration
class TestListDirectory:
    async def test_list_directory(self, module: FilesystemModule, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b.py").write_text("b")
        result = await module._action_list_directory({"path": str(tmp_path)})
        names = [e["name"] for e in result["entries"]]
        assert "a.txt" in names
        assert "b.py" in names

    async def test_list_with_pattern(self, module: FilesystemModule, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b.py").write_text("b")
        result = await module._action_list_directory({"path": str(tmp_path), "pattern": "*.txt"})
        names = [e["name"] for e in result["entries"]]
        assert "a.txt" in names
        assert "b.py" not in names


@pytest.mark.integration
class TestChecksum:
    async def test_sha256_checksum(self, module: FilesystemModule, tmp_path: Path) -> None:
        f = tmp_path / "hash_me.txt"
        f.write_text("deterministic content")
        result = await module._action_compute_checksum({"path": str(f), "algorithm": "sha256"})
        assert result["algorithm"] == "sha256"
        assert len(result["checksum"]) == 64

    async def test_same_content_same_checksum(self, module: FilesystemModule, tmp_path: Path) -> None:
        f1 = tmp_path / "f1.txt"
        f2 = tmp_path / "f2.txt"
        f1.write_text("identical")
        f2.write_text("identical")
        r1 = await module._action_compute_checksum({"path": str(f1)})
        r2 = await module._action_compute_checksum({"path": str(f2)})
        assert r1["checksum"] == r2["checksum"]


# ---------------------------------------------------------------------------
# Append file
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestAppendFile:
    async def test_append_to_existing(self, module: FilesystemModule, tmp_path: Path) -> None:
        f = tmp_path / "append.txt"
        f.write_text("line1")
        result = await module._action_append_file(
            {"path": str(f), "content": "line2", "newline": True}
        )
        assert result["bytes_appended"] > 0
        assert "line1" in f.read_text()
        assert "line2" in f.read_text()

    async def test_append_no_newline(self, module: FilesystemModule, tmp_path: Path) -> None:
        f = tmp_path / "append2.txt"
        f.write_text("hello")
        await module._action_append_file(
            {"path": str(f), "content": " world", "newline": False}
        )
        assert f.read_text() == "hello world"

    async def test_append_creates_new_file(self, module: FilesystemModule, tmp_path: Path) -> None:
        f = tmp_path / "new_append.txt"
        result = await module._action_append_file({"path": str(f), "content": "fresh"})
        assert f.read_text() == "fresh"
        assert result["bytes_appended"] > 0


# ---------------------------------------------------------------------------
# Create directory
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestCreateDirectory:
    async def test_create_directory(self, module: FilesystemModule, tmp_path: Path) -> None:
        d = tmp_path / "newdir"
        result = await module._action_create_directory({"path": str(d)})
        assert d.is_dir()
        assert result["created"] is True

    async def test_create_directory_with_parents(
        self, module: FilesystemModule, tmp_path: Path
    ) -> None:
        d = tmp_path / "deep" / "nested" / "dir"
        await module._action_create_directory({"path": str(d), "parents": True})
        assert d.is_dir()

    async def test_create_directory_exist_ok(
        self, module: FilesystemModule, tmp_path: Path
    ) -> None:
        d = tmp_path / "exists"
        d.mkdir()
        # Should not raise with exist_ok=True
        result = await module._action_create_directory(
            {"path": str(d), "exist_ok": True}
        )
        assert result["created"] is True


# ---------------------------------------------------------------------------
# Get file info
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestGetFileInfo:
    async def test_get_file_info(self, module: FilesystemModule, tmp_path: Path) -> None:
        f = tmp_path / "info.txt"
        f.write_text("some content")
        result = await module._action_get_file_info({"path": str(f)})
        assert result["name"] == "info.txt"
        assert result["type"] == "file"
        assert result["size_bytes"] > 0
        assert result["suffix"] == ".txt"
        assert "permissions" in result
        assert "modified" in result

    async def test_get_directory_info(self, module: FilesystemModule, tmp_path: Path) -> None:
        result = await module._action_get_file_info({"path": str(tmp_path)})
        assert result["type"] == "directory"

    async def test_get_file_info_nonexistent_raises(
        self, module: FilesystemModule, tmp_path: Path
    ) -> None:
        with pytest.raises(FileNotFoundError):
            await module._action_get_file_info({"path": str(tmp_path / "ghost.txt")})


# ---------------------------------------------------------------------------
# Search files
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestSearchFiles:
    async def test_search_by_pattern(self, module: FilesystemModule, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("import os")
        (tmp_path / "b.txt").write_text("plain text")
        (tmp_path / "c.py").write_text("import sys")
        result = await module._action_search_files(
            {"directory": str(tmp_path), "pattern": "*.py"}
        )
        names = [m["name"] for m in result["matches"]]
        assert "a.py" in names
        assert "c.py" in names
        assert "b.txt" not in names

    async def test_search_by_content(self, module: FilesystemModule, tmp_path: Path) -> None:
        (tmp_path / "match.txt").write_text("secret content here")
        (tmp_path / "no_match.txt").write_text("nothing relevant")
        result = await module._action_search_files(
            {
                "directory": str(tmp_path),
                "pattern": "*.txt",
                "content_pattern": "secret",
            }
        )
        names = [m["name"] for m in result["matches"]]
        assert "match.txt" in names
        assert "no_match.txt" not in names

    async def test_search_respects_max_results(
        self, module: FilesystemModule, tmp_path: Path
    ) -> None:
        for i in range(10):
            (tmp_path / f"file{i}.txt").write_text("content")
        result = await module._action_search_files(
            {"directory": str(tmp_path), "pattern": "*.txt", "max_results": 3}
        )
        assert result["count"] <= 3


# ---------------------------------------------------------------------------
# Archive operations
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestArchive:
    async def test_create_zip_archive(self, module: FilesystemModule, tmp_path: Path) -> None:
        src = tmp_path / "to_archive"
        src.mkdir()
        (src / "file1.txt").write_text("content1")
        (src / "file2.txt").write_text("content2")
        dest = tmp_path / "archive.zip"
        result = await module._action_create_archive(
            {"source": str(src), "destination": str(dest), "format": "zip"}
        )
        assert result["source"] == str(src)
        # shutil.make_archive appends the format extension
        from pathlib import Path as P
        archive_files = list(tmp_path.glob("*.zip"))
        assert len(archive_files) >= 1

    async def test_extract_archive(self, module: FilesystemModule, tmp_path: Path) -> None:
        import zipfile

        # Create a zip file to extract
        archive = tmp_path / "test.zip"
        with zipfile.ZipFile(str(archive), "w") as zf:
            zf.writestr("hello.txt", "hello content")
            zf.writestr("world.txt", "world content")

        extract_dir = tmp_path / "extracted"
        result = await module._action_extract_archive(
            {"source": str(archive), "destination": str(extract_dir)}
        )
        assert (extract_dir / "hello.txt").exists()
        assert (extract_dir / "world.txt").exists()
        assert result["destination"] == str(extract_dir)

    async def test_list_directory_recursive(
        self, module: FilesystemModule, tmp_path: Path
    ) -> None:
        sub = tmp_path / "sub"
        sub.mkdir()
        (tmp_path / "top.txt").write_text("top")
        (sub / "deep.txt").write_text("deep")
        result = await module._action_list_directory(
            {"path": str(tmp_path), "recursive": True}
        )
        names = [e["name"] for e in result["entries"]]
        assert "top.txt" in names
        assert "deep.txt" in names
