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
from llmos_bridge.security.profiles import (
    BUILTIN_PROFILES,
    PermissionProfile,
    PermissionProfileConfig,
)


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
- **Do NOT use perception for pure data operations** (file I/O, API calls, database queries) — \
the LLM learns what happened from the action result dict, not from screenshots.
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

_DB_GATEWAY_GUIDELINES = """\
## Database Operations (db_gateway)

### Workflow

1. Call `connect()` to open a connection (you get a `connection_id`).
2. Call `introspect()` to discover tables, columns, types, FKs, indexes.
3. Use read actions: `find()`, `find_one()`, `count()`, `search()`, `aggregate()`.
4. Use write actions: `create()`, `create_many()`, `update()`, `delete()` (if allowed).
5. Call `disconnect()` when done. Always pass `connection_id` in every action.

### Filter syntax (MongoDB-like)

All filter params use MongoDB-like operators — **never write raw SQL**:

- **Exact match**: `{"name": "Alice"}`
- **Comparison**: `{"age": {"$gt": 18}}`, `{"$gte": 18}`, `{"$lt": 100}`, `{"$lte": 50}`
- **Not equal**: `{"status": {"$ne": "deleted"}}`
- **In / Not in**: `{"role": {"$in": ["admin", "manager"]}}`, `{"$nin": [...]}`
- **Between**: `{"score": {"$between": [70, 90]}}`
- **Logical OR**: `{"$or": [{"status": "active"}, {"role": "admin"}]}`
- **Logical AND**: `{"$and": [{"age": {"$gte": 18}}, {"age": {"$lte": 65}}]}`

### Handling large result sets

- `find()` defaults to `limit: 100`. Use `offset` for pagination.
- Check `truncated` in the result: if `true`, more rows exist.
- Example pagination: `find({limit: 100, offset: 0})` → if truncated, \
`find({limit: 100, offset: 100})` → repeat until `truncated=false`.

### Aggregate column aliases

When using `aggregate()`, result columns are named `{function}_{column}`:
- `{"salary": "avg"}` → column alias: `avg_salary`
- `{"id": "count"}` → column alias: `count_id`
- Use these aliases in `having` and `order_by`: `{"having": {"count_id": {"$gte": 5}}}`

### Template chaining with results

Use `{{result.<action_id>.<field>}}` to reference database results:
- `{{result.find_users.rows}}` — all rows from a find action
- `{{result.find_users.row_count}}` — number of rows found
- `{{result.count_items.count}}` — count result
- `{{result.create_user.inserted_id}}` — ID of the created record

### Error recovery

- If a table is not found, call `introspect()` to refresh and check table names.
- If a column filter fails, verify column names via `introspect()`.
- If connection fails, check driver, host, port, and credentials.

### Security

- Respect the **DB user privileges** shown in the database context section (if present).
- If the DB user cannot INSERT, do not generate `create`/`create_many` actions.
- If the DB user cannot DELETE, do not generate `delete` actions.
- The `delete` action requires `confirm: true` as a safety flag.
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
        context_snippets: dict[str, str] | None = None,
        intent_verifier_active: bool = False,
    ) -> None:
        self._manifests = manifests
        self._permission_profile = permission_profile
        self._daemon_version = daemon_version
        self._include_schemas = include_schemas
        self._include_examples = include_examples
        self._max_actions_per_module = max_actions_per_module
        self._context_snippets = context_snippets or {}
        self._intent_verifier_active = intent_verifier_active
        self._profile_config = self._resolve_profile_config()

    def _resolve_profile_config(self) -> PermissionProfileConfig | None:
        """Resolve the active profile to its config (if it's a built-in)."""
        try:
            profile_enum = PermissionProfile(self._permission_profile)
            return BUILTIN_PROFILES[profile_enum]
        except (ValueError, KeyError):
            return None

    def _is_action_allowed(self, module_id: str, action_name: str) -> bool:
        """Check if the active profile allows this action."""
        if self._profile_config is None:
            return True  # Unknown profile → show everything
        return self._profile_config.is_allowed(module_id, action_name)

    def generate(self) -> str:
        """Generate the full system prompt."""
        sections = [
            self._build_identity(),
            _IML_PROTOCOL_SECTION,
            self._build_capabilities(),
            self._build_permission_section(),
        ]

        # Dynamic context from modules (e.g. database schemas)
        ctx = self._build_context_snippets()
        if ctx:
            sections.append(ctx)

        sections.extend([
            self._build_security_prompt_section(),
            self._build_intent_verifier_section(),
            _PERCEPTION_SECTION,
            _MEMORY_SECTION,
            _GUIDELINES_SECTION,
        ])

        # Add module-specific guidelines
        has_db_gateway = any(m.module_id == "db_gateway" for m in self._manifests)
        if has_db_gateway:
            sections.append(_DB_GATEWAY_GUIDELINES)

        examples = self._build_examples()
        if examples:
            sections.append(examples)

        return "\n".join(sections)

    def _build_context_snippets(self) -> str:
        """Build dynamic context section from module snippets."""
        if not self._context_snippets:
            return ""
        parts = []
        for _module_id, snippet in self._context_snippets.items():
            parts.append(snippet)
        return "\n\n".join(parts) + "\n"

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
            # Partition actions into allowed vs. denied by the active profile
            allowed_actions: list[ActionSpec] = []
            denied_actions: list[ActionSpec] = []
            for action in manifest.actions:
                if self._is_action_allowed(manifest.module_id, action.name):
                    allowed_actions.append(action)
                else:
                    denied_actions.append(action)

            parts.append(
                f"\n### {manifest.module_id} (v{manifest.version})\n"
                f"{manifest.description}\n"
            )

            actions = allowed_actions
            if self._max_actions_per_module is not None:
                actions = actions[: self._max_actions_per_module]

            for action in actions:
                parts.append(self._format_action(action, manifest.module_id))

            if (
                self._max_actions_per_module is not None
                and len(allowed_actions) > self._max_actions_per_module
            ):
                remaining = len(allowed_actions) - self._max_actions_per_module
                parts.append(f"\n  ... and {remaining} more actions.\n")

            # Explicitly list denied actions so the LLM does NOT try to use them
            if denied_actions:
                names = ", ".join(f"`{a.name}`" for a in denied_actions)
                parts.append(
                    f"\n  **Denied by current profile ({self._permission_profile}):** "
                    f"{names} — do NOT use these actions.\n"
                )

        return "\n".join(parts)

    def _format_action(self, action: ActionSpec, module_id: str = "") -> str:
        lines = [f"\n- **{action.name}**: {action.description}"]

        if action.permission_required != "local_worker":
            lines.append(f"  - Permission: `{action.permission_required}`")

        if action.permissions:
            lines.append(f"  - Required OS permissions: {', '.join(f'`{p}`' for p in action.permissions)}")

        if action.risk_level:
            risk_str = action.risk_level.upper()
            if action.irreversible:
                risk_str += " (irreversible)"
            lines.append(f"  - Risk level: **{risk_str}**")

        if action.returns_description:
            lines.append(f"  - Returns: {action.returns_description}")

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
                "manage Office documents, and query/write databases. "
                "Destructive actions (delete files, delete DB records, "
                "kill processes) are **denied** — do NOT generate plans that use them."
            ),
            "power_user": (
                "Full access to local operations plus browser automation, GUI "
                "control, database writes/deletes, and IoT devices. Destructive "
                "actions are allowed without approval."
            ),
            "unrestricted": (
                "All actions are allowed without restrictions. Use with extreme caution."
            ),
        }

        desc = descriptions.get(profile, descriptions["local_worker"])

        parts = [
            f"## Permission Model\n\n"
            f"Current profile: **{profile}**\n\n"
            f"{desc}\n\n"
            f"**IMPORTANT**: Never generate an IML plan that uses a denied action. "
            f"If the user asks for a denied operation, inform them that it requires "
            f"a higher permission level.\n"
        ]

        # Build explicit denied actions list from all loaded manifests
        if self._profile_config:
            denied_list: list[str] = []
            for manifest in self._manifests:
                for action in manifest.actions:
                    if not self._profile_config.is_allowed(
                        manifest.module_id, action.name
                    ):
                        denied_list.append(f"`{manifest.module_id}.{action.name}`")
            if denied_list:
                parts.append(
                    f"\n**Denied actions under {profile}**: "
                    + ", ".join(denied_list)
                    + "\n"
                )

        return "\n".join(parts)

    def _build_security_prompt_section(self) -> str:
        """Build the OS-level permission system explanation."""
        return (
            "## OS-Level Permission System\n\n"
            "Every module action that accesses a sensitive resource (filesystem, "
            "network, database, camera, processes) requires an **OS-level permission**. "
            "This works like Android/iOS: permissions must be explicitly granted.\n\n"
            "### How permissions work\n\n"
            "- **LOW risk** (e.g. `filesystem.read`, `network.read`): Auto-granted on first use.\n"
            "- **MEDIUM risk** (e.g. `filesystem.write`, `network.send`): Granted on first use, logged.\n"
            "- **HIGH risk** (e.g. `filesystem.delete`, `os.process.kill`): Requires explicit approval.\n"
            "- **CRITICAL risk** (e.g. `data.credentials`, `os.admin`): Requires explicit approval.\n\n"
            "### Managing permissions via IML\n\n"
            "Use the `security` module to query and manage permissions:\n\n"
            "- `security.list_permissions` — List all granted permissions\n"
            "- `security.check_permission` — Check if a permission is granted\n"
            "- `security.request_permission` — Request a new permission\n"
            "- `security.revoke_permission` — Revoke a permission\n"
            "- `security.get_security_status` — Security overview\n\n"
            "### If a permission is denied\n\n"
            "When an action fails with `PermissionNotGrantedError`, use "
            "`security.request_permission` to request the missing permission, "
            "then retry the original action.\n"
        )

    def _build_intent_verifier_section(self) -> str:
        """Tell the LLM about the active security analysis layer."""
        if not self._intent_verifier_active:
            return ""
        return (
            "## Intent Verification (Security Layer)\n\n"
            "All plans you generate are analysed by a dedicated security LLM before "
            "execution. Plans containing prompt injection, privilege escalation, data "
            "exfiltration, or intent misalignment will be **rejected**.\n\n"
            "### Implications for your plans\n\n"
            "- Keep plan descriptions accurate and specific.\n"
            "- Do not embed instructions in parameters.\n"
            "- Avoid overly broad plans — split complex tasks into focused steps.\n"
            "- Use `requires_approval: true` for sensitive operations.\n"
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
