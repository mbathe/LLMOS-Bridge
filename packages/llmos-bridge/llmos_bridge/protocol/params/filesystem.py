"""Typed parameter models for the ``filesystem`` module."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

# Maximum readable file size per request: 10 MB
_MAX_READ_BYTES = 10 * 1024 * 1024


class ReadFileParams(BaseModel):
    path: str = Field(description="Absolute or relative path to the file.")
    encoding: str = Field(default="utf-8", description="Text encoding.")
    start_line: Annotated[int, Field(ge=1)] | None = Field(
        default=None, description="First line to read (1-indexed, inclusive)."
    )
    end_line: Annotated[int, Field(ge=1)] | None = Field(
        default=None, description="Last line to read (1-indexed, inclusive)."
    )
    max_bytes: Annotated[int, Field(ge=1, le=_MAX_READ_BYTES)] | None = Field(
        default=None, description="Maximum bytes to read."
    )


class WriteFileParams(BaseModel):
    path: str = Field(description="Absolute or relative path to write to.")
    content: str = Field(description="Text content to write.")
    encoding: str = "utf-8"
    create_dirs: bool = Field(
        default=False, description="Create parent directories if they do not exist."
    )
    overwrite: bool = Field(default=True, description="Overwrite the file if it exists.")


class AppendFileParams(BaseModel):
    path: str = Field(description="Path to the file to append to.")
    content: str = Field(description="Text to append.")
    encoding: str = "utf-8"
    newline: bool = Field(
        default=True, description="Prepend a newline before appending."
    )


class CopyFileParams(BaseModel):
    source: str = Field(description="Source path.")
    destination: str = Field(description="Destination path.")
    overwrite: bool = False


class MoveFileParams(BaseModel):
    source: str = Field(description="Source path.")
    destination: str = Field(description="Destination path.")
    overwrite: bool = False


class DeleteFileParams(BaseModel):
    path: str = Field(description="Path to the file or empty directory to delete.")
    recursive: bool = Field(
        default=False,
        description="If True, delete directory and all its contents (rm -rf semantics).",
    )


class CreateDirectoryParams(BaseModel):
    path: str = Field(description="Path of the directory to create.")
    parents: bool = Field(default=True, description="Create all missing parent directories.")
    exist_ok: bool = Field(default=True, description="Do not raise if the directory exists.")


class ListDirectoryParams(BaseModel):
    path: str = Field(description="Directory to list.")
    recursive: bool = False
    pattern: str | None = Field(
        default=None, description="Glob pattern to filter entries (e.g. '*.py')."
    )
    include_hidden: bool = False
    max_results: Annotated[int, Field(ge=1, le=10_000)] = 500


class SearchFilesParams(BaseModel):
    directory: str = Field(description="Root directory for the search.")
    pattern: str = Field(description="Glob filename pattern (e.g. '*.log').")
    content_pattern: str | None = Field(
        default=None, description="Regex to match inside file contents."
    )
    case_sensitive: bool = False
    max_results: Annotated[int, Field(ge=1, le=1_000)] = 100


class GetFileInfoParams(BaseModel):
    path: str = Field(description="Path to inspect.")


class CreateArchiveParams(BaseModel):
    source: str = Field(description="File or directory to archive.")
    destination: str = Field(description="Output archive path.")
    format: Literal["zip", "tar", "tar.gz", "tar.bz2"] = "zip"


class ExtractArchiveParams(BaseModel):
    source: str = Field(description="Archive file to extract.")
    destination: str = Field(description="Directory to extract into.")
    overwrite: bool = False


class ComputeChecksumParams(BaseModel):
    path: str = Field(description="File to hash.")
    algorithm: Literal["md5", "sha1", "sha256", "sha512"] = "sha256"


class WatchPathParams(BaseModel):
    path: str = Field(description="File or directory to watch.")
    events: list[Literal["created", "modified", "deleted", "moved"]] = Field(
        default_factory=lambda: ["created", "modified", "deleted"]
    )
    timeout: Annotated[int, Field(ge=1, le=3600)] = 60


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

PARAMS_MAP: dict[str, type[BaseModel]] = {
    "read_file": ReadFileParams,
    "write_file": WriteFileParams,
    "append_file": AppendFileParams,
    "copy_file": CopyFileParams,
    "move_file": MoveFileParams,
    "delete_file": DeleteFileParams,
    "create_directory": CreateDirectoryParams,
    "list_directory": ListDirectoryParams,
    "search_files": SearchFilesParams,
    "get_file_info": GetFileInfoParams,
    "create_archive": CreateArchiveParams,
    "extract_archive": ExtractArchiveParams,
    "compute_checksum": ComputeChecksumParams,
    "watch_path": WatchPathParams,
}
