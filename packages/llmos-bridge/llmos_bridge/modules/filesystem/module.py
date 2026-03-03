"""FileSystem module — Implementation.

All path operations are performed using pathlib.  Symlinks are followed by
default.  The PermissionGuard enforces sandbox restrictions upstream;
this module does not re-check them.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import stat
import time
from pathlib import Path
from typing import Any

from llmos_bridge.modules.base import BaseModule, Platform
from llmos_bridge.modules.manifest import ActionSpec, ModuleManifest, ParamSpec
from llmos_bridge.orchestration.streaming_decorators import streams_progress
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
    MODULE_TYPE = "system"
    SUPPORTED_PLATFORMS = [Platform.ALL]

    @staticmethod
    def _resolve_path(path: Path) -> Path:
        """Resolve symlinks to prevent write-through-symlink attacks.

        Returns the fully resolved (real) path.  This ensures that even
        if the PermissionGuard validated the unresolved path, the actual
        I/O target is known.
        """
        return path.resolve(strict=False)

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
        path = self._resolve_path(Path(p.path))

        if p.create_dirs:
            path.parent.mkdir(parents=True, exist_ok=True)

        def _write() -> int:
            data = p.content.encode(p.encoding)
            if not p.overwrite:
                # Atomic exclusive create — avoids TOCTOU race between
                # exists() check and open().
                fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
                try:
                    os.write(fd, data)
                finally:
                    os.close(fd)
            else:
                path.write_text(p.content, encoding=p.encoding)
            return len(data)

        bytes_written = await asyncio.to_thread(_write)
        return {"path": str(path), "bytes_written": bytes_written}

    @requires_permission(Permission.FILESYSTEM_WRITE, reason="Append to file")
    @rate_limited(calls_per_minute=60)
    async def _action_append_file(self, params: dict[str, Any]) -> dict[str, Any]:
        p = AppendFileParams.model_validate(params)
        path = self._resolve_path(Path(p.path))
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
        src, dst = self._resolve_path(Path(p.source)), self._resolve_path(Path(p.destination))

        if dst.exists() and not p.overwrite:
            raise FileExistsError(f"Destination exists and overwrite=False: {dst}")

        await asyncio.to_thread(shutil.copy2, src, dst)
        return {"source": str(src), "destination": str(dst)}

    @requires_permission(Permission.FILESYSTEM_READ, Permission.FILESYSTEM_WRITE, reason="Move file")
    @rate_limited(calls_per_minute=60)
    async def _action_move_file(self, params: dict[str, Any]) -> dict[str, Any]:
        p = MoveFileParams.model_validate(params)
        src, dst = self._resolve_path(Path(p.source)), self._resolve_path(Path(p.destination))

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
        path = self._resolve_path(Path(p.path))

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
        path = self._resolve_path(Path(p.path))
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

    @streams_progress
    @requires_permission(Permission.FILESYSTEM_READ, reason="Search files by pattern")
    async def _action_search_files(self, params: dict[str, Any]) -> dict[str, Any]:
        import re

        stream = params.pop("_stream", None)
        p = SearchFilesParams.model_validate(params)
        base = Path(p.directory)

        content_re = (
            re.compile(p.content_pattern, 0 if p.case_sensitive else re.IGNORECASE)
            if p.content_pattern
            else None
        )

        if stream:
            await stream.emit_status("searching")

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
        if stream:
            await stream.emit_progress(100, f"{len(results)} matches found")
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

    @streams_progress
    @requires_permission(Permission.FILESYSTEM_READ, Permission.FILESYSTEM_WRITE, reason="Create archive")
    async def _action_create_archive(self, params: dict[str, Any]) -> dict[str, Any]:
        stream = params.pop("_stream", None)
        p = CreateArchiveParams.model_validate(params)
        src = self._resolve_path(Path(p.source))

        format_map = {
            "zip": "zip",
            "tar": "tar",
            "tar.gz": "gztar",
            "tar.bz2": "bztar",
        }
        archive_format = format_map[p.format]
        dest = Path(p.destination)

        if stream:
            await stream.emit_status("creating_archive")
        await asyncio.to_thread(
            shutil.make_archive, str(dest.with_suffix("")), archive_format, str(src.parent), src.name
        )
        if stream:
            await stream.emit_progress(100, f"Archive created: {dest}")
        return {"archive": str(dest), "source": str(src)}

    @streams_progress
    @requires_permission(Permission.FILESYSTEM_WRITE, reason="Extract archive to disk")
    async def _action_extract_archive(self, params: dict[str, Any]) -> dict[str, Any]:
        stream = params.pop("_stream", None)
        p = ExtractArchiveParams.model_validate(params)
        src, dst = self._resolve_path(Path(p.source)), self._resolve_path(Path(p.destination))
        dst.mkdir(parents=True, exist_ok=True)
        if stream:
            await stream.emit_status("extracting")
        await asyncio.to_thread(shutil.unpack_archive, src, dst)
        if stream:
            await stream.emit_progress(100, f"Extracted to {dst}")
        return {"source": str(src), "destination": str(dst)}

    @streams_progress
    @requires_permission(Permission.FILESYSTEM_READ, reason="Compute file checksum")
    async def _action_compute_checksum(self, params: dict[str, Any]) -> dict[str, Any]:
        stream = params.pop("_stream", None)
        p = ComputeChecksumParams.model_validate(params)
        path = Path(p.path)

        if stream:
            await stream.emit_status("computing_checksum")

        def _hash() -> str:
            h = hashlib.new(p.algorithm)
            with path.open("rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
            return h.hexdigest()

        checksum = await asyncio.to_thread(_hash)
        if stream:
            await stream.emit_progress(100, f"{p.algorithm} checksum computed")
        return {"path": str(path), "algorithm": p.algorithm, "checksum": checksum}

    @streams_progress
    @requires_permission(Permission.FILESYSTEM_READ, reason="Watch path for changes")
    async def _action_watch_path(self, params: dict[str, Any]) -> dict[str, Any]:
        from llmos_bridge.protocol.params.filesystem import WatchPathParams

        stream = params.pop("_stream", None)
        p = WatchPathParams.model_validate(params)
        # Basic polling implementation — Phase 3 will integrate watchdog.
        path = Path(p.path)
        initial_mtime = path.stat().st_mtime if path.exists() else None
        deadline = time.time() + p.timeout

        while time.time() < deadline:
            await asyncio.sleep(0.5)
            if stream:
                elapsed = p.timeout - (deadline - time.time())
                pct = min(99.0, (elapsed / p.timeout) * 100)
                await stream.emit_progress(pct, f"Watching {path.name}")
            current_mtime = path.stat().st_mtime if path.exists() else None
            if current_mtime != initial_mtime:
                if stream:
                    await stream.emit_progress(100, "Change detected")
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
                        ParamSpec("max_bytes", "integer", "Maximum bytes to read.", required=False),
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
                        ParamSpec("encoding", "string", "Text encoding.", required=False, default="utf-8"),
                        ParamSpec("create_dirs", "boolean", "Create parent directories.", required=False, default=False),
                        ParamSpec("overwrite", "boolean", "Overwrite existing file.", required=False, default=True),
                    ],
                    returns_description='{"path": str, "bytes_written": int}',
                    permission_required="local_worker",
                ),
                ActionSpec(
                    name="append_file",
                    description="Append text content to an existing file.",
                    params=[
                        ParamSpec("path", "string", "Path to the file to append to."),
                        ParamSpec("content", "string", "Text to append."),
                        ParamSpec("encoding", "string", "Text encoding.", required=False, default="utf-8"),
                        ParamSpec("newline", "boolean", "Prepend a newline before appending.", required=False, default=True),
                    ],
                    returns_description='{"path": str, "bytes_appended": int}',
                    permission_required="local_worker",
                ),
                ActionSpec(
                    name="copy_file",
                    description="Copy a file or directory to a new location.",
                    params=[
                        ParamSpec("source", "string", "Source path."),
                        ParamSpec("destination", "string", "Destination path."),
                        ParamSpec("overwrite", "boolean", "Overwrite if destination exists.", required=False, default=False),
                    ],
                    returns_description='{"source": str, "destination": str}',
                    permission_required="local_worker",
                ),
                ActionSpec(
                    name="move_file",
                    description="Move or rename a file or directory.",
                    params=[
                        ParamSpec("source", "string", "Source path."),
                        ParamSpec("destination", "string", "Destination path."),
                        ParamSpec("overwrite", "boolean", "Overwrite if destination exists.", required=False, default=False),
                    ],
                    returns_description='{"source": str, "destination": str}',
                    permission_required="local_worker",
                ),
                ActionSpec(
                    name="delete_file",
                    description="Delete a file or directory.",
                    params=[
                        ParamSpec("path", "string", "Path to delete."),
                        ParamSpec("recursive", "boolean", "Delete directory recursively.", required=False, default=False),
                    ],
                    returns_description='{"deleted": str}',
                    permission_required="power_user",
                ),
                ActionSpec(
                    name="create_directory",
                    description="Create a new directory, optionally creating parent directories.",
                    params=[
                        ParamSpec("path", "string", "Path of the directory to create."),
                        ParamSpec("parents", "boolean", "Create all missing parent directories.", required=False, default=True),
                        ParamSpec("exist_ok", "boolean", "Do not raise if directory exists.", required=False, default=True),
                    ],
                    returns_description='{"path": str, "created": bool}',
                    permission_required="local_worker",
                ),
                ActionSpec(
                    name="list_directory",
                    description="List the contents of a directory with optional glob filtering.",
                    params=[
                        ParamSpec("path", "string", "Directory to list."),
                        ParamSpec("recursive", "boolean", "Recurse into subdirectories.", required=False, default=False),
                        ParamSpec("pattern", "string", "Glob pattern to filter entries (e.g. '*.py').", required=False),
                        ParamSpec("include_hidden", "boolean", "Include hidden files.", required=False, default=False),
                        ParamSpec("max_results", "integer", "Maximum entries to return.", required=False, default=500),
                    ],
                    returns_description='{"path": str, "entries": [...], "count": int}',
                ),
                ActionSpec(
                    name="search_files",
                    description="Search for files by name pattern and optional content regex.",
                    params=[
                        ParamSpec("directory", "string", "Root directory for the search."),
                        ParamSpec("pattern", "string", "Glob filename pattern (e.g. '*.log')."),
                        ParamSpec("content_pattern", "string", "Regex to match inside file contents.", required=False),
                        ParamSpec("case_sensitive", "boolean", "Case-sensitive content matching.", required=False, default=False),
                        ParamSpec("max_results", "integer", "Maximum results to return.", required=False, default=100),
                    ],
                    returns_description='{"matches": [...], "count": int}',
                ),
                ActionSpec(
                    name="get_file_info",
                    description="Get metadata about a file or directory (size, permissions, timestamps).",
                    params=[
                        ParamSpec("path", "string", "Path to inspect."),
                    ],
                    returns_description='{"path", "name", "type", "size_bytes", "created", "modified", "permissions", "is_symlink", "suffix"}',
                ),
                ActionSpec(
                    name="create_archive",
                    description="Create a zip, tar, tar.gz, or tar.bz2 archive from a file or directory.",
                    params=[
                        ParamSpec("source", "string", "File or directory to archive."),
                        ParamSpec("destination", "string", "Output archive path."),
                        ParamSpec("format", "string", "Archive format.", required=False, default="zip",
                                  enum=["zip", "tar", "tar.gz", "tar.bz2"]),
                    ],
                    returns_description='{"archive": str, "source": str}',
                    permission_required="local_worker",
                ),
                ActionSpec(
                    name="extract_archive",
                    description="Extract an archive to a destination directory.",
                    params=[
                        ParamSpec("source", "string", "Archive file to extract."),
                        ParamSpec("destination", "string", "Directory to extract into."),
                        ParamSpec("overwrite", "boolean", "Overwrite existing files.", required=False, default=False),
                    ],
                    returns_description='{"source": str, "destination": str}',
                    permission_required="local_worker",
                ),
                ActionSpec(
                    name="compute_checksum",
                    description="Compute a hash checksum (md5, sha1, sha256, sha512) of a file.",
                    params=[
                        ParamSpec("path", "string", "File to hash."),
                        ParamSpec("algorithm", "string", "Hash algorithm.", required=False, default="sha256",
                                  enum=["md5", "sha1", "sha256", "sha512"]),
                    ],
                    returns_description='{"path": str, "algorithm": str, "checksum": str}',
                ),
                ActionSpec(
                    name="watch_path",
                    description="Watch a file or directory for changes within a timeout window.",
                    params=[
                        ParamSpec("path", "string", "File or directory to watch."),
                        ParamSpec("events", "array", "Event types to watch.", required=False,
                                  example=["created", "modified", "deleted"]),
                        ParamSpec("timeout", "integer", "Watch timeout in seconds.", required=False, default=60),
                    ],
                    returns_description='{"path": str, "event": str, "detected_at": float} or {"event": "timeout"}',
                ),
            ],
        )
