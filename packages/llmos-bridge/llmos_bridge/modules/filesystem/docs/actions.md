# Filesystem Module -- Action Reference

## read_file

Read the content of a text file.

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Absolute or relative path to the file |
| `encoding` | string | No | `"utf-8"` | Text encoding |
| `start_line` | integer | No | -- | First line to read (1-indexed, inclusive) |
| `end_line` | integer | No | -- | Last line to read (1-indexed, inclusive) |
| `max_bytes` | integer | No | -- | Maximum bytes to read (max 10 MB) |

### Returns

```json
{
  "path": "string",
  "content": "string",
  "size_bytes": "integer"
}
```

### Examples

```yaml
actions:
  - id: read-script
    module: filesystem
    action: read_file
    params:
      path: /home/user/script.py

  - id: read-lines
    module: filesystem
    action: read_file
    params:
      path: /var/log/app.log
      start_line: 1
      end_line: 50
```

### Security

- Permission: `filesystem.read`
- Risk Level: Low

---

## write_file

Write text content to a file, creating it if necessary.

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Absolute or relative path to write to |
| `content` | string | Yes | -- | Text content to write |
| `encoding` | string | No | `"utf-8"` | Text encoding |
| `create_dirs` | boolean | No | `false` | Create parent directories if they do not exist |
| `overwrite` | boolean | No | `true` | Overwrite the file if it exists |

### Returns

```json
{
  "path": "string",
  "bytes_written": "integer"
}
```

### Examples

```yaml
actions:
  - id: write-config
    module: filesystem
    action: write_file
    params:
      path: /home/user/project/config.yaml
      content: "key: value\n"
      create_dirs: true

  - id: create-new-only
    module: filesystem
    action: write_file
    params:
      path: /tmp/lock.pid
      content: "12345"
      overwrite: false
```

### Security

- Permission: `filesystem.write`
- Risk Level: Medium
- Rate limited: 60 calls/minute
- Audit trail: standard

---

## append_file

Append text content to an existing file.

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the file to append to |
| `content` | string | Yes | -- | Text to append |
| `encoding` | string | No | `"utf-8"` | Text encoding |
| `newline` | boolean | No | `true` | Prepend a newline before appending |

### Returns

```json
{
  "path": "string",
  "bytes_appended": "integer"
}
```

### Examples

```yaml
actions:
  - id: append-log
    module: filesystem
    action: append_file
    params:
      path: /var/log/myapp.log
      content: "2026-01-15 Task completed successfully"
```

### Security

- Permission: `filesystem.write`
- Risk Level: Medium
- Rate limited: 60 calls/minute

---

## copy_file

Copy a file or directory to a new location, preserving metadata.

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `source` | string | Yes | -- | Source path |
| `destination` | string | Yes | -- | Destination path |
| `overwrite` | boolean | No | `false` | Overwrite destination if it exists |

### Returns

```json
{
  "source": "string",
  "destination": "string"
}
```

### Examples

```yaml
actions:
  - id: backup-config
    module: filesystem
    action: copy_file
    params:
      source: /etc/myapp/config.yaml
      destination: /etc/myapp/config.yaml.bak
      overwrite: true
```

### Security

- Permission: `filesystem.read`, `filesystem.write`
- Risk Level: Medium
- Rate limited: 60 calls/minute

---

## move_file

Move or rename a file or directory.

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `source` | string | Yes | -- | Source path |
| `destination` | string | Yes | -- | Destination path |
| `overwrite` | boolean | No | `false` | Overwrite destination if it exists |

### Returns

```json
{
  "source": "string",
  "destination": "string"
}
```

### Examples

```yaml
actions:
  - id: rename-file
    module: filesystem
    action: move_file
    params:
      source: /home/user/report_draft.pdf
      destination: /home/user/report_final.pdf
```

### Security

- Permission: `filesystem.read`, `filesystem.write`
- Risk Level: Medium
- Rate limited: 60 calls/minute

---

## delete_file

Delete a file or directory.

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to the file or empty directory to delete |
| `recursive` | boolean | No | `false` | If true, delete directory and all contents (rm -rf semantics) |

### Returns

```json
{
  "deleted": "string"
}
```

### Examples

```yaml
actions:
  - id: cleanup-temp
    module: filesystem
    action: delete_file
    params:
      path: /tmp/build-artifacts
      recursive: true
```

### Security

- Permission: `filesystem.delete`
- Risk Level: High (irreversible)
- Rate limited: 60 calls/minute
- Audit trail: detailed
- Marked as sensitive action

---

## create_directory

Create a new directory, optionally creating parent directories.

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path of the directory to create |
| `parents` | boolean | No | `true` | Create all missing parent directories |
| `exist_ok` | boolean | No | `true` | Do not raise if the directory already exists |

### Returns

```json
{
  "path": "string",
  "created": true
}
```

### Examples

```yaml
actions:
  - id: create-output-dir
    module: filesystem
    action: create_directory
    params:
      path: /home/user/project/output/reports
      parents: true
```

### Security

- Permission: `filesystem.write`
- Risk Level: Medium

