"""AppCompiler — Parses .app.yaml files into validated AppDefinition models.

The compiler performs:
1. YAML parsing (with safe_load)
2. Schema validation (via Pydantic)
3. Semantic validation (cross-field consistency checks)
4. Module/action existence validation (when module_info provided)
5. Agent ID cross-reference validation in flow steps
6. Expression syntax pre-validation (filter names, bracket matching)
7. Macro reference validation
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from .models import (
    AgentConfig,
    AppDefinition,
    FlowStep,
    MacroDefinition,
    MultiAgentConfig,
)

logger = logging.getLogger(__name__)


class CompilationError(Exception):
    """Raised when a .app.yaml file fails compilation."""

    def __init__(self, message: str, errors: list[str] | None = None):
        super().__init__(message)
        self.errors = errors or []


# Known expression filters (from expression.py _FILTERS registry)
_KNOWN_FILTERS = {
    "upper", "lower", "trim", "first", "last", "count", "join",
    "default", "required", "json", "parse_json", "matches", "replace",
    "split", "truncate", "slice", "sort", "unique", "filter", "map",
    "basename", "dirname", "startswith", "endswith", "round", "abs",
    "descriptions",
}

# Regex to find template expressions: {{...}}
_EXPR_PATTERN = re.compile(r"\{\{(.*?)\}\}", re.DOTALL)

# Regex to find filter usage: value | filter_name or value | filter_name(args)
_FILTER_USAGE = re.compile(r"\|\s*([a-zA-Z_]\w*)")


class AppCompiler:
    """Compiles .app.yaml files into validated AppDefinition models.

    Args:
        module_info: Optional dict of module_id -> {"actions": [...]} from
            the module registry. When provided, the compiler validates that
            tools reference existing modules and actions.
    """

    def __init__(self, module_info: dict[str, dict] | None = None):
        self._module_info = module_info or {}

    def compile_file(self, path: str | Path) -> AppDefinition:
        """Load and compile a .app.yaml file."""
        path = Path(path)
        if not path.exists():
            raise CompilationError(f"File not found: {path}")
        if not path.suffix in (".yaml", ".yml"):
            raise CompilationError(f"Expected .yaml or .yml file, got: {path.suffix}")
        text = path.read_text(encoding="utf-8")
        return self.compile_string(text, source=str(path))

    def compile_string(self, yaml_text: str, source: str = "<string>") -> AppDefinition:
        """Parse and compile a YAML string."""
        # Step 1: Parse YAML
        try:
            raw = yaml.safe_load(yaml_text)
        except yaml.YAMLError as e:
            raise CompilationError(f"YAML parse error in {source}: {e}") from e

        if not isinstance(raw, dict):
            raise CompilationError(
                f"Expected a YAML mapping at top level in {source}, got {type(raw).__name__}"
            )

        return self.compile_dict(raw, source=source)

    def compile_dict(self, data: dict[str, Any], source: str = "<dict>") -> AppDefinition:
        """Compile a raw dict (already parsed from YAML) into AppDefinition."""
        # Step 1: Normalize the data
        data = self._normalize(data)

        # Step 2: Pydantic validation
        try:
            app_def = AppDefinition.model_validate(data)
        except ValidationError as e:
            errors = [f"  - {err['msg']} at {'.'.join(str(x) for x in err['loc'])}"
                      for err in e.errors()]
            raise CompilationError(
                f"Schema validation failed in {source}:\n" + "\n".join(errors),
                errors=errors,
            ) from e

        # Step 3: Semantic validation
        self._validate_semantics(app_def, source)

        # Step 4: Validate macro references in flow (no compile-time expansion;
        # macros are executed at runtime by FlowExecutor._exec_use_macro)
        if app_def.macros and app_def.flow:
            macro_names = {m.name for m in app_def.macros}
            macro_lookup = {m.name: m for m in app_def.macros}
            self._validate_macro_refs(app_def.flow, macro_names, source, macro_lookup)

        # Step 5: Validate module/action existence (when module_info provided)
        if self._module_info:
            self._validate_modules(app_def, source)

        # Step 6: Validate agent ID cross-references in flow steps
        self._validate_agent_refs(app_def, source)

        # Step 7: Expression syntax pre-validation (warnings for unknown filters)
        self._validate_expressions(app_def, source)

        # Step 8: Validate result references in flow
        if app_def.flow:
            self._validate_result_refs(app_def, source)

        # Step 9: Validate variable references (warning for undefined)
        self._validate_variable_refs(app_def, source)

        # Step 10: Validate flow action params against module schema
        if self._module_info and app_def.flow:
            self._validate_action_params(app_def.flow, source)

        # Step 10b: Validate macro body action params against module schema
        if self._module_info and app_def.macros:
            for macro in app_def.macros:
                if macro.body:
                    self._validate_action_params(
                        macro.body, source, context=f"macro '{macro.name}'",
                    )

        # Step 11: Validate approval_required module/action (with module_info)
        if self._module_info and app_def.capabilities:
            self._validate_approval_refs(app_def.capabilities, source)

        # Step 12: Validate brain provider
        self._validate_brain_providers(app_def, source)

        # Step 12b: Validate brain params against provider capabilities
        self._validate_brain_params(app_def, source)

        # Step 13: Validate security profile consistency with tools
        self._validate_security_profile(app_def, source)

        # Step 14: Validate P2P/blackboard communication requirements
        self._validate_communication_mode(app_def, source)

        # Step 15: Validate observability config (metrics track expressions)
        self._validate_observability(app_def, source)

        return app_def

    def _normalize(self, data: dict[str, Any]) -> dict[str, Any]:
        """Normalize raw YAML data before Pydantic validation."""
        # Handle `agents:` as a list (shorthand) vs full config
        if "agents" in data and isinstance(data["agents"], list):
            # If it's a list of agent dicts, wrap in MultiAgentConfig
            agents_list = data.pop("agents")
            data["agents"] = {"agents": agents_list}

        # If no agent and no agents, create a default agent
        if "agent" not in data and "agents" not in data:
            data["agent"] = {}

        # Ensure triggers is a list
        if "triggers" not in data:
            data["triggers"] = []

        # Handle tools defined at top level — merge into agent if single agent
        # (kept as-is, merged at runtime by AppDefinition.get_all_tools)

        return data

    def _validate_semantics(self, app_def: AppDefinition, source: str) -> None:
        """Validate cross-field semantic constraints."""
        errors: list[str] = []

        # 1. Cannot have both agent and agents
        if app_def.agent is not None and app_def.agents is not None:
            errors.append("Cannot define both 'agent' and 'agents' blocks")

        # 2. Multi-agent must have at least one agent
        if app_def.agents is not None and len(app_def.agents.agents) == 0:
            errors.append("'agents' block must contain at least one agent")

        # 3. Multi-agent agents must have unique IDs
        if app_def.agents:
            ids = [a.id for a in app_def.agents.agents if a.id]
            if len(ids) != len(set(ids)):
                errors.append("Agent IDs must be unique within 'agents' block")

        # 4. Flow step IDs must be unique (if flow is defined)
        if app_def.flow:
            self._check_step_ids(app_def.flow, errors)
            # 4b. Validate goto targets reference existing step IDs
            self._validate_goto_targets(app_def.flow, errors)

        # 5. Macro names must be unique
        if app_def.macros:
            names = [m.name for m in app_def.macros]
            if len(names) != len(set(names)):
                errors.append("Macro names must be unique")

        # 6. Trigger IDs must be unique
        if app_def.triggers:
            trigger_ids = [t.id for t in app_def.triggers if t.id]
            if len(trigger_ids) != len(set(trigger_ids)):
                errors.append("Trigger IDs must be unique")

        # 7. Tools must reference modules or builtins, not both
        for tool in app_def.get_all_tools():
            if tool.module and tool.builtin:
                errors.append(
                    f"Tool cannot have both 'module' and 'builtin': "
                    f"module={tool.module}, builtin={tool.builtin}"
                )
            if not tool.module and not tool.builtin:
                if not tool.id:
                    errors.append("Tool must have 'module', 'builtin', or 'id'")

        # 8. Validate flow step action format (must be "module.action")
        if app_def.flow:
            self._validate_flow_actions(app_def.flow, errors)

        # 9. Validate trigger required fields based on type
        self._validate_triggers(app_def.triggers or [], errors)

        # 10. Validate duration/timeout strings
        self._validate_duration_strings(app_def, errors)

        # 11. Validate flow step on_error values
        if app_def.flow:
            self._validate_on_error(app_def.flow, errors)

        # 12. Validate flow step polymorphism (only one type per step)
        if app_def.flow:
            self._validate_step_polymorphism(app_def.flow, errors)

        # 13. Multi-agent structural requirements
        self._validate_multi_agent_structure(app_def, errors)

        # 14. Flow step completeness (agent needs input, branch needs cases, etc.)
        if app_def.flow:
            self._validate_flow_completeness(app_def.flow, errors)

        # 15. Macro structure (non-empty body, param usage)
        if app_def.macros:
            self._validate_macro_structure(app_def.macros, errors)

        # 16. Duplicate tool module declarations (warning)
        seen_modules: dict[str, int] = {}
        for tool in app_def.get_all_tools():
            if tool.module:
                seen_modules[tool.module] = seen_modules.get(tool.module, 0) + 1
        for mod, count in seen_modules.items():
            if count > 1:
                logger.warning(
                    "Module '%s' is declared %d times in tools — "
                    "this may cause duplicate tool registrations",
                    mod, count,
                )

        # 17. Perception action keys format (must be module.action)
        if app_def.perception and app_def.perception.actions:
            for key in app_def.perception.actions:
                if "." not in key:
                    errors.append(
                        f"perception.actions key '{key}' must be in 'module.action' "
                        f"format (e.g. 'filesystem.read_file')"
                    )

        # 18. Trigger deep validation (HTTP paths, transform syntax, schedule quality)
        if app_def.triggers:
            self._validate_triggers_deep(app_def.triggers, errors)

        if errors:
            raise CompilationError(
                f"Semantic validation failed in {source}:\n"
                + "\n".join(f"  - {e}" for e in errors),
                errors=errors,
            )

    def _check_step_ids(self, steps: list[FlowStep], errors: list[str]) -> None:
        """Recursively check for duplicate step IDs."""
        seen: set[str] = set()

        def _walk(step_list: list[FlowStep]) -> None:
            for step in step_list:
                if step.id:
                    if step.id in seen:
                        errors.append(f"Duplicate flow step ID: '{step.id}'")
                    seen.add(step.id)
                # Recurse into nested steps
                if step.sequence:
                    _walk(step.sequence)
                if step.parallel:
                    _walk(step.parallel.steps)
                if step.branch:
                    for case_steps in step.branch.cases.values():
                        _walk(case_steps)
                    if step.branch.default:
                        _walk(step.branch.default)
                if step.loop:
                    _walk(step.loop.body)
                if step.map:
                    _walk(step.map.step)
                if step.pipe:
                    _walk(step.pipe)
                if step.race:
                    _walk(step.race.steps)
                if step.try_steps:
                    _walk(step.try_steps)
                if step.finally_steps:
                    _walk(step.finally_steps)

        _walk(steps)

    def _validate_goto_targets(self, steps: list[FlowStep], errors: list[str]) -> None:
        """Validate that goto targets reference existing top-level step IDs.

        Only validates static goto targets (not template expressions like {{result.x}}).
        """
        # Collect all top-level step IDs
        top_ids: set[str] = set()
        for s in steps:
            if s.id:
                top_ids.add(s.id)

        # Find all goto references (only static ones — skip templates)
        def _walk(step_list: list[FlowStep]) -> None:
            for step in step_list:
                if step.goto and not step.goto.startswith("{{"):
                    if step.goto not in top_ids:
                        errors.append(
                            f"goto target '{step.goto}' not found in top-level flow steps"
                            f" (referenced in step '{step.id or '<anonymous>'}')"
                        )
                if step.sequence:
                    _walk(step.sequence)
                if step.parallel:
                    _walk(step.parallel.steps)
                if step.branch:
                    for case_steps in step.branch.cases.values():
                        _walk(case_steps)
                    if step.branch.default:
                        _walk(step.branch.default)
                if step.loop:
                    _walk(step.loop.body)
                if step.pipe:
                    _walk(step.pipe)
                if step.try_steps:
                    _walk(step.try_steps)
                if step.finally_steps:
                    _walk(step.finally_steps)

        _walk(steps)

    def _validate_macro_refs(
        self,
        steps: list[FlowStep],
        macro_names: set[str],
        source: str,
        macro_lookup: dict[str, Any] | None = None,
    ) -> None:
        """Validate that all macro references in flow steps refer to defined macros.

        When ``macro_lookup`` is provided (name → MacroDefinition), also validates
        that required params are supplied and no unknown params are passed.
        """
        errors: list[str] = []

        def _walk(step_list: list[FlowStep]) -> None:
            for step in step_list:
                step_label = step.id or "<anonymous>"
                if step.use and step.use not in macro_names:
                    errors.append(f"Unknown macro '{step.use}' referenced in flow step '{step_label}'")
                elif step.use and macro_lookup and step.use in macro_lookup:
                    macro_def = macro_lookup[step.use]
                    provided = set(step.with_params.keys())
                    # Check required params
                    for pname, pdef in macro_def.params.items():
                        if pdef.required and pdef.default is None and pname not in provided:
                            # Allow if with_params has template expressions
                            errors.append(
                                f"Macro call '{step.use}' in step '{step_label}' "
                                f"is missing required param '{pname}'"
                            )
                    # Check unknown params
                    if macro_def.params:
                        for pname in provided:
                            if pname not in macro_def.params:
                                errors.append(
                                    f"Macro call '{step.use}' in step '{step_label}' "
                                    f"passes unknown param '{pname}'. "
                                    f"Valid params: {sorted(macro_def.params.keys())}"
                                )
                # Recurse into nested steps
                if step.sequence:
                    _walk(step.sequence)
                if step.parallel:
                    _walk(step.parallel.steps)
                if step.branch:
                    for case_steps in step.branch.cases.values():
                        _walk(case_steps)
                    if step.branch.default:
                        _walk(step.branch.default)
                if step.loop:
                    _walk(step.loop.body)
                if step.map:
                    _walk(step.map.step)
                if step.pipe:
                    _walk(step.pipe)
                if step.race:
                    _walk(step.race.steps)
                if step.try_steps:
                    _walk(step.try_steps)
                if step.finally_steps:
                    _walk(step.finally_steps)

        _walk(steps)

        if errors:
            raise CompilationError(
                f"Macro reference errors in {source}:\n" + "\n".join(f"  - {e}" for e in errors),
                errors=errors,
            )

    # ── Module/action existence validation ──────────────────────────

    def _validate_modules(self, app_def: AppDefinition, source: str) -> None:
        """Validate that tool declarations reference existing modules and actions.

        Only runs when module_info was provided at compiler init (e.g., from the
        daemon's module registry).
        """
        errors: list[str] = []
        available = self._module_info

        for tool in app_def.get_all_tools():
            if not tool.module:
                continue  # builtin, skip

            if tool.module not in available:
                errors.append(
                    f"Unknown module '{tool.module}' in tool declaration. "
                    f"Available modules: {sorted(available.keys())}"
                )
                continue

            mod_info = available[tool.module]
            mod_actions = {a["name"] for a in mod_info.get("actions", [])}

            # Validate single action
            if tool.action and tool.action not in mod_actions:
                errors.append(
                    f"Unknown action '{tool.action}' in module '{tool.module}'. "
                    f"Available: {sorted(mod_actions)}"
                )

            # Validate actions list
            if tool.actions:
                for action_name in tool.actions:
                    if action_name not in mod_actions:
                        errors.append(
                            f"Unknown action '{action_name}' in module '{tool.module}'. "
                            f"Available: {sorted(mod_actions)}"
                        )

            # Validate exclude list
            if tool.exclude:
                for action_name in tool.exclude:
                    if action_name not in mod_actions:
                        errors.append(
                            f"Excluded action '{action_name}' does not exist in module '{tool.module}'. "
                            f"Available: {sorted(mod_actions)}"
                        )

        # Collect declared tool modules for cross-referencing
        declared_modules = {t.module for t in app_def.get_all_tools() if t.module}

        # Validate capabilities.grant modules and actions
        if app_def.capabilities:
            for i, grant in enumerate(app_def.capabilities.grant or []):
                if not grant.module:
                    continue
                if grant.module not in available:
                    errors.append(
                        f"Unknown module '{grant.module}' in capabilities.grant[{i}]. "
                        f"Available modules: {sorted(available.keys())}"
                    )
                else:
                    # Validate grant actions against module's real actions
                    if grant.actions:
                        mod_actions = {a["name"] for a in available[grant.module].get("actions", [])}
                        for action_name in grant.actions:
                            if action_name not in mod_actions:
                                errors.append(
                                    f"Unknown action '{action_name}' in capabilities.grant[{i}] "
                                    f"for module '{grant.module}'. "
                                    f"Available: {sorted(mod_actions)}"
                                )
                # Warn if grant module not in declared tools
                if grant.module and grant.module in available and grant.module not in declared_modules:
                    logger.warning(
                        "capabilities.grant[%d] references module '%s' which is not "
                        "declared in agent tools — grant has no effect without the tool",
                        i, grant.module,
                    )

            for i, deny in enumerate(app_def.capabilities.deny or []):
                if not deny.module:
                    continue
                if deny.module not in available:
                    errors.append(
                        f"Unknown module '{deny.module}' in capabilities.deny[{i}]. "
                        f"Available modules: {sorted(available.keys())}"
                    )
                elif deny.action:
                    # Validate deny action against module's real actions
                    mod_actions = {a["name"] for a in available[deny.module].get("actions", [])}
                    if deny.action not in mod_actions:
                        errors.append(
                            f"Unknown action '{deny.action}' in capabilities.deny[{i}] "
                            f"for module '{deny.module}'. "
                            f"Available: {sorted(mod_actions)}"
                        )

        # Validate flow step actions reference existing modules/actions
        if app_def.flow:
            self._validate_flow_action_existence(app_def.flow, available, errors)

        # Validate macro body step actions reference existing modules/actions
        if app_def.macros:
            for macro in app_def.macros:
                if macro.body:
                    self._validate_flow_action_existence(
                        macro.body, available, errors,
                        context=f"macro '{macro.name}'",
                    )

        # Validate module_config keys reference existing modules
        if app_def.module_config:
            for mod_id in app_def.module_config:
                if mod_id not in available:
                    errors.append(
                        f"module_config references unknown module '{mod_id}'. "
                        f"Available modules: {sorted(available.keys())}"
                    )

        # Validate perception.actions keys reference existing modules/actions
        if app_def.perception and app_def.perception.actions:
            for key in app_def.perception.actions:
                if "." in key:
                    mod_id, action_name = key.split(".", 1)
                    if mod_id not in available:
                        errors.append(
                            f"perception.actions key '{key}' references unknown "
                            f"module '{mod_id}'. Available: {sorted(available.keys())}"
                        )
                    else:
                        mod_actions = {a["name"] for a in available[mod_id].get("actions", [])}
                        if action_name not in mod_actions:
                            errors.append(
                                f"perception.actions key '{key}' references unknown "
                                f"action '{action_name}' in module '{mod_id}'. "
                                f"Available: {sorted(mod_actions)}"
                            )

        if errors:
            raise CompilationError(
                f"Module/action validation failed in {source}:\n"
                + "\n".join(f"  - {e}" for e in errors),
                errors=errors,
            )

    # ── Flow action existence validation ─────────────────────────────

    def _validate_flow_action_existence(
        self,
        steps: list[FlowStep],
        available: dict[str, dict],
        errors: list[str],
        context: str = "flow",
    ) -> None:
        """Validate that flow step actions reference existing modules and actions."""

        def _walk(step_list: list[FlowStep]) -> None:
            for step in step_list:
                if step.action and "." in step.action and not step.action.startswith("{{"):
                    mod_id, action_name = step.action.split(".", 1)
                    if mod_id not in available:
                        errors.append(
                            f"Step '{step.id or '<anonymous>'}' in {context} references "
                            f"unknown module '{mod_id}' in action '{step.action}'. "
                            f"Available modules: {sorted(available.keys())}"
                        )
                    else:
                        mod_actions = {a["name"] for a in available[mod_id].get("actions", [])}
                        if action_name not in mod_actions:
                            errors.append(
                                f"Step '{step.id or '<anonymous>'}' in {context} references "
                                f"unknown action '{action_name}' in module '{mod_id}'. "
                                f"Available: {sorted(mod_actions)}"
                            )
                # Recurse
                if step.sequence:
                    _walk(step.sequence)
                if step.parallel:
                    _walk(step.parallel.steps)
                if step.branch:
                    for case_steps in step.branch.cases.values():
                        _walk(case_steps)
                    if step.branch.default:
                        _walk(step.branch.default)
                if step.loop:
                    _walk(step.loop.body)
                if step.map:
                    _walk(step.map.step)
                if step.pipe:
                    _walk(step.pipe)
                if step.race:
                    _walk(step.race.steps)
                if step.try_steps:
                    _walk(step.try_steps)
                if step.finally_steps:
                    _walk(step.finally_steps)

        _walk(steps)

    # ── Agent ID cross-reference validation ─────────────────────────

    def _validate_agent_refs(self, app_def: AppDefinition, source: str) -> None:
        """Validate that flow steps reference existing agent IDs."""
        if not app_def.flow and not app_def.macros:
            return

        # Collect valid agent IDs
        valid_ids: set[str] = {"default", ""}
        if app_def.agent and app_def.agent.id:
            valid_ids.add(app_def.agent.id)
        if app_def.agents:
            for a in app_def.agents.agents:
                if a.id:
                    valid_ids.add(a.id)

        errors: list[str] = []

        def _walk(steps: list[FlowStep]) -> None:
            for step in steps:
                if step.agent and step.agent not in valid_ids:
                    # Skip template expressions
                    if not step.agent.startswith("{{"):
                        errors.append(
                            f"Flow step '{step.id or '<anonymous>'}' references unknown "
                            f"agent '{step.agent}'. Available: {sorted(valid_ids - {''})}"
                        )
                # Recurse
                if step.sequence:
                    _walk(step.sequence)
                if step.parallel:
                    _walk(step.parallel.steps)
                if step.branch:
                    for case_steps in step.branch.cases.values():
                        _walk(case_steps)
                    if step.branch.default:
                        _walk(step.branch.default)
                if step.loop:
                    _walk(step.loop.body)
                if step.map:
                    _walk(step.map.step)
                if step.pipe:
                    _walk(step.pipe)
                if step.race:
                    _walk(step.race.steps)
                if step.try_steps:
                    _walk(step.try_steps)
                if step.finally_steps:
                    _walk(step.finally_steps)

        if app_def.flow:
            _walk(app_def.flow)

        # Also check macro bodies
        if app_def.macros:
            for macro in app_def.macros:
                _walk(macro.body)

        if errors:
            raise CompilationError(
                f"Agent reference errors in {source}:\n"
                + "\n".join(f"  - {e}" for e in errors),
                errors=errors,
            )

    # ── Flow step polymorphism validation ─────────────────────────

    # Fields that define a step type (only ONE should be set per step)
    # Primary step-type fields (only ONE should be set per step).
    # Note: 'goto' is a modifier (jump after step completes), not a primary type,
    # so it can coexist with action/agent/try/emit/etc.
    _STEP_TYPE_FIELDS = [
        ("action", "action"),
        ("agent", "agent"),
        ("sequence", "sequence"),
        ("parallel", "parallel"),
        ("branch", "branch"),
        ("loop", "loop"),
        ("map", "map"),
        ("reduce", "reduce"),
        ("race", "race"),
        ("pipe", "pipe"),
        ("spawn", "spawn"),
        ("approval", "approval"),
        ("try_steps", "try"),
        ("dispatch", "dispatch"),
        ("emit", "emit"),
        ("wait", "wait"),
        ("end", "end"),
        ("use", "use"),
        # 'goto' excluded — it's a modifier, not a primary type
    ]

    def _validate_step_polymorphism(
        self, steps: list[FlowStep], errors: list[str]
    ) -> None:
        """Validate that each flow step has exactly one step-type field set."""

        def _is_set(step: FlowStep, attr: str) -> bool:
            val = getattr(step, attr, None)
            if val is None:
                return False
            if isinstance(val, str) and val == "":
                return False
            if isinstance(val, list) and len(val) == 0:
                return False
            return True

        def _walk(step_list: list[FlowStep]) -> None:
            for step in step_list:
                active = [
                    label for attr, label in self._STEP_TYPE_FIELDS
                    if _is_set(step, attr)
                ]
                if len(active) > 1:
                    errors.append(
                        f"Flow step '{step.id or '<anonymous>'}' has multiple types: "
                        f"{', '.join(active)}. Each step must be exactly one type."
                    )
                # Recurse
                if step.sequence:
                    _walk(step.sequence)
                if step.parallel:
                    _walk(step.parallel.steps)
                if step.branch:
                    for case_steps in step.branch.cases.values():
                        _walk(case_steps)
                    if step.branch.default:
                        _walk(step.branch.default)
                if step.loop:
                    _walk(step.loop.body)
                if step.map:
                    _walk(step.map.step)
                if step.pipe:
                    _walk(step.pipe)
                if step.race:
                    _walk(step.race.steps)
                if step.try_steps:
                    _walk(step.try_steps)
                if step.finally_steps:
                    _walk(step.finally_steps)

        _walk(steps)

    # ── Multi-agent structure validation ────────────────────────────

    def _validate_multi_agent_structure(
        self, app_def: AppDefinition, errors: list[str]
    ) -> None:
        """Validate multi-agent structural requirements."""
        if not app_def.agents:
            return

        # Multi-agent apps typically need a flow — warn but don't error,
        # since agents can be spawned dynamically via agent_spawn module
        if not app_def.flow:
            logger.warning(
                "Multi-agent app '%s' has no 'flow:' block — agents will only "
                "be usable via agent_spawn or reactive mode",
                app_def.app.name,
            )

        # Every agent in a multi-agent app must have an ID
        for i, agent in enumerate(app_def.agents.agents):
            if not agent.id:
                errors.append(
                    f"Agent at index {i} in 'agents:' must have an 'id' field "
                    f"so it can be referenced in flow steps"
                )

    # ── Flow step completeness validation ───────────────────────────

    def _validate_flow_completeness(
        self, steps: list[FlowStep], errors: list[str]
    ) -> None:
        """Validate that flow steps have the required fields for their type."""

        def _walk(step_list: list[FlowStep]) -> None:
            for step in step_list:
                step_type = step.infer_type()

                # Agent steps need input
                if step_type.value == "agent" and not step.input:
                    errors.append(
                        f"Flow step '{step.id or '<anonymous>'}' references agent "
                        f"'{step.agent}' but has no 'input:' field"
                    )

                # Branch steps need cases or default
                if step.branch:
                    if not step.branch.cases and not step.branch.default:
                        errors.append(
                            f"Flow step '{step.id or '<anonymous>'}' has a 'branch:' "
                            f"with no 'cases:' and no 'default:'"
                        )

                # Parallel steps need at least one sub-step
                if step.parallel and len(step.parallel.steps) == 0:
                    errors.append(
                        f"Flow step '{step.id or '<anonymous>'}' has 'parallel:' "
                        f"with no steps"
                    )

                # Race steps need at least 2 sub-steps
                if step.race and len(step.race.steps) < 2:
                    errors.append(
                        f"Flow step '{step.id or '<anonymous>'}' has 'race:' "
                        f"with fewer than 2 steps (need at least 2 to race)"
                    )

                # Recurse
                if step.sequence:
                    _walk(step.sequence)
                if step.parallel:
                    _walk(step.parallel.steps)
                if step.branch:
                    for case_steps in step.branch.cases.values():
                        _walk(case_steps)
                    if step.branch.default:
                        _walk(step.branch.default)
                if step.loop:
                    _walk(step.loop.body)
                if step.map:
                    _walk(step.map.step)
                if step.pipe:
                    _walk(step.pipe)
                if step.race:
                    _walk(step.race.steps)
                if step.try_steps:
                    _walk(step.try_steps)
                if step.finally_steps:
                    _walk(step.finally_steps)

        _walk(steps)

    # ── Macro structure validation ──────────────────────────────────

    def _validate_macro_structure(
        self, macros: list[MacroDefinition], errors: list[str]
    ) -> None:
        """Validate macro structural requirements."""
        for macro in macros:
            # Macro must have a non-empty body
            if not macro.body:
                errors.append(
                    f"Macro '{macro.name}' has an empty body"
                )

            # Warn about declared params that are never referenced in body
            if macro.params and macro.body:
                body_text = str([s.model_dump() for s in macro.body])
                for param_name in macro.params:
                    ref = f"macro.{param_name}"
                    if ref not in body_text:
                        logger.warning(
                            "Macro '%s' declares param '%s' but never references "
                            "'{{macro.%s}}' in its body",
                            macro.name, param_name, param_name,
                        )

    # ── Flow action format validation ─────────────────────────────

    _DURATION_RE = re.compile(r"^\d+(\.\d+)?\s*(ms|s|m|h|d)$")
    _SIZE_RE = re.compile(r"^\d+(\.\d+)?\s*(B|KB|MB|GB|TB)$", re.IGNORECASE)
    _ON_ERROR_VALUES = {"fail", "skip", "continue", "rollback"}

    def _validate_flow_actions(self, steps: list[FlowStep], errors: list[str]) -> None:
        """Validate that action steps use the 'module.action' format."""

        def _walk(step_list: list[FlowStep]) -> None:
            for step in step_list:
                if step.action:
                    # Skip template expressions
                    if not step.action.startswith("{{"):
                        if "." not in step.action:
                            errors.append(
                                f"Flow step '{step.id or '<anonymous>'}' action '{step.action}' "
                                f"must be in 'module.action' format (e.g. 'filesystem.read_file')"
                            )
                # Recurse
                if step.sequence:
                    _walk(step.sequence)
                if step.parallel:
                    _walk(step.parallel.steps)
                if step.branch:
                    for case_steps in step.branch.cases.values():
                        _walk(case_steps)
                    if step.branch.default:
                        _walk(step.branch.default)
                if step.loop:
                    _walk(step.loop.body)
                if step.map:
                    _walk(step.map.step)
                if step.pipe:
                    _walk(step.pipe)
                if step.race:
                    _walk(step.race.steps)
                if step.try_steps:
                    _walk(step.try_steps)
                if step.finally_steps:
                    _walk(step.finally_steps)

        _walk(steps)

    # ── Trigger required fields validation ──────────────────────────

    def _validate_triggers(
        self, triggers: list, errors: list[str]
    ) -> None:
        """Validate trigger-type-specific required fields."""
        for i, trigger in enumerate(triggers):
            label = f"trigger[{i}] (type={trigger.type.value})"
            if trigger.type.value == "schedule":
                if not trigger.cron and not trigger.when:
                    errors.append(
                        f"{label}: schedule trigger requires 'cron' or 'when'"
                    )
                if trigger.cron:
                    self._validate_cron(trigger.cron, label, errors)
            elif trigger.type.value in ("http", "webhook"):
                if not trigger.path:
                    errors.append(
                        f"{label}: {trigger.type.value} trigger requires 'path'"
                    )
            elif trigger.type.value == "watch":
                if not trigger.paths:
                    errors.append(f"{label}: watch trigger requires 'paths'")
            elif trigger.type.value == "event":
                if not trigger.topic:
                    errors.append(f"{label}: event trigger requires 'topic'")

    @staticmethod
    def _validate_cron(cron: str, label: str, errors: list[str]) -> None:
        """Basic cron expression validation (5 or 6 fields)."""
        parts = cron.strip().split()
        if len(parts) not in (5, 6):
            errors.append(
                f"{label}: cron expression '{cron}' must have 5 or 6 fields, "
                f"got {len(parts)}"
            )

    def _validate_triggers_deep(
        self, triggers: list, errors: list[str]
    ) -> None:
        """Deep trigger validation — checks transform syntax, HTTP paths, schedule quality."""
        http_paths: set[str] = set()
        cli_modes_seen: set[str] = set()

        for i, trigger in enumerate(triggers):
            label = f"trigger[{i}] (type={trigger.type.value})"

            # Check for duplicate CLI triggers with the same mode
            if trigger.type.value == "cli":
                cli_mode = getattr(trigger, "mode", None) or "conversation"
                if isinstance(cli_mode, str):
                    cli_key = cli_mode
                else:
                    cli_key = cli_mode.value if hasattr(cli_mode, "value") else str(cli_mode)
                if cli_key in cli_modes_seen:
                    logger.warning(
                        "Multiple CLI triggers with mode '%s' defined — "
                        "only the first will be used",
                        cli_key,
                    )
                cli_modes_seen.add(cli_key)

            # HTTP/webhook: check for duplicate paths
            if trigger.type.value in ("http", "webhook") and trigger.path:
                key = f"{trigger.method}:{trigger.path}"
                if key in http_paths:
                    errors.append(
                        f"{label}: duplicate HTTP path '{trigger.method} {trigger.path}'"
                    )
                http_paths.add(key)

                # Path must start with /
                if not trigger.path.startswith("/"):
                    errors.append(
                        f"{label}: HTTP path must start with '/' — got '{trigger.path}'"
                    )

            # Transform: validate bracket matching in templates
            if trigger.transform:
                open_count = trigger.transform.count("{{")
                close_count = trigger.transform.count("}}")
                if open_count != close_count:
                    errors.append(
                        f"{label}: transform has mismatched template brackets "
                        f"({{ count={open_count}, }} count={close_count})"
                    )

            # Schedule: warn about very short intervals
            if trigger.type.value == "schedule":
                if trigger.when:
                    expr = trigger.when.strip().lower()
                    if expr.startswith("every "):
                        from llmos_bridge.apps.trigger_manager import _parse_duration
                        interval = _parse_duration(expr[6:].strip())
                        if 0 < interval < 10:
                            logger.warning(
                                "%s: schedule interval %ss is very short — "
                                "this may cause high resource usage",
                                label, interval,
                            )

            # Event: warn about broad topics
            if trigger.type.value == "event" and trigger.topic:
                if trigger.topic == "*" or trigger.topic == "#":
                    logger.warning(
                        "%s: subscribing to wildcard topic '%s' may cause "
                        "high event volume",
                        label, trigger.topic,
                    )

    # ── Duration string validation ──────────────────────────────────

    def _validate_duration_strings(
        self, app_def: AppDefinition, errors: list[str]
    ) -> None:
        """Validate that duration/timeout strings match expected format."""

        def _check(value: str, context: str) -> None:
            if not value or value.startswith("{{"):
                return
            if not self._DURATION_RE.match(value.strip()):
                errors.append(
                    f"Invalid duration '{value}' in {context}. "
                    f"Expected format: <number><unit> (e.g. '30s', '5m', '1h')"
                )

        # Flow step timeouts
        if app_def.flow:
            self._walk_flow_durations(app_def.flow, errors)

        # Agent-level timeouts (if the model has a timeout field)
        def _check_agent_timeouts(agent: Any, prefix: str) -> None:
            if hasattr(agent, "loop") and agent.loop:
                loop = agent.loop
                if hasattr(loop, "timeout") and loop.timeout:
                    _check(loop.timeout, f"{prefix}.loop.timeout")

        if app_def.agent:
            _check_agent_timeouts(app_def.agent, "agent")
        if app_def.agents:
            for a in app_def.agents.agents:
                _check_agent_timeouts(a, f"agent[{a.id}]")

        # Trigger debounce
        if app_def.triggers:
            for i, t in enumerate(app_def.triggers):
                if t.debounce:
                    _check(t.debounce, f"triggers[{i}].debounce")

        # Approval timeouts
        if app_def.capabilities and app_def.capabilities.approval_required:
            for j, ap in enumerate(app_def.capabilities.approval_required):
                if ap.timeout:
                    _check(ap.timeout, f"capabilities.approval_required[{j}].timeout")

        # Tool constraints durations and sizes
        def _check_size(value: str, context: str) -> None:
            if not value or value.startswith("{{"):
                return
            if not self._SIZE_RE.match(value.strip()):
                errors.append(
                    f"Invalid size '{value}' in {context}. "
                    f"Expected format: <number><unit> (e.g. '50MB', '1GB')"
                )

        def _check_constraints(c: Any, prefix: str) -> None:
            if c.timeout:
                _check(c.timeout, f"{prefix}.timeout")
            if c.max_file_size:
                _check_size(c.max_file_size, f"{prefix}.max_file_size")
            if c.max_response_size:
                _check_size(c.max_response_size, f"{prefix}.max_response_size")

        for i, tool in enumerate(app_def.get_all_tools()):
            _check_constraints(
                tool.constraints,
                f"tools[{tool.module or tool.builtin or i}].constraints",
            )

        # Grant constraints
        if app_def.capabilities:
            for j, grant in enumerate(app_def.capabilities.grant or []):
                _check_constraints(
                    grant.constraints,
                    f"capabilities.grant[{j}].constraints",
                )

    def _walk_flow_durations(
        self, steps: list[FlowStep], errors: list[str], prefix: str = "flow"
    ) -> None:
        """Recursively validate duration strings in flow steps."""

        def _check(value: str, context: str) -> None:
            if not value or value.startswith("{{"):
                return
            if not self._DURATION_RE.match(value.strip()):
                errors.append(
                    f"Invalid duration '{value}' in {context}. "
                    f"Expected format: <number><unit> (e.g. '30s', '5m', '1h')"
                )

        for i, step in enumerate(steps):
            ctx = f"{prefix}[{step.id or i}]"
            if step.timeout:
                _check(step.timeout, f"{ctx}.timeout")
            if step.sequence:
                self._walk_flow_durations(step.sequence, errors, f"{ctx}.sequence")
            if step.parallel:
                self._walk_flow_durations(step.parallel.steps, errors, f"{ctx}.parallel")
            if step.branch:
                for k, case_steps in step.branch.cases.items():
                    self._walk_flow_durations(case_steps, errors, f"{ctx}.branch.{k}")
                if step.branch.default:
                    self._walk_flow_durations(step.branch.default, errors, f"{ctx}.branch.default")
            if step.loop:
                self._walk_flow_durations(step.loop.body, errors, f"{ctx}.loop")
            if step.pipe:
                self._walk_flow_durations(step.pipe, errors, f"{ctx}.pipe")
            if step.try_steps:
                self._walk_flow_durations(step.try_steps, errors, f"{ctx}.try")
            if step.finally_steps:
                self._walk_flow_durations(step.finally_steps, errors, f"{ctx}.finally")

    # ── on_error value validation ──────────────────────────────────

    def _validate_on_error(self, steps: list[FlowStep], errors: list[str]) -> None:
        """Validate that on_error values are recognized enum values."""

        def _walk(step_list: list[FlowStep]) -> None:
            for step in step_list:
                if step.on_error and not step.on_error.startswith("{{"):
                    if step.on_error not in self._ON_ERROR_VALUES:
                        errors.append(
                            f"Flow step '{step.id or '<anonymous>'}' has invalid on_error "
                            f"value '{step.on_error}'. Must be one of: "
                            f"{sorted(self._ON_ERROR_VALUES)}"
                        )
                if step.sequence:
                    _walk(step.sequence)
                if step.parallel:
                    _walk(step.parallel.steps)
                if step.branch:
                    for case_steps in step.branch.cases.values():
                        _walk(case_steps)
                    if step.branch.default:
                        _walk(step.branch.default)
                if step.loop:
                    _walk(step.loop.body)
                if step.pipe:
                    _walk(step.pipe)
                if step.try_steps:
                    _walk(step.try_steps)
                if step.finally_steps:
                    _walk(step.finally_steps)

        _walk(steps)

    # ── Expression syntax pre-validation ────────────────────────────

    def _validate_expressions(self, app_def: AppDefinition, source: str) -> None:
        """Pre-validate expression syntax: check for unknown filters, unmatched
        brackets, and common mistakes.

        Emits warnings (via logger) rather than errors for most issues, since
        expressions may contain dynamic content resolved at runtime.
        """
        warnings: list[str] = []

        def _check_expr(expr: str, context: str) -> None:
            """Check a single expression string for issues."""
            if not expr or "{{" not in expr:
                return

            # Check bracket matching
            open_count = expr.count("{{")
            close_count = expr.count("}}")
            if open_count != close_count:
                warnings.append(
                    f"Unmatched brackets in {context}: "
                    f"{open_count} '{{{{' vs {close_count} '}}}}'"
                )

            # Find all template blocks and check filters
            for match in _EXPR_PATTERN.finditer(expr):
                block = match.group(1)
                for filter_match in _FILTER_USAGE.finditer(block):
                    filter_name = filter_match.group(1)
                    # Skip logical operators and known keywords
                    if filter_name in {"and", "or", "not", "true", "false", "null",
                                       "in", "is", "if", "else"}:
                        continue
                    if filter_name not in _KNOWN_FILTERS:
                        warnings.append(
                            f"Unknown filter '|{filter_name}' in {context}. "
                            f"Known filters: {sorted(_KNOWN_FILTERS)}"
                        )

        def _check_all_strings(obj: Any, path: str = "") -> None:
            """Recursively walk the AppDefinition, checking all string fields."""
            if isinstance(obj, str):
                _check_expr(obj, path)
            elif isinstance(obj, list):
                for i, item in enumerate(obj):
                    _check_all_strings(item, f"{path}[{i}]")
            elif isinstance(obj, dict):
                for k, v in obj.items():
                    _check_all_strings(v, f"{path}.{k}")
            elif hasattr(obj, "model_dump"):
                # Pydantic model — dump to dict and check
                try:
                    d = obj.model_dump()
                    _check_all_strings(d, path)
                except Exception:
                    pass

        _check_all_strings(app_def, source)

        for w in warnings:
            logger.warning("expression_warning: %s", w)

    # ── Result reference validation ─────────────────────────────────

    _RESULT_REF_RE = re.compile(r"\{\{result\.(\w+)")

    def _validate_result_refs(self, app_def: AppDefinition, source: str) -> None:
        """Warn when {{result.step_id}} references a step ID that doesn't exist."""
        # Collect all step IDs (including nested)
        all_ids: set[str] = set()

        def _collect(steps: list[FlowStep]) -> None:
            for step in steps:
                if step.id:
                    all_ids.add(step.id)
                if step.sequence:
                    _collect(step.sequence)
                if step.parallel:
                    _collect(step.parallel.steps)
                if step.branch:
                    for case_steps in step.branch.cases.values():
                        _collect(case_steps)
                    if step.branch.default:
                        _collect(step.branch.default)
                if step.loop:
                    _collect(step.loop.body)
                if step.pipe:
                    _collect(step.pipe)
                if step.race:
                    _collect(step.race.steps)
                if step.try_steps:
                    _collect(step.try_steps)
                if step.finally_steps:
                    _collect(step.finally_steps)

        _collect(app_def.flow)

        # Also collect macro step IDs (macro bodies produce results too)
        if app_def.macros:
            for macro in app_def.macros:
                _collect(macro.body)

        # Scan all strings for {{result.X}} references
        def _scan(obj: Any) -> None:
            if isinstance(obj, str):
                for match in self._RESULT_REF_RE.finditer(obj):
                    ref_id = match.group(1)
                    if ref_id not in all_ids:
                        logger.warning(
                            "expression_warning: '{{result.%s}}' references "
                            "unknown step ID '%s' in %s. Known IDs: %s",
                            ref_id, ref_id, source, sorted(all_ids),
                        )
            elif isinstance(obj, list):
                for item in obj:
                    _scan(item)
            elif isinstance(obj, dict):
                for v in obj.values():
                    _scan(v)
            elif hasattr(obj, "model_dump"):
                try:
                    _scan(obj.model_dump())
                except Exception:
                    pass

        _scan(app_def)

    # ── Variable reference validation ───────────────────────────────

    # Known built-in namespaces that are NOT user-defined variables
    _BUILTIN_NAMESPACES = {
        # Core expression namespaces (from expression.py)
        "result", "trigger", "memory", "secret", "env", "agent",
        "run", "app", "loop", "context", "macro", "params",
        "workspace", "data_dir", "now",
        # Runtime-injected variables
        "input", "payload",
        # Event-triggered context
        "event",
        # Default iteration variables (map: item/index, reduce: item)
        "item", "index",
        # Logical operators (can appear at start of expression block)
        "not", "and", "or",
    }

    @staticmethod
    def _collect_iter_vars(app_def: AppDefinition) -> set[str]:
        """Collect iteration variable names from map/reduce steps."""
        names: set[str] = set()

        def _walk(steps: list[FlowStep] | None) -> None:
            if not steps:
                return
            for step in steps:
                if step.map:
                    names.add(step.map.as_var)
                    _walk(step.map.step)
                if step.reduce:
                    names.add(step.reduce.as_var)
                if step.sequence:
                    _walk(step.sequence)
                if step.parallel:
                    _walk(step.parallel.steps)
                if step.branch:
                    for case_steps in step.branch.cases.values():
                        _walk(case_steps)
                    if step.branch.default:
                        _walk(step.branch.default)
                if step.loop:
                    _walk(step.loop.body)
                if step.pipe:
                    _walk(step.pipe)
                if step.try_steps:
                    _walk(step.try_steps)
                if step.finally_steps:
                    _walk(step.finally_steps)

        _walk(app_def.flow)
        return names

    def _validate_variable_refs(self, app_def: AppDefinition, source: str) -> None:
        """Warn when template expressions reference undefined variables."""
        defined_vars = set(app_def.variables.keys())
        # Collect iteration variable names from map/reduce steps
        iter_vars = self._collect_iter_vars(app_def)
        all_valid = self._BUILTIN_NAMESPACES | defined_vars | iter_vars

        def _scan(obj: Any) -> None:
            if isinstance(obj, str) and "{{" in obj:
                for match in _EXPR_PATTERN.finditer(obj):
                    block = match.group(1).strip()
                    # Get the root name (before dots or filters)
                    root = block.split(".")[0].split("|")[0].split(" ")[0].strip()
                    if root and root not in all_valid and not root.startswith(("'", '"')):
                        # Skip numeric literals and booleans
                        if root in ("true", "false", "null", "none"):
                            continue
                        try:
                            float(root)
                            continue
                        except ValueError:
                            pass
                        logger.warning(
                            "expression_warning: '{{%s}}' references unknown "
                            "variable '%s' in %s. Defined variables: %s",
                            block, root, source, sorted(defined_vars) or "(none)",
                        )
            elif isinstance(obj, list):
                for item in obj:
                    _scan(item)
            elif isinstance(obj, dict):
                for v in obj.values():
                    _scan(v)
            elif hasattr(obj, "model_dump"):
                try:
                    _scan(obj.model_dump())
                except Exception:
                    pass

        _scan(app_def)

    # ── Flow action param validation ────────────────────────────────

    def _validate_action_params(
        self, steps: list[FlowStep], source: str, context: str = "flow",
    ) -> None:
        """Validate that flow step params match the module action's expected params.

        Only validates when module_info is provided and params are static
        (not template expressions).
        """
        errors: list[str] = []

        # Build lookup: (module, action) -> {param_name: {type, required, ...}}
        action_schemas: dict[tuple[str, str], dict[str, dict]] = {}
        for mod_id, mod_info in self._module_info.items():
            for action in mod_info.get("actions", []):
                action_schemas[(mod_id, action["name"])] = action.get("params", {})

        def _walk(step_list: list[FlowStep]) -> None:
            for step in step_list:
                if step.action and "." in step.action and not step.action.startswith("{{"):
                    parts = step.action.split(".", 1)
                    mod_id, action_name = parts[0], parts[1]
                    schema = action_schemas.get((mod_id, action_name))

                    if schema is not None and step.params:
                        # Check for unknown params
                        for param_name in step.params:
                            if param_name.startswith("_"):
                                continue  # internal params like _stream
                            if param_name not in schema:
                                errors.append(
                                    f"Step '{step.id or '<anonymous>'}' in {context} passes "
                                    f"unknown param '{param_name}' to {mod_id}.{action_name}. "
                                    f"Valid params: {sorted(schema.keys())}"
                                )

                        # Check for missing required params (only if no templates in params)
                        has_templates = any(
                            isinstance(v, str) and "{{" in v
                            for v in step.params.values()
                        )
                        if not has_templates:
                            for pname, pdef in schema.items():
                                if pdef.get("required") and pname not in step.params:
                                    if pdef.get("default") is None:
                                        errors.append(
                                            f"Step '{step.id or '<anonymous>'}' in {context} "
                                            f"is missing required param '{pname}' "
                                            f"for {mod_id}.{action_name}"
                                        )

                # Recurse
                if step.sequence:
                    _walk(step.sequence)
                if step.parallel:
                    _walk(step.parallel.steps)
                if step.branch:
                    for case_steps in step.branch.cases.values():
                        _walk(case_steps)
                    if step.branch.default:
                        _walk(step.branch.default)
                if step.loop:
                    _walk(step.loop.body)
                if step.pipe:
                    _walk(step.pipe)
                if step.try_steps:
                    _walk(step.try_steps)
                if step.finally_steps:
                    _walk(step.finally_steps)

        _walk(steps)

        if errors:
            raise CompilationError(
                f"Action param validation failed in {source}:\n"
                + "\n".join(f"  - {e}" for e in errors),
                errors=errors,
            )

    # ── Approval rule reference validation ──────────────────────────

    def _validate_approval_refs(
        self, capabilities: Any, source: str
    ) -> None:
        """Validate that approval_required rules reference existing modules/actions."""
        errors: list[str] = []
        available = self._module_info

        for i, rule in enumerate(capabilities.approval_required or []):
            if rule.module and rule.module not in available:
                errors.append(
                    f"capabilities.approval_required[{i}]: unknown module "
                    f"'{rule.module}'. Available: {sorted(available.keys())}"
                )
            elif rule.module and rule.action:
                mod_info = available.get(rule.module, {})
                mod_actions = {a["name"] for a in mod_info.get("actions", [])}
                if rule.action not in mod_actions:
                    errors.append(
                        f"capabilities.approval_required[{i}]: unknown action "
                        f"'{rule.action}' in module '{rule.module}'. "
                        f"Available: {sorted(mod_actions)}"
                    )

        if errors:
            raise CompilationError(
                f"Approval rule validation failed in {source}:\n"
                + "\n".join(f"  - {e}" for e in errors),
                errors=errors,
            )

    # ── Brain provider validation ───────────────────────────────────

    _KNOWN_PROVIDERS = {
        "anthropic", "openai", "ollama", "google", "bedrock", "vertex", "azure",
        "local", "test",
    }

    def _validate_brain_providers(self, app_def: AppDefinition, source: str) -> None:
        """Warn when brain provider values are not in the known set.

        Uses warnings (not errors) since custom providers can be registered.
        """
        def _check_brain(brain: Any, context: str) -> None:
            if brain and brain.provider:
                provider = brain.provider
                if provider.startswith("{{"):
                    return
                if provider not in self._KNOWN_PROVIDERS:
                    logger.warning(
                        "Unknown provider '%s' in %s (%s). "
                        "Known providers: %s",
                        provider, context, source,
                        sorted(self._KNOWN_PROVIDERS - {"test"}),
                    )

        if app_def.agent and app_def.agent.brain:
            _check_brain(app_def.agent.brain, "agent.brain")
        if app_def.agents:
            for a in app_def.agents.agents:
                if a.brain:
                    _check_brain(a.brain, f"agents[{a.id}].brain")

    # ── Brain param validation against provider capabilities ────────

    def _validate_brain_params(self, app_def: AppDefinition, source: str) -> None:
        """Validate brain params against known provider constraints.

        Catches at compile time:
        - Mutually exclusive params (e.g. temperature + top_p for Anthropic)
        - Fallback brains with no provider (logs info — they inherit at runtime)
        """
        from llmos_bridge.apps.providers import PROVIDER_CAPS

        errors: list[str] = []

        def _check_brain_config(brain: Any, context: str) -> None:
            if not brain:
                return
            provider = getattr(brain, "provider", None) or ""
            if not provider or provider.startswith("{{"):
                return

            caps = PROVIDER_CAPS.get(provider)
            if not caps:
                return

            # Collect which LLM params are explicitly set
            set_params: list[str] = []
            for param_name in caps.supported_params:
                val = getattr(brain, param_name, None)
                if val is not None:
                    set_params.append(param_name)

            # Check mutual exclusion
            for excl_set in caps.mutually_exclusive:
                present = [p for p in set_params if p in excl_set]
                if len(present) > 1:
                    errors.append(
                        f"{context}: provider '{provider}' does not allow "
                        f"{' and '.join(sorted(present))} together"
                    )

        def _check_fallback_provider(
            brain: Any, parent_provider: str, context: str,
        ) -> None:
            """Log info if a fallback has no provider — it will inherit at runtime."""
            if not brain:
                return
            for i, fb in enumerate(getattr(brain, "fallback", []) or []):
                fb_provider = getattr(fb, "provider", None)
                if not fb_provider:
                    logger.info(
                        "%s: fallback[%d] has no provider — "
                        "will inherit '%s' from parent at runtime",
                        context, i, parent_provider,
                    )

        if app_def.agent and app_def.agent.brain:
            brain = app_def.agent.brain
            _check_brain_config(brain, f"agent.brain ({source})")
            _check_fallback_provider(brain, brain.provider, f"agent.brain ({source})")

        if app_def.agents:
            for a in app_def.agents.agents:
                if a.brain:
                    _check_brain_config(a.brain, f"agents[{a.id}].brain ({source})")
                    _check_fallback_provider(
                        a.brain, a.brain.provider, f"agents[{a.id}].brain ({source})",
                    )

        if errors:
            raise CompilationError(
                f"Brain parameter validation failed in {source}:\n"
                + "\n".join(f"  - {e}" for e in errors),
                errors=errors,
            )

    # ── Security profile consistency validation ─────────────────────

    # Modules that perform write/destructive operations
    _WRITE_MODULES = {"os_exec", "database", "browser", "api_http", "agent_spawn"}

    def _validate_security_profile(
        self, app_def: AppDefinition, source: str
    ) -> None:
        """Warn when security profile is inconsistent with declared tools.

        For example, a 'readonly' profile should not declare write-capable tools.
        """
        if not app_def.security:
            return

        profile = app_def.security.profile.value

        if profile == "readonly":
            tool_modules = {t.module for t in app_def.get_all_tools() if t.module}
            write_tools = tool_modules & self._WRITE_MODULES
            if write_tools:
                logger.warning(
                    "Security profile 'readonly' in %s but app declares write-capable "
                    "tools: %s. These tools will be blocked at runtime.",
                    source, sorted(write_tools),
                )

    # ── P2P / blackboard communication mode validation ────────────

    def _validate_communication_mode(
        self, app_def: AppDefinition, source: str
    ) -> None:
        """Validate communication mode requirements."""
        if not app_def.agents:
            return
        from .models import CommunicationMode

        comm = app_def.agents.communication
        mode = comm.mode

        # P2P and blackboard require at least 2 agents
        if mode in (CommunicationMode.peer_to_peer, CommunicationMode.blackboard):
            if len(app_def.agents.agents) < 2:
                logger.warning(
                    "%s communication mode in %s requires at least 2 agents "
                    "(found %d). The single agent will run without peer interaction.",
                    mode.value, source, len(app_def.agents.agents),
                )

        # P2P agents should declare send_message builtin in tools for clarity
        if mode == CommunicationMode.peer_to_peer:
            for agent in app_def.agents.agents:
                has_send = any(
                    t.builtin == "send_message" for t in agent.tools
                )
                if not has_send:
                    logger.info(
                        "Agent '%s' in P2P mode has no explicit 'send_message' builtin "
                        "tool. It will be auto-injected at runtime.",
                        agent.id or "unnamed",
                    )

    # ── Observability / metrics validation ────────────────────────

    _VALID_METRIC_TYPES = {"counter", "gauge", "histogram"}
    _KNOWN_TRACK_PREFIXES = {
        "action.duration_ms", "action.count", "action.error",
        "action.success", "action.tokens",
    }

    def _validate_observability(
        self, app_def: AppDefinition, source: str
    ) -> None:
        """Validate observability config: metric types, track expressions."""
        obs = app_def.observability
        if not obs:
            return

        for i, metric in enumerate(obs.metrics):
            if metric.type not in self._VALID_METRIC_TYPES:
                logger.warning(
                    "metrics[%d].type='%s' in %s is not a known type. "
                    "Valid: %s",
                    i, metric.type, source, sorted(self._VALID_METRIC_TYPES),
                )
            if metric.track and not metric.track.startswith("{{"):
                # Check if it matches a known track expression pattern
                matched = any(metric.track.startswith(p) for p in self._KNOWN_TRACK_PREFIXES)
                if not matched:
                    logger.warning(
                        "metrics[%d].track='%s' in %s does not match known patterns. "
                        "Known: %s",
                        i, metric.track, source, sorted(self._KNOWN_TRACK_PREFIXES),
                    )
