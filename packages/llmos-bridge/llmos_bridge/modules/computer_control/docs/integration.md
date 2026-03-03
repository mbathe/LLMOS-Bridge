# Computer Control Module -- Integration Guide

## Cross-Module Workflows

The `computer_control` module is the primary orchestration layer for the LLMOS computer use agent. It does not directly control the screen -- instead, it delegates to `vision` (perception) and `gui` (physical actions) modules via the `ModuleRegistry`.

### Architecture

```
LLM Agent (langchain-llmos SDK)
    |
    v
computer_control (semantic resolution)
    |
    +-- vision (OmniParserModule)       -- screen capture + element detection
    |       |
    |       +-- PerceptionCache         -- LRU + TTL caching of parse results
    |       +-- SpeculativePrefetcher   -- background pre-parsing
    |       +-- SceneGraphBuilder       -- hierarchical region detection
    |
    +-- gui (GUIModule)                 -- physical mouse/keyboard/screenshot
            |
            +-- TextInputEngine         -- layout-agnostic keyboard input
```

### Computer Use Agent Loop

The standard agent loop (implemented in `langchain_llmos`) follows this pattern:

```
1. read_screen        -> Get current UI state
2. LLM decides        -> Choose action based on elements + scene graph
3. click/type/scroll  -> Execute semantic action
4. (prefetch runs)    -> Background parse of post-action screen
5. read_screen        -> Verify result (uses prefetched cache)
6. Repeat until done
```

**IML Plan -- Login Workflow:**

```json
{
  "plan_id": "login-workflow",
  "protocol_version": "2.0",
  "description": "Log into the application",
  "execution_mode": "sequential",
  "actions": [
    {
      "id": "read-initial",
      "module": "computer_control",
      "action": "read_screen",
      "params": {}
    },
    {
      "id": "type-username",
      "module": "computer_control",
      "action": "type_into_element",
      "params": {
        "target_description": "Username or email input field",
        "text": "{{env.APP_USERNAME}}",
        "clear_first": true
      },
      "depends_on": ["read-initial"]
    },
    {
      "id": "type-password",
      "module": "computer_control",
      "action": "type_into_element",
      "params": {
        "target_description": "Password input field",
        "text": "{{env.APP_PASSWORD}}",
        "clear_first": true
      },
      "depends_on": ["type-username"]
    },
    {
      "id": "click-login",
      "module": "computer_control",
      "action": "click_element",
      "params": {"target_description": "Login button or Sign in button"},
      "depends_on": ["type-password"]
    },
    {
      "id": "wait-dashboard",
      "module": "computer_control",
      "action": "wait_for_element",
      "params": {
        "target_description": "Dashboard or Welcome message",
        "timeout": 15.0
      },
      "depends_on": ["click-login"]
    }
  ]
}
```

### Computer Control + Window Tracker

Combine semantic GUI actions with window context awareness:

```json
{
  "plan_id": "multi-window-workflow",
  "protocol_version": "2.0",
  "actions": [
    {
      "id": "list-windows",
      "module": "window_tracker",
      "action": "list_windows",
      "params": {}
    },
    {
      "id": "focus-target",
      "module": "window_tracker",
      "action": "focus_window",
      "params": {"title_pattern": "Spreadsheet"},
      "depends_on": ["list-windows"]
    },
    {
      "id": "read-spreadsheet",
      "module": "computer_control",
      "action": "read_screen",
      "params": {},
      "depends_on": ["focus-target"]
    },
    {
      "id": "click-cell",
      "module": "computer_control",
      "action": "click_element",
      "params": {"target_description": "Cell A1"},
      "depends_on": ["read-spreadsheet"]
    },
    {
      "id": "type-value",
      "module": "computer_control",
      "action": "type_into_element",
      "params": {
        "target_description": "Cell A1",
        "text": "Revenue Report Q1"
      },
      "depends_on": ["click-cell"]
    }
  ]
}
```

### Computer Control + Browser

Use `computer_control` for desktop GUI alongside `browser` for web content:

```json
{
  "plan_id": "download-and-open",
  "protocol_version": "2.0",
  "actions": [
    {
      "id": "open-browser",
      "module": "browser",
      "action": "open_browser",
      "params": {"headless": false}
    },
    {
      "id": "navigate",
      "module": "browser",
      "action": "navigate_to",
      "params": {"url": "https://example.com/report.pdf"},
      "depends_on": ["open-browser"]
    },
    {
      "id": "wait-download-dialog",
      "module": "computer_control",
      "action": "wait_for_element",
      "params": {
        "target_description": "Save file dialog or Download button",
        "timeout": 10.0
      },
      "depends_on": ["navigate"]
    },
    {
      "id": "click-save",
      "module": "computer_control",
      "action": "click_element",
      "params": {"target_description": "Save button"},
      "depends_on": ["wait-download-dialog"]
    }
  ]
}
```

## Performance Optimization

### Speculative Prefetching

After each physical action (click, type, interact), the module triggers a background screen parse. The next `read_screen` or element resolution reuses this cached result:

- **Without prefetch:** action (50ms) + wait + capture+parse (4000ms) = ~4050ms per iteration
- **With prefetch:** action (50ms) + background parse runs in parallel + next read hits cache = ~50ms per iteration

Enable via config:
```yaml
vision:
  speculative_prefetch: true
  cache_max_entries: 5
  cache_ttl_seconds: 2.0
```

### Perception Cache

Screen parse results are cached by MD5 hash of content fingerprint. If the screen hasn't changed between reads, the cached result is returned instantly.

### Element Resolution Strategy

The `ElementResolver` uses multiple matching strategies:
1. **Exact match** -- label exactly matches the description
2. **Contains match** -- label contains the description as a substring
3. **Fuzzy match** -- Levenshtein distance-based similarity
4. **Type-filtered match** -- filters candidates by `element_type` before matching

The best match is returned with a confidence score and the strategy used.

## Registry Setup

The `computer_control` module requires the registry to contain both `vision` and `gui` modules:

```python
from llmos_bridge.modules.registry import ModuleRegistry
from llmos_bridge.modules.gui.module import GUIModule
from llmos_bridge.modules.computer_control.module import ComputerControlModule
from llmos_bridge.modules.perception_vision.omniparser.module import OmniParserModule

registry = ModuleRegistry()
registry.register(GUIModule())
registry.register(OmniParserModule())  # Registers as "vision"

cc = ComputerControlModule()
cc.set_registry(registry)
registry.register(cc)
```

If either module is missing, actions will raise `ActionExecutionError` with a descriptive message.
