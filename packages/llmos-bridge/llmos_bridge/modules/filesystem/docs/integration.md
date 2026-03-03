# Filesystem Module -- Integration Guide

## Cross-Module Workflows

### Backup and Archive

Use `filesystem` to read and archive project files, then `os_exec` to upload the
archive to a remote server.

```yaml
actions:
  - id: create-backup
    module: filesystem
    action: create_archive
    params:
      source: /home/user/project
      destination: /tmp/backup.tar.gz
      format: tar.gz

  - id: upload-backup
    module: os_exec
    action: run_command
    depends_on: [create-backup]
    params:
      command: ["rsync", "-avz", "/tmp/backup.tar.gz", "server:/backups/"]
      timeout: 120
```

### Read, Transform, Write Pipeline

Read a configuration file, pass the content to a downstream action for
transformation, and write the result back.

```yaml
actions:
  - id: read-config
    module: filesystem
    action: read_file
    params:
      path: /etc/myapp/config.yaml

  - id: write-transformed
    module: filesystem
    action: write_file
    depends_on: [read-config]
    params:
      path: /etc/myapp/config.yaml.new
      content: "{{result.read-config.content}}"
      overwrite: false
```

### Build Output Verification

After running a build command, verify the output file exists and compute its
checksum for integrity tracking.

```yaml
actions:
  - id: run-build
    module: os_exec
    action: run_command
    params:
      command: ["make", "build"]
      working_directory: /home/user/project

  - id: check-output
    module: filesystem
    action: get_file_info
    depends_on: [run-build]
    params:
      path: /home/user/project/dist/app.bin

  - id: checksum-output
    module: filesystem
    action: compute_checksum
    depends_on: [check-output]
    params:
      path: /home/user/project/dist/app.bin
      algorithm: sha256
```

### Watch and React

Watch a directory for new files, then process them when they arrive.

```yaml
actions:
  - id: wait-for-upload
    module: filesystem
    action: watch_path
    params:
      path: /var/incoming/data.csv
      timeout: 300

  - id: process-upload
    module: filesystem
    action: read_file
    depends_on: [wait-for-upload]
    params:
      path: /var/incoming/data.csv

  - id: archive-processed
    module: filesystem
    action: move_file
    depends_on: [process-upload]
    params:
      source: /var/incoming/data.csv
      destination: /var/archive/data.csv
```

### Log Search and Cleanup

Search log files for errors, read the matching files, and optionally clean up
old logs.

```yaml
actions:
  - id: find-error-logs
    module: filesystem
    action: search_files
    params:
      directory: /var/log/myapp
      pattern: "*.log"
      content_pattern: "ERROR|CRITICAL"

  - id: list-old-logs
    module: filesystem
    action: list_directory
    params:
      path: /var/log/myapp
      pattern: "*.log.*.gz"
      recursive: false

  - id: cleanup-old-logs
    module: filesystem
    action: delete_file
    depends_on: [list-old-logs]
    requires_approval: true
    params:
      path: /var/log/myapp/archive
      recursive: true
```
