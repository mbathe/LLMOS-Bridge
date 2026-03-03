# Filesystem Module

Read, write, move, copy, delete files and directories. Create and extract archives.

## Overview

The Filesystem module provides comprehensive file and directory operations for IML
plans. It handles reading and writing text files, moving and copying files,
recursive directory management, archive creation/extraction, and file watching.
All path operations use `pathlib` with symlink resolution to prevent
write-through-symlink attacks. The PermissionGuard enforces sandbox restrictions
upstream before any I/O reaches this module.

## Actions

| Action | Description | Risk | Permission |
|--------|-------------|------|------------|
| `read_file` | Read the content of a text file | Low | `filesystem.read` |
| `write_file` | Write text content to a file, creating it if necessary | Medium | `filesystem.write` |
| `append_file` | Append text content to an existing file | Medium | `filesystem.write` |
| `copy_file` | Copy a file or directory to a new location | Medium | `filesystem.read`, `filesystem.write` |
| `move_file` | Move or rename a file or directory | Medium | `filesystem.read`, `filesystem.write` |
| `delete_file` | Delete a file or directory | High | `filesystem.delete` |
| `create_directory` | Create a new directory with optional parent creation | Medium | `filesystem.write` |
| `list_directory` | List directory contents with optional glob filtering | Low | `filesystem.read` |
| `search_files` | Search for files by name pattern and optional content regex | Low | `filesystem.read` |
| `get_file_info` | Get metadata about a file or directory | Low | `filesystem.read` |
| `create_archive` | Create a zip/tar/tar.gz/tar.bz2 archive | Medium | `filesystem.read`, `filesystem.write` |
| `extract_archive` | Extract an archive to a destination directory | Medium | `filesystem.write` |
| `compute_checksum` | Compute a hash checksum of a file | Low | `filesystem.read` |
| `watch_path` | Watch a file or directory for changes within a timeout | Low | `filesystem.read` |

## Quick Start

```yaml
actions:
  - id: read-config
    module: filesystem
    action: read_file
    params:
      path: /etc/myapp/config.yaml
```

## Requirements

No external dependencies required. The module uses only Python standard library
modules (`pathlib`, `shutil`, `hashlib`, `os`, `stat`).

## Configuration

Uses default LLMOS Bridge configuration. Sandbox paths are enforced by the
upstream PermissionGuard via `SecurityConfig.sandbox_paths`.

## Platform Support

| Platform | Status |
|----------|--------|
| Linux | Supported |
| macOS | Supported |
| Windows | Supported |

## Related Modules

- **os_exec** -- Execute system commands, useful for file operations not covered
  by this module (e.g., `chmod`, `chown`).
- **gui** -- GUI automation that may read/write files as part of workflows.
