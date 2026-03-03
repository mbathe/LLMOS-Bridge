# Example Module — Integration Guide

## Cross-Module Workflows

### Greet and Analyze

Combine the example module with other modules for demonstration purposes.

```yaml
plan_id: greet_and_analyze
protocol_version: "2.0"
description: "Greet a user and count words in the greeting"
actions:
  - id: greet
    module: example
    action: say_hello
    params:
      name: "Alice"
      formal: true

  - id: analyze
    module: example
    action: count_words
    depends_on: [greet]
    params:
      text: "{{result.greet.greeting}}"
```

This workflow demonstrates template references between actions using `{{result.<action_id>.<field>}}` syntax.

## Building Your Own Workflows

When creating a community module, document common workflows that combine your module with existing LLMOS Bridge modules. Include complete IML YAML examples that users can copy and adapt.
