"""API layer — System Prompt Generator.

Builds a complete, production-quality system prompt from the current state of
the daemon: loaded modules, their action manifests, IML v2 protocol rules,
permission model, and error handling guidance.

The generated prompt is designed to be injected as the LLM *system message*
so that the model can autonomously compose valid IML plans.

Usage::

    from llmos_bridge.api.prompt import SystemPromptGenerator

    generator = SystemPromptGenerator(
        manifests=[fs_manifest, exec_manifest],
        permission_profile="local_worker",
    )
    prompt = generator.generate()
"""

from __future__ import annotations

from typing import Any

from llmos_bridge.modules.manifest import ActionSpec, ModuleManifest


_IML_PROTOCOL_SECTION = """\
## IML Protocol v2

You interact with the operating system by emitting **IML plans** — JSON \
documents that the LLMOS Bridge daemon executes on your behalf.

### Plan structure

```json
{
  "plan_id": "<unique-uuid>",
  "protocol_version": "2.0",
  "description": "Human-readable description of what this plan does",
  "execution_mode": "sequential",
  "actions": [
    {
      "id": "step_1",
      "module": "<module_id>",
      "action": "<action_name>",
      "params": { ... }
    }
  ]
}
```

### Key rules

1. **plan_id** must be a unique UUID string.
2. **protocol_version** must be `"2.0"`.
3. **execution_mode** can be `"sequential"` (default), `"parallel"`, or `"reactive"`.
4. Each action has a unique **id** (alphanumeric + underscores/hyphens).
5. **module** is the module_id and **action** is the action name from the \
capability list below.
6. **params** must match the action's parameter schema exactly.

### Action chaining with depends_on

Actions can depend on previous actions:

```json
{
  "id": "step_2",
  "module": "filesystem",
  "action": "write_file",
  "params": { "path": "/tmp/output.txt", "content": "{{result.step_1.stdout}}" },
  "depends_on": ["step_1"]
}
```

Template expressions `{{result.<action_id>.<field>}}` inject output from \
completed actions. Available templates:
- `{{result.<id>.<field>}}` — result of a previous action
- `{{memory.<key>}}` — value from the key-value memory store
- `{{env.<VAR>}}` — environment variable (if allowed by permission profile)

### Error handling

Each action can specify:
- `"on_error": "abort"` — stop the entire plan (default)
- `"on_error": "continue"` — skip this action and continue
- `"on_error": "retry"` — retry with backoff

For retries:
```json
{
  "retry": { "max_attempts": 3, "delay_seconds": 1.0, "backoff_factor": 2.0 }
}
```

### Approval gates

Some actions require user approval before execution. You can explicitly mark:
```json
{ "requires_approval": true }
```
"""

_PERCEPTION_SECTION = """\
## Perception (Visual Feedback Loop)

Actions can capture screenshots before and after execution, run OCR on them, \
and detect visual changes. This allows you to **verify** that an action had \
the intended visual effect.

### Adding perception to an action

```json
{
  "id": "click_button",
  "module": "os_exec",
  "action": "run_command",
  "params": { "command": ["xdotool", "click", "1"] },
  "perception": {
    "capture_before": true,
    "capture_after": true,
    "ocr_enabled": true,
    "validate_output": "contains:Success"
  }
}
```

### Perception fields

- `capture_before` (bool): Take a screenshot before the action runs.
- `capture_after` (bool): Take a screenshot after the action runs.
- `ocr_enabled` (bool): Run OCR on the captured screenshots.
- `validate_output` (string): Validate OCR text — `"contains:<text>"` or `"regex:<pattern>"`.

### Accessing perception results in templates

Downstream actions can reference perception data:
- `{{result.<action_id>._perception.after_text}}` — OCR text from post-action screenshot
- `{{result.<action_id>._perception.before_text}}` — OCR text from pre-action screenshot
- `{{result.<action_id>._perception.diff_detected}}` — `true` if visual change detected
- `{{result.<action_id>._perception.ocr_confidence}}` — OCR confidence score (0-100)
- `{{result.<action_id>._perception.validation_passed}}` — whether validate_output matched

### When to use perception

- GUI automation: verify a button click changed the screen
- Document generation: verify a PDF was created correctly
- Web scraping: detect page changes after navigation
- **Do NOT use perception for pure data operations** (file I/O, API calls) — it adds overhead
"""

