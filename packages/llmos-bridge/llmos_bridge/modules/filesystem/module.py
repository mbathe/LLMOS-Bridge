"""FileSystem module — Implementation.

All path operations are performed using pathlib.  Symlinks are followed by
default.  The PermissionGuard enforces sandbox restrictions upstream;
this module does not re-check them.
"""

from __future__ import annotations

import asyncio
import hashlib
import shutil
import stat
import time
from pathlib import Path
from typing import Any

from llmos_bridge.modules.base import BaseModule, Platform
from llmos_bridge.modules.manifest import ActionSpec, ModuleManifest, ParamSpec
from llmos_bridge.security.decorators import (
    audit_trail,
    rate_limited,
    requires_permission,
    sensitive_action,
)
from llmos_bridge.security.models import Permission, RiskLevel
from llmos_bridge.protocol.params.filesystem import (
    AppendFileParams,
    ComputeChecksumParams,
    CopyFileParams,
    CreateArchiveParams,
    CreateDirectoryParams,
    DeleteFileParams,
    ExtractArchiveParams,
    GetFileInfoParams,
    ListDirectoryParams,
    MoveFileParams,
    ReadFileParams,
    SearchFilesParams,
    WriteFileParams,
)


class FilesystemModule(BaseModule):
    MODULE_ID = "filesystem"
    VERSION = "1.0.0"
    SUPPORTED_PLATFORMS = [Platform.ALL]

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    @requires_permission(Permission.FILESYSTEM_READ, reason="Read file contents")
    async def _action_read_file(self, params: dict[str, Any]) -> dict[str, Any]:
        p = ReadFileParams.model_validate(params)
        path = Path(p.path)

        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        if not path.is_file():
            raise IsADirectoryError(f"Path is a directory: {path}")

        raw = await asyncio.to_thread(self._read_file_sync, path, p)
        return {"path": str(path), "content": raw, "size_bytes": len(raw.encode(p.encoding))}

    def _read_file_sync(self, path: Path, p: ReadFileParams) -> str:
        with path.open(encoding=p.encoding, errors="replace") as f:
            lines = f.readlines()

        start = (p.start_line - 1) if p.start_line else 0
        end = p.end_line if p.end_line else len(lines)
        selected = lines[start:end]
        content = "".join(selected)

        if p.max_bytes and len(content.encode(p.encoding)) > p.max_bytes:
            content = content.encode(p.encoding)[: p.max_bytes].decode(p.encoding, errors="replace")
        return content

    @requires_permission(Permission.FILESYSTEM_WRITE, reason="Write file to disk")
    @rate_limited(calls_per_minute=60)
    @audit_trail("standard")
    async def _action_write_file(self, params: dict[str, Any]) -> dict[str, Any]:
        p = WriteFileParams.model_validate(params)
        path = Path(p.path)

        if path.exists() and not p.overwrite:
            raise FileExistsError(f"File already exists and overwrite=False: {path}")
        if p.create_dirs:
            path.parent.mkdir(parents=True, exist_ok=True)

        await asyncio.to_thread(
            path.write_text, p.content, encoding=p.encoding
        )
        return {"path": str(path), "bytes_written": len(p.content.encode(p.encoding))}

    @requires_permission(Permission.FILESYSTEM_WRITE, reason="Append to file")
    @rate_limited(calls_per_minute=60)
    async def _action_append_file(self, params: dict[str, Any]) -> dict[str, Any]:
        p = AppendFileParams.model_validate(params)
        path = Path(p.path)
        text = ("\n" + p.content) if p.newline and path.exists() else p.content

        def _append() -> None:
            with path.open("a", encoding=p.encoding) as f:
                f.write(text)

        await asyncio.to_thread(_append)
        return {"path": str(path), "bytes_appended": len(text.encode(p.encoding))}

    @requires_permission(Permission.FILESYSTEM_READ, Permission.FILESYSTEM_WRITE, reason="Copy file")
    @rate_limited(calls_per_minute=60)
    async def _action_copy_file(self, params: dict[str, Any]) -> dict[str, Any]:
        p = CopyFileParams.model_validate(params)
        src, dst = Path(p.source), Path(p.destination)

        if dst.exists() and not p.overwrite:
            raise FileExistsError(f"Destination exists and overwrite=False: {dst}")

        await asyncio.to_thread(shutil.copy2, src, dst)
        return {"source": str(src), "destination": str(dst)}

    @requires_permission(Permission.FILESYSTEM_READ, Permission.FILESYSTEM_WRITE, reason="Move file")
    @rate_limited(calls_per_minute=60)
    async def _action_move_file(self, params: dict[str, Any]) -> dict[str, Any]:
        p = MoveFileParams.model_validate(params)
        src, dst = Path(p.source), Path(p.destination)

        if dst.exists() and not p.overwrite:
            raise FileExistsError(f"Destination exists and overwrite=False: {dst}")

        await asyncio.to_thread(shutil.move, str(src), dst)
        return {"source": str(src), "destination": str(dst)}

    @requires_permission(Permission.FILESYSTEM_DELETE, reason="Delete file or directory")
    @sensitive_action(RiskLevel.HIGH, irreversible=True)
    @rate_limited(calls_per_minute=60)
    @audit_trail("detailed")
    async def _action_delete_file(self, params: dict[str, Any]) -> dict[str, Any]:
        p = DeleteFileParams.model_validate(params)
        path = Path(p.path)

        if not path.exists():
            raise FileNotFoundError(f"Path not found: {path}")

        if path.is_dir():
            if not p.recursive:
                raise IsADirectoryError(
                    f"Path is a directory. Set recursive=true to delete: {path}"
                )
            await asyncio.to_thread(shutil.rmtree, path)
        else:
            await asyncio.to_thread(path.unlink)

        return {"deleted": str(path)}

    @requires_permission(Permission.FILESYSTEM_WRITE, reason="Create directory")
    async def _action_create_directory(self, params: dict[str, Any]) -> dict[str, Any]:
        p = CreateDirectoryParams.model_validate(params)
        path = Path(p.path)
        await asyncio.to_thread(path.mkdir, parents=p.parents, exist_ok=p.exist_ok)
        return {"path": str(path), "created": True}

    @requires_permission(Permission.FILESYSTEM_READ, reason="List directory contents")
    async def _action_list_directory(self, params: dict[str, Any]) -> dict[str, Any]:
        p = ListDirectoryParams.model_validate(params)
        base = Path(p.path)

        if not base.exists():
            raise FileNotFoundError(f"Directory not found: {base}")

        def _list() -> list[dict[str, Any]]:
            entries: list[dict[str, Any]] = []
            if p.recursive:
                pattern = f"**/{p.pattern}" if p.pattern else "**/*"
                paths = list(base.glob(pattern))
            else:
                pattern = p.pattern or "*"
                paths = list(base.glob(pattern))

            for path in paths[: p.max_results]:
                if not p.include_hidden and path.name.startswith("."):
                    continue
                try:
                    s = path.stat()
                    entries.append(
                        {
                            "name": path.name,
                            "path": str(path),
                            "type": "directory" if path.is_dir() else "file",
                            "size": s.st_size,
                            "modified": s.st_mtime,
                        }
                    )
                except OSError:
                    continue
            return entries

        entries = await asyncio.to_thread(_list)
        return {"path": str(base), "entries": entries, "count": len(entries)}

    @requires_permission(Permission.FILESYSTEM_READ, reason="Search files by pattern")
    async def _action_search_files(self, params: dict[str, Any]) -> dict[str, Any]:
        import re

        p = SearchFilesParams.model_validate(params)
        base = Path(p.directory)

        content_re = (
            re.compile(p.content_pattern, 0 if p.case_sensitive else re.IGNORECASE)
            if p.content_pattern
            else None
        )

        def _search() -> list[dict[str, Any]]:
            results = []
            for path in base.rglob(p.pattern):
                if not path.is_file():
                    continue
                if content_re:
                    try:
                        text = path.read_text(encoding="utf-8", errors="ignore")
                        if not content_re.search(text):
                            continue
                    except OSError:
                        continue
                results.append({"path": str(path), "name": path.name})
                if len(results) >= p.max_results:
                    break
            return results

        results = await asyncio.to_thread(_search)
        return {"matches": results, "count": len(results)}

    @requires_permission(Permission.FILESYSTEM_READ, reason="Reads file metadata")
    async def _action_get_file_info(self, params: dict[str, Any]) -> dict[str, Any]:
        p = GetFileInfoParams.model_validate(params)
        path = Path(p.path)

        if not path.exists():
            raise FileNotFoundError(f"Path not found: {path}")

        s = path.stat()
        return {
            "path": str(path),
            "name": path.name,
            "type": "directory" if path.is_dir() else "file",
            "size_bytes": s.st_size,
            "created": s.st_ctime,
            "modified": s.st_mtime,
            "permissions": oct(stat.S_IMODE(s.st_mode)),
            "is_symlink": path.is_symlink(),
            "suffix": path.suffix,
        }

    @requires_permission(Permission.FILESYSTEM_READ, Permission.FILESYSTEM_WRITE, reason="Create archive")
    async def _action_create_archive(self, params: dict[str, Any]) -> dict[str, Any]:
        p = CreateArchiveParams.model_validate(params)
        src = Path(p.source)

        format_map = {
            "zip": "zip",
            "tar": "tar",
            "tar.gz": "gztar",
            "tar.bz2": "bztar",
        }
        archive_format = format_map[p.format]
        dest = Path(p.destination)

        await asyncio.to_thread(
            shutil.make_archive, str(dest.with_suffix("")), archive_format, str(src.parent), src.name
        )
        return {"archive": str(dest), "source": str(src)}

    @requires_permission(Permission.FILESYSTEM_WRITE, reason="Extract archive to disk")
    async def _action_extract_archive(self, params: dict[str, Any]) -> dict[str, Any]:
        p = ExtractArchiveParams.model_validate(params)
        src, dst = Path(p.source), Path(p.destination)
        dst.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(shutil.unpack_archive, src, dst)
        return {"source": str(src), "destination": str(dst)}

    @requires_permission(Permission.FILESYSTEM_READ, reason="Compute file checksum")
    async def _action_compute_checksum(self, params: dict[str, Any]) -> dict[str, Any]:
        p = ComputeChecksumParams.model_validate(params)
        path = Path(p.path)

        def _hash() -> str:
            h = hashlib.new(p.algorithm)
            with path.open("rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
            return h.hexdigest()

        checksum = await asyncio.to_thread(_hash)
        return {"path": str(path), "algorithm": p.algorithm, "checksum": checksum}

    @requires_permission(Permission.FILESYSTEM_READ, reason="Watch path for changes")
    async def _action_watch_path(self, params: dict[str, Any]) -> dict[str, Any]:
        from llmos_bridge.protocol.params.filesystem import WatchPathParams

        p = WatchPathParams.model_validate(params)
        # Basic polling implementation — Phase 3 will integrate watchdog.
        path = Path(p.path)
        initial_mtime = path.stat().st_mtime if path.exists() else None
        deadline = time.time() + p.timeout

        while time.time() < deadline:
            await asyncio.sleep(0.5)
            current_mtime = path.stat().st_mtime if path.exists() else None
            if current_mtime != initial_mtime:
                return {
                    "path": str(path),
                    "event": "modified" if path.exists() else "deleted",
                    "detected_at": time.time(),
                }

        return {"path": str(path), "event": "timeout", "timeout": p.timeout}

    # ------------------------------------------------------------------
    # Manifest
    # ------------------------------------------------------------------

    def get_manifest(self) -> ModuleManifest:
        return ModuleManifest(
            module_id=self.MODULE_ID,
            version=self.VERSION,
            description="Read, write, move, copy, delete files and directories. Create and extract archives.",
            platforms=["all"],
            tags=["files", "io", "filesystem"],
            actions=[
                ActionSpec(
                    name="read_file",
                    description="Read the content of a text file.",
                    params=[
                        ParamSpec("path", "string", "Absolute or relative path to the file."),
                        ParamSpec("encoding", "string", "Text encoding.", required=False, default="utf-8"),
                        ParamSpec("start_line", "integer", "First line to read (1-indexed).", required=False),
                        ParamSpec("end_line", "integer", "Last line to read (1-indexed).", required=False),
                    ],
                    returns="object",
                    returns_description='{"path": str, "content": str, "size_bytes": int}',
                    examples=[
                        {
                            "description": "Read a Python file",
                            "params": {"path": "/home/user/script.py"},
                        }
                    ],
                ),
                ActionSpec(
                    name="write_file",
                    description="Write text content to a file, creating it if necessary.",
                    params=[
                        ParamSpec("path", "string", "Path to write to."),
                        ParamSpec("content", "string", "Content to write."),
                        ParamSpec("create_dirs", "boolean", "Create parent directories.", required=False, default=False),
                        ParamSpec("overwrite", "boolean", "Overwrite existing file.", required=False, default=True),
                    ],
                    permission_required="local_worker",
                ),
                ActionSpec(
                    name="delete_file",
                    description="Delete a file or directory.",
                    params=[
                        ParamSpec("path", "string", "Path to delete."),
                        ParamSpec("recursive", "boolean", "Delete directory recursively.", required=False, default=False),
                    ],
                    permission_required="power_user",
                ),
            ],
        )