---

## list_directory

List the contents of a directory with optional glob filtering.

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Directory to list |
| `recursive` | boolean | No | `false` | Recurse into subdirectories |
| `pattern` | string | No | -- | Glob pattern to filter entries (e.g. `*.py`) |
| `include_hidden` | boolean | No | `false` | Include hidden files (dotfiles) |
| `max_results` | integer | No | `500` | Maximum number of entries (1-10000) |

### Returns

```json
{
  "path": "string",
  "entries": [
    {
      "name": "string",
      "path": "string",
      "type": "file | directory",
      "size": "integer",
      "modified": "float"
    }
  ],
  "count": "integer"
}
```

### Examples

```yaml
actions:
  - id: list-python-files
    module: filesystem
    action: list_directory
    params:
      path: /home/user/project/src
      pattern: "*.py"
      recursive: true
```

### Security

- Permission: `filesystem.read`
- Risk Level: Low

---

## search_files

Search for files by name pattern and optional content regex.

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `directory` | string | Yes | -- | Root directory for the search |
| `pattern` | string | Yes | -- | Glob filename pattern (e.g. `*.log`) |
| `content_pattern` | string | No | -- | Regex to match inside file contents |
| `case_sensitive` | boolean | No | `false` | Case-sensitive content matching |
| `max_results` | integer | No | `100` | Maximum number of matches (1-1000) |

### Returns

```json
{
  "matches": [
    {
      "path": "string",
      "name": "string"
    }
  ],
  "count": "integer"
}
```

### Examples

```yaml
actions:
  - id: find-todo-comments
    module: filesystem
    action: search_files
    params:
      directory: /home/user/project
      pattern: "*.py"
      content_pattern: "TODO|FIXME|HACK"
```

### Security

- Permission: `filesystem.read`
- Risk Level: Low

---

## get_file_info

Get metadata about a file or directory (size, permissions, timestamps).

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | Path to inspect |

### Returns

```json
{
  "path": "string",
  "name": "string",
  "type": "file | directory",
  "size_bytes": "integer",
  "created": "float",
  "modified": "float",
  "permissions": "string (octal)",
  "is_symlink": "boolean",
  "suffix": "string"
}
```

### Examples

```yaml
actions:
  - id: check-size
    module: filesystem
    action: get_file_info
    params:
      path: /var/log/syslog
```

### Security

- Permission: `filesystem.read`
- Risk Level: Low

---

## create_archive

Create a zip, tar, tar.gz, or tar.bz2 archive from a file or directory.

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `source` | string | Yes | -- | File or directory to archive |
| `destination` | string | Yes | -- | Output archive path |
| `format` | string | No | `"zip"` | Archive format: `zip`, `tar`, `tar.gz`, `tar.bz2` |

### Returns

```json
{
  "archive": "string",
  "source": "string"
}
```

### Examples

```yaml
actions:
  - id: backup-project
    module: filesystem
    action: create_archive
    params:
      source: /home/user/project
      destination: /home/user/backups/project.tar.gz
      format: tar.gz
```

### Security

- Permission: `filesystem.read`, `filesystem.write`
- Risk Level: Medium

---

## extract_archive

Extract an archive to a destination directory.

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `source` | string | Yes | -- | Archive file to extract |
| `destination` | string | Yes | -- | Directory to extract into |
| `overwrite` | boolean | No | `false` | Overwrite existing files |

### Returns

```json
{
  "source": "string",
  "destination": "string"
}
```

### Examples

```yaml
actions:
  - id: unpack-release
    module: filesystem
    action: extract_archive
    params:
      source: /tmp/release-v2.0.tar.gz
      destination: /opt/myapp
```

### Security

- Permission: `filesystem.write`
- Risk Level: Medium

---

## compute_checksum

Compute a hash checksum (md5, sha1, sha256, sha512) of a file.

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | File to hash |
| `algorithm` | string | No | `"sha256"` | Hash algorithm: `md5`, `sha1`, `sha256`, `sha512` |

### Returns

```json
{
  "path": "string",
  "algorithm": "string",
  "checksum": "string"
}
```

### Examples

```yaml
actions:
  - id: verify-download
    module: filesystem
    action: compute_checksum
    params:
      path: /tmp/installer.iso
      algorithm: sha256
```

### Security

- Permission: `filesystem.read`
- Risk Level: Low

---

## watch_path

Watch a file or directory for changes within a timeout window.

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | -- | File or directory to watch |
| `events` | array | No | `["created", "modified", "deleted"]` | Event types to watch for |
| `timeout` | integer | No | `60` | Timeout in seconds (1-3600) |

### Returns

```json
{
  "path": "string",
  "event": "modified | deleted | timeout",
  "detected_at": "float (unix timestamp, absent on timeout)",
  "timeout": "integer (present only on timeout)"
}
```

### Examples

```yaml
actions:
  - id: wait-for-output
    module: filesystem
    action: watch_path
    params:
      path: /tmp/processing/result.json
      timeout: 120
```

### Security

- Permission: `filesystem.read`
- Risk Level: Low