_MEMORY_SECTION = """\
## Memory (Key-Value Store)

Actions can **read from** and **write to** a persistent key-value memory store. \
This allows data to flow between plans and persist across sessions.

### Reading memory in an action

```json
{
  "id": "use_config",
  "module": "filesystem",
  "action": "write_file",
  "params": {
    "path": "/tmp/output.txt",
    "content": "{{memory.last_config}}"
  },
  "memory": { "read_keys": ["last_config"] }
}
```

### Writing to memory after an action

```json
{
  "id": "read_config",
  "module": "filesystem",
  "action": "read_file",
  "params": { "path": "/etc/myapp/config.yaml" },
  "memory": { "write_key": "last_config" }
}
```

The action's result is stored under the key `"last_config"` and can be used \
by later actions or future plans via `{{memory.last_config}}`.

### When to use memory

- Store intermediate results across multiple plans
- Cache frequently-read configuration
- Pass context from one user session to the next
"""

_GUIDELINES_SECTION = """\
## Guidelines

- Always use the **simplest plan** that achieves the goal.
- Prefer **sequential** execution unless actions are truly independent.
- Use **depends_on** to express data flow between actions.
- Never fabricate module or action names — only use those listed below.
- When unsure about parameters, refer to the schema descriptions.
- For filesystem operations, always use **absolute paths**.
- Handle errors gracefully: use `on_error` and `retry` when appropriate.
- One plan per user request unless the task naturally splits into independent operations.
- Use **perception** only for visual/GUI operations, not for data-only tasks.
- Use **memory** to persist data across plans; use **depends_on + templates** within a plan.
"""


