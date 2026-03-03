# Example Module

Example LLMOS Bridge module that greets people and counts words. Use as a template for building your own modules.

## Overview

This module demonstrates the minimum required structure for an LLMOS Bridge module (Module Spec v3). It provides two simple actions: generating greetings and counting words in text. Use this as a starting point for building your own community modules.

## Actions

| Action | Description | Risk | Permission |
|--------|-------------|------|------------|
| say_hello | Generate a greeting for a person | Low | readonly |
| count_words | Count the number of words in a text string | Low | readonly |

## Quick Start

```yaml
actions:
  - id: greet
    module: example
    action: say_hello
    params:
      name: "Alice"
      formal: false

  - id: count
    module: example
    action: count_words
    params:
      text: "Hello world, this is a test."
```

## Requirements

No external dependencies required.

## Configuration

Uses default LLMOS Bridge configuration. No module-specific settings.

## Platform Support

| Platform | Status |
|----------|--------|
| Linux | Supported |
| macOS | Supported |
| Windows | Supported |

## Related Modules

This is a standalone example module with no dependencies on other modules.
