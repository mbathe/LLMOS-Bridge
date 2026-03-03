# Changelog -- Filesystem Module

## [1.0.0] -- 2026-01-15

### Added
- Initial release with 14 actions.
- `read_file` -- Read text files with optional line range and byte limit.
- `write_file` -- Write text files with atomic exclusive create support.
- `append_file` -- Append text to existing files.
- `copy_file` -- Copy files preserving metadata via `shutil.copy2`.
- `move_file` -- Move or rename files and directories.
- `delete_file` -- Delete files or directories (recursive optional).
- `create_directory` -- Create directories with parent creation.
- `list_directory` -- List directory contents with glob pattern and pagination.
- `search_files` -- Recursive file search by name glob and content regex.
- `get_file_info` -- Retrieve file metadata (size, permissions, timestamps).
- `create_archive` -- Create zip, tar, tar.gz, and tar.bz2 archives.
- `extract_archive` -- Extract archives to a target directory.
- `compute_checksum` -- Compute md5, sha1, sha256, or sha512 checksums.
- `watch_path` -- Poll-based file change detection with configurable timeout.
- Symlink resolution on all write paths to prevent write-through-symlink attacks.
- Security decorators: `@requires_permission`, `@rate_limited`, `@audit_trail`, `@sensitive_action`.