class SystemPromptGenerator:
    """Generates a complete LLM system prompt from module manifests.

    The prompt includes:
    - Role description and identity
    - IML v2 protocol rules
    - Available modules and actions with parameter schemas
    - Permission model explanation
    - Error handling guidance
    - Few-shot examples (from action specs)
    """

    def __init__(
        self,
        manifests: list[ModuleManifest],
        permission_profile: str = "local_worker",
        daemon_version: str = "",
        include_schemas: bool = True,
        include_examples: bool = True,
        max_actions_per_module: int | None = None,
    ) -> None:
        self._manifests = manifests
        self._permission_profile = permission_profile
        self._daemon_version = daemon_version
        self._include_schemas = include_schemas
        self._include_examples = include_examples
        self._max_actions_per_module = max_actions_per_module

    def generate(self) -> str:
        """Generate the full system prompt."""
        sections = [
            self._build_identity(),
            _IML_PROTOCOL_SECTION,
            self._build_capabilities(),
            self._build_permission_section(),
            _PERCEPTION_SECTION,
            _MEMORY_SECTION,
            _GUIDELINES_SECTION,
        ]

        examples = self._build_examples()
        if examples:
            sections.append(examples)

        return "\n".join(sections)

    def _build_identity(self) -> str:
        version_str = f" v{self._daemon_version}" if self._daemon_version else ""
        module_count = len(self._manifests)
        action_count = sum(len(m.actions) for m in self._manifests)
        return (
            f"# LLMOS Bridge{version_str} — System Assistant\n\n"
            f"You are an AI assistant with direct access to the local operating "
            f"system through the LLMOS Bridge daemon. You have **{module_count} "
            f"modules** providing **{action_count} actions** available.\n\n"
            f"Your role is to translate user requests into IML (Instruction Markup "
            f"Language) v2 plans that the daemon executes. You can read/write files, "
            f"run commands, manage documents, call APIs, and more — all through "
            f"structured IML plans.\n\n"
            f"Current permission profile: **{self._permission_profile}**\n"
        )

    def _build_capabilities(self) -> str:
        if not self._manifests:
            return "## Available Modules\n\nNo modules loaded.\n"

        parts = ["## Available Modules\n"]

        for manifest in self._manifests:
            parts.append(
                f"\n### {manifest.module_id} (v{manifest.version})\n"
                f"{manifest.description}\n"
            )

            actions = manifest.actions
            if self._max_actions_per_module is not None:
                actions = actions[: self._max_actions_per_module]

            for action in actions:
                parts.append(self._format_action(action))

            if (
                self._max_actions_per_module is not None
                and len(manifest.actions) > self._max_actions_per_module
            ):
                remaining = len(manifest.actions) - self._max_actions_per_module
                parts.append(f"\n  ... and {remaining} more actions.\n")

        return "\n".join(parts)

    def _format_action(self, action: ActionSpec) -> str:
        lines = [f"\n- **{action.name}**: {action.description}"]

        if action.permission_required != "local_worker":
            lines.append(f"  - Permission: `{action.permission_required}`")

        if self._include_schemas and action.params:
            lines.append("  - Parameters:")
            for param in action.params:
                req = " *(required)*" if param.required else f" *(default: {param.default})*"
                type_str = param.type
                if param.enum:
                    type_str += f" — one of: {param.enum}"
                lines.append(f"    - `{param.name}` ({type_str}): {param.description}{req}")

        return "\n".join(lines)

    def _build_permission_section(self) -> str:
        profile = self._permission_profile
        descriptions = {
            "readonly": (
                "You can only **read** files and system information. "
                "No writes, no command execution, no network calls."
            ),
            "local_worker": (
                "You can read/write files, run safe commands, call HTTP APIs, "
                "and manage Office documents. Destructive actions (delete files, "
                "kill processes) require explicit user approval."
            ),
            "power_user": (
                "Full access to local operations plus browser automation, GUI "
                "control, database writes, and IoT devices. Destructive actions "
                "are allowed without approval."
            ),
            "unrestricted": (
                "All actions are allowed without restrictions. Use with extreme caution."
            ),
        }

        desc = descriptions.get(profile, descriptions["local_worker"])

        return (
            f"## Permission Model\n\n"
            f"Current profile: **{profile}**\n\n"
            f"{desc}\n\n"
            f"If an action is denied by the permission system, do NOT retry it. "
            f"Inform the user that the action requires a higher permission level.\n"
        )

    def _build_examples(self) -> str:
        if not self._include_examples:
            return ""

        # Collect examples from action specs
        action_examples: list[dict[str, Any]] = []
        for manifest in self._manifests:
            for action in manifest.actions:
                for ex in action.examples:
                    action_examples.append(
                        {"module": manifest.module_id, "action": action.name, "example": ex}
                    )

        # Build built-in examples (always available)
        parts = ["## Examples\n"]

        # Example 1: Simple file read
        has_filesystem = any(m.module_id == "filesystem" for m in self._manifests)
        has_os_exec = any(m.module_id == "os_exec" for m in self._manifests)

        if has_filesystem:
            parts.append(
                '### Read a file\n\n'
                '```json\n'
                '{\n'
                '  "plan_id": "read-example-001",\n'
                '  "protocol_version": "2.0",\n'
                '  "description": "Read the contents of config.yaml",\n'
                '  "actions": [\n'
                '    {\n'
                '      "id": "read",\n'
                '      "module": "filesystem",\n'
                '      "action": "read_file",\n'
                '      "params": { "path": "/home/user/config.yaml" }\n'
                '    }\n'
                '  ]\n'
                '}\n'
                '```\n'
            )

        # Example 2: Chained actions
        if has_filesystem and has_os_exec:
            parts.append(
                '### Run a command and save output\n\n'
                '```json\n'
                '{\n'
                '  "plan_id": "chain-example-001",\n'
                '  "protocol_version": "2.0",\n'
                '  "description": "List files and save to report",\n'
                '  "execution_mode": "sequential",\n'
                '  "actions": [\n'
                '    {\n'
                '      "id": "list",\n'
                '      "module": "os_exec",\n'
                '      "action": "run_command",\n'
                '      "params": { "command": ["ls", "-la", "/tmp"] }\n'
                '    },\n'
                '    {\n'
                '      "id": "save",\n'
                '      "module": "filesystem",\n'
                '      "action": "write_file",\n'
                '      "params": {\n'
                '        "path": "/tmp/report.txt",\n'
                '        "content": "{{result.list.stdout}}"\n'
                '      },\n'
                '      "depends_on": ["list"]\n'
                '    }\n'
                '  ]\n'
                '}\n'
                '```\n'
            )

        # Add action-level examples if any exist
        if action_examples:
            parts.append("### Module-specific examples\n")
            for ae in action_examples[:5]:  # Limit to 5 to avoid bloat
                parts.append(
                    f"**{ae['module']}.{ae['action']}**:\n"
                    f"```json\n{_compact_json(ae['example'])}\n```\n"
                )

        return "\n".join(parts) if len(parts) > 1 else ""

    def to_dict(self) -> dict[str, Any]:
        """Return a structured dict representation (for JSON API response)."""
        return {
            "system_prompt": self.generate(),
            "permission_profile": self._permission_profile,
            "daemon_version": self._daemon_version,
            "modules": [
                {
                    "module_id": m.module_id,
                    "version": m.version,
                    "action_count": len(m.actions),
                }
                for m in self._manifests
            ],
            "total_actions": sum(len(m.actions) for m in self._manifests),
        }


def _compact_json(obj: Any) -> str:
    """Pretty-print a dict as compact JSON."""
    import json
    return json.dumps(obj, indent=2, default=str)
