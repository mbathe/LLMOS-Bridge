# Visual Perception Module -- Integration Guide

## Cross-Module Workflows

### Click on a Detected Element

The most common pattern: find a UI element by label, then click on it
using the computer control module.

```yaml
actions:
  - id: find-button
    module: vision
    action: find_element
    params:
      query: "Submit"
      element_type: button

  - id: click-button
    module: computer_control
    action: click
    depends_on: [find-button]
    params:
      x: "{{result.find-button.pixel_x}}"
      y: "{{result.find-button.pixel_y}}"
```

### Focus Window, Parse, and Interact

Use the window tracker to ensure the correct window is focused before
parsing the screen and interacting with elements.

```yaml
actions:
  - id: focus-app
    module: window_tracker
    action: focus_window
    params:
      title_pattern: "My Application"

  - id: parse-screen
    module: vision
    action: capture_and_parse
    depends_on: [focus-app]
    params:
      monitor: 0

  - id: find-input
    module: vision
    action: find_element
    depends_on: [parse-screen]
    params:
      query: "Username"
      element_type: input

  - id: click-input
    module: computer_control
    action: click
    depends_on: [find-input]
    params:
      x: "{{result.find-input.pixel_x}}"
      y: "{{result.find-input.pixel_y}}"

  - id: type-username
    module: computer_control
    action: type_text
    depends_on: [click-input]
    params:
      text: "admin"
```

### Screen Text Extraction Pipeline

Extract text from the screen and write it to a file for processing.

```yaml
actions:
  - id: extract-text
    module: vision
    action: get_screen_text
    params: {}

  - id: save-text
    module: filesystem
    action: write_file
    depends_on: [extract-text]
    params:
      path: /tmp/screen-text.txt
      content: "{{result.extract-text.text}}"
```

### Visual Verification After Action

After performing an action, parse the screen again to verify the
expected result is visible.

```yaml
actions:
  - id: click-save
    module: computer_control
    action: click
    params:
      x: 500
      y: 300

  - id: verify-saved
    module: vision
    action: find_element
    depends_on: [click-save]
    params:
      query: "saved successfully"
      element_type: text

  - id: check-result
    module: vision
    action: get_screen_text
    depends_on: [click-save]
    params: {}
```

### Recorded Visual Workflow

Record a full perception-guided GUI automation for later replay.

```yaml
actions:
  - id: start-rec
    module: recording
    action: start_recording
    params:
      title: "Login workflow"
      description: "Automated login via visual perception"

  - id: parse-login
    module: vision
    action: capture_and_parse
    depends_on: [start-rec]
    params:
      monitor: 0

  - id: find-username
    module: vision
    action: find_element
    depends_on: [parse-login]
    params:
      query: "Username"
      element_type: input

  - id: click-username
    module: computer_control
    action: click
    depends_on: [find-username]
    params:
      x: "{{result.find-username.pixel_x}}"
      y: "{{result.find-username.pixel_y}}"

  - id: type-user
    module: computer_control
    action: type_text
    depends_on: [click-username]
    params:
      text: "admin"

  - id: find-password
    module: vision
    action: find_element
    depends_on: [type-user]
    params:
      query: "Password"
      element_type: input

  - id: click-password
    module: computer_control
    action: click
    depends_on: [find-password]
    params:
      x: "{{result.find-password.pixel_x}}"
      y: "{{result.find-password.pixel_y}}"

  - id: type-pass
    module: computer_control
    action: type_text
    depends_on: [click-password]
    params:
      text: "secret"

  - id: find-login-btn
    module: vision
    action: find_element
    depends_on: [type-pass]
    params:
      query: "Log In"
      element_type: button

  - id: click-login
    module: computer_control
    action: click
    depends_on: [find-login-btn]
    params:
      x: "{{result.find-login-btn.pixel_x}}"
      y: "{{result.find-login-btn.pixel_y}}"

  - id: stop-rec
    module: recording
    action: stop_recording
    depends_on: [click-login]
    params:
      recording_id: "{{result.start-rec.recording_id}}"
```

### Parse Specific Screen Region

Capture and parse only a specific region of the screen for faster processing.

```yaml
actions:
  - id: parse-sidebar
    module: vision
    action: capture_and_parse
    params:
      monitor: 0
      region:
        left: 0
        top: 0
        width: 300
        height: 1080

  - id: parse-toolbar
    module: vision
    action: capture_and_parse
    params:
      monitor: 0
      region:
        left: 0
        top: 0
        width: 1920
        height: 60
```
