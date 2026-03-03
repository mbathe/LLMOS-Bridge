# Window Tracker Module -- Integration Guide

## Cross-Module Workflows

### GUI Automation with Focus Recovery

Track the target window during a multi-step GUI automation. If the user
switches to another window, recover focus before continuing.

```yaml
actions:
  - id: start-tracking
    module: window_tracker
    action: start_tracking
    params:
      title_pattern: "Firefox"

  - id: capture-screen
    module: vision
    action: capture_and_parse
    depends_on: [start-tracking]
    params:
      monitor: 0

  - id: find-button
    module: vision
    action: find_element
    depends_on: [capture-screen]
    params:
      query: "Submit"
      element_type: button

  - id: check-focus
    module: window_tracker
    action: detect_context_switch
    depends_on: [find-button]
    params: {}

  - id: recover-if-needed
    module: window_tracker
    action: recover_focus
    depends_on: [check-focus]
    params: {}

  - id: click-button
    module: computer_control
    action: click
    depends_on: [recover-if-needed]
    params:
      x: "{{result.find-button.pixel_x}}"
      y: "{{result.find-button.pixel_y}}"

  - id: stop-tracking
    module: window_tracker
    action: stop_tracking
    depends_on: [click-button]
    params: {}
```

### Window Discovery with Perception

List all windows, then use the vision module to capture and understand
the content of a specific window.

```yaml
actions:
  - id: list-all-windows
    module: window_tracker
    action: list_windows
    params: {}

  - id: focus-target
    module: window_tracker
    action: focus_window
    depends_on: [list-all-windows]
    params:
      title_pattern: "VS Code"

  - id: parse-screen
    module: vision
    action: capture_and_parse
    depends_on: [focus-target]
    params:
      monitor: 0

  - id: get-text
    module: vision
    action: get_screen_text
    depends_on: [focus-target]
    params: {}
```

### Recorded GUI Workflow with Focus Tracking

Combine recording, window tracking, and computer control for a fully
recorded and replayable GUI automation sequence.

```yaml
actions:
  - id: start-rec
    module: recording
    action: start_recording
    params:
      title: "Form fill workflow"

  - id: track-browser
    module: window_tracker
    action: start_tracking
    depends_on: [start-rec]
    params:
      title_pattern: "Chrome.*Form"

  - id: type-name
    module: computer_control
    action: type_text
    depends_on: [track-browser]
    params:
      text: "John Doe"

  - id: check-focus-mid
    module: window_tracker
    action: get_tracking_status
    depends_on: [type-name]
    params: {}

  - id: recover
    module: window_tracker
    action: recover_focus
    depends_on: [check-focus-mid]
    params: {}

  - id: stop-tracking
    module: window_tracker
    action: stop_tracking
    depends_on: [recover]
    params: {}

  - id: stop-rec
    module: recording
    action: stop_recording
    depends_on: [stop-tracking]
    params:
      recording_id: "{{result.start-rec.recording_id}}"
```

### Context Switch Monitoring with Triggers

Create a trigger that fires when too many context switches are detected
during a tracking session.

```yaml
actions:
  - id: start-tracking
    module: window_tracker
    action: start_tracking
    params:
      title_pattern: "IDE"

  - id: poll-switches
    module: window_tracker
    action: detect_context_switch
    depends_on: [start-tracking]
    params: {}
```

### Multi-Window Workflow

Focus different windows in sequence to perform cross-application tasks.

```yaml
actions:
  - id: focus-editor
    module: window_tracker
    action: focus_window
    params:
      title_pattern: "VS Code"

  - id: get-editor-info
    module: window_tracker
    action: get_active_window
    depends_on: [focus-editor]
    params: {}

  - id: focus-terminal
    module: window_tracker
    action: focus_window
    depends_on: [get-editor-info]
    params:
      title_pattern: "Terminal"

  - id: run-tests
    module: os_exec
    action: run_command
    depends_on: [focus-terminal]
    params:
      command: ["pytest", "-x"]
      working_directory: /home/user/project
```
