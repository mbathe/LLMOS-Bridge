"""Extended expression engine for the LLMOS App Language.

Extends the base TemplateResolver with:
- Rich filter system (|upper, |first, |count, |join, |filter, |default, etc.)
- Dot-access into nested dicts/objects
- Optional chaining (obj?.field)
- Null coalescing (??)
- Comparison and logical operators
- Access to: result.*, trigger.*, memory.*, env.*, secret.*, agent.*, run.*, app.*
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any


# Template pattern: {{expression}}
_TEMPLATE_RE = re.compile(r"\{\{(.+?)\}\}")
# Strict single-template pattern (no }} allowed inside)
_SINGLE_TEMPLATE_RE = re.compile(r"\{\{([^}]+)\}\}")

# Filter separator: value | filter_name(args)
_FILTER_RE = re.compile(r"\s*\|\s*")


class ExpressionContext:
    """Context for resolving expressions during app execution."""

    def __init__(
        self,
        *,
        variables: dict[str, Any] | None = None,
        results: dict[str, Any] | None = None,
        trigger: dict[str, Any] | None = None,
        memory: dict[str, Any] | None = None,
        secrets: dict[str, str] | None = None,
        agent: dict[str, Any] | None = None,
        run: dict[str, Any] | None = None,
        app: dict[str, Any] | None = None,
        loop: dict[str, Any] | None = None,
        extra: dict[str, Any] | None = None,
    ):
        self.variables = variables or {}
        self.results = results or {}
        self.trigger = trigger or {}
        self.memory = memory or {}
        self.secrets = secrets or {}
        self.agent = agent or {}
        self.run = run or {}
        self.app = app or {}
        self.loop = loop or {}
        self.extra = extra or {}

    def get_namespace(self, name: str) -> Any:
        """Get a top-level namespace value."""
        namespaces = {
            "result": self.results,
            "trigger": self.trigger,
            "memory": self.memory,
            "secret": self.secrets,
            "env": os.environ,
            "agent": self.agent,
            "run": self.run,
            "app": self.app,
            "loop": self.loop,
            "context": self.extra,
            "workspace": self.variables.get("workspace", ""),
            "data_dir": self.variables.get("data_dir", ""),
            "now": __import__("time").time(),
        }
        # Check direct namespace
        if name in namespaces:
            return namespaces[name]
        # Check variables
        if name in self.variables:
            return self.variables[name]
        # Check extra context
        if name in self.extra:
            return self.extra[name]
        return None


class ExpressionEngine:
    """Resolves {{expressions}} in strings and data structures."""

    def resolve(self, value: Any, ctx: ExpressionContext) -> Any:
        """Resolve all templates in a value (string, dict, list, or primitive)."""
        if isinstance(value, str):
            return self._resolve_string(value, ctx)
        if isinstance(value, dict):
            return {k: self.resolve(v, ctx) for k, v in value.items()}
        if isinstance(value, list):
            return [self.resolve(item, ctx) for item in value]
        return value

    def evaluate_condition(self, expr: str, ctx: ExpressionContext) -> bool:
        """Evaluate an expression as a boolean condition."""
        result = self.resolve(expr, ctx)
        if isinstance(result, bool):
            return result
        if isinstance(result, str):
            lower = result.lower().strip()
            if lower in ("true", "1", "yes"):
                return True
            if lower in ("false", "0", "no", "none", "null", ""):
                return False
            return bool(result)
        return bool(result)

    def _resolve_string(self, text: str, ctx: ExpressionContext) -> Any:
        """Resolve templates in a string."""
        # Check if the entire string is a single template
        match = _SINGLE_TEMPLATE_RE.fullmatch(text.strip())
        if match:
            # Single expression — preserve type
            return self._evaluate_expression(match.group(1).strip(), ctx)

        # Multiple templates — string interpolation
        def _replace(m: re.Match) -> str:
            result = self._evaluate_expression(m.group(1).strip(), ctx)
            if result is None:
                return ""
            return str(result)

        return _TEMPLATE_RE.sub(_replace, text)

    def _evaluate_expression(self, expr: str, ctx: ExpressionContext) -> Any:
        """Evaluate a single expression (without {{ }})."""
        # Handle null coalescing: a ?? b
        if " ?? " in expr:
            parts = expr.split(" ?? ", 1)
            result = self._evaluate_expression(parts[0].strip(), ctx)
            if result is None:
                return self._evaluate_expression(parts[1].strip(), ctx)
            return result

        # Handle comparisons: a == b, a != b, a > b, etc.
        for op, func in [
            (" == ", lambda a, b: a == b),
            (" != ", lambda a, b: a != b),
            (" >= ", lambda a, b: _compare(a, b, ">=")),
            (" <= ", lambda a, b: _compare(a, b, "<=")),
            (" > ", lambda a, b: _compare(a, b, ">")),
            (" < ", lambda a, b: _compare(a, b, "<")),
        ]:
            if op in expr:
                left, right = expr.split(op, 1)
                left_val = self._evaluate_expression(left.strip(), ctx)
                right_val = self._evaluate_expression(right.strip(), ctx)
                return func(left_val, right_val)

        # Handle logical operators
        if " and " in expr:
            parts = expr.split(" and ", 1)
            return (
                self._evaluate_expression(parts[0].strip(), ctx)
                and self._evaluate_expression(parts[1].strip(), ctx)
            )
        if " or " in expr:
            parts = expr.split(" or ", 1)
            return (
                self._evaluate_expression(parts[0].strip(), ctx)
                or self._evaluate_expression(parts[1].strip(), ctx)
            )
        if expr.startswith("not "):
            return not self._evaluate_expression(expr[4:].strip(), ctx)

        # Split by filters
        parts = _FILTER_RE.split(expr)
        value = self._resolve_path(parts[0].strip(), ctx)

        # Apply filters
        for f in parts[1:]:
            value = self._apply_filter(value, f.strip(), ctx)

        return value

    def _resolve_path(self, path: str, ctx: ExpressionContext) -> Any:
        """Resolve a dotted path like 'result.step_id.field'."""
        # Handle string/number literals
        if path.startswith(("'", '"')) and path.endswith(("'", '"')):
            return path[1:-1]
        if path.isdigit():
            return int(path)
        try:
            return float(path)
        except ValueError:
            pass
        if path == "true":
            return True
        if path == "false":
            return False
        if path == "null" or path == "none":
            return None

        # Split by dots, handling optional chaining (?.)
        segments = _split_path(path)
        if not segments:
            return None

        # First segment is the namespace
        current = ctx.get_namespace(segments[0])
        if current is None and len(segments) == 1:
            return None

        for segment in segments[1:]:
            if current is None:
                return None
            current = _access(current, segment)

        return current

    def _apply_filter(self, value: Any, filter_expr: str, ctx: ExpressionContext) -> Any:
        """Apply a filter to a value."""
        # Parse filter name and args
        name, args = _parse_filter(filter_expr)
        return _FILTERS.get(name, _filter_identity)(value, args, ctx)


# ─── Path parsing ──────────────────────────────────────────────────────


def _split_path(path: str) -> list[str]:
    """Split a dotted path, respecting [] indexing and ?. chaining."""
    segments: list[str] = []
    current = ""
    i = 0
    while i < len(path):
        c = path[i]
        if c == ".":
            if current:
                segments.append(current)
                current = ""
        elif c == "?":
            # Optional chaining: skip the ? and the following .
            if current:
                segments.append(current)
                current = ""
            if i + 1 < len(path) and path[i + 1] == ".":
                i += 1  # skip the dot after ?
        elif c == "[":
            if current:
                segments.append(current)
                current = ""
            # Find closing bracket
            end = path.index("]", i + 1)
            segments.append(path[i + 1:end])
            i = end
        else:
            current += c
        i += 1
    if current:
        segments.append(current)
    return segments


def _access(obj: Any, key: str) -> Any:
    """Access a field/index on an object."""
    # Try integer index
    if key.isdigit():
        idx = int(key)
        if isinstance(obj, (list, tuple)) and idx < len(obj):
            return obj[idx]
        return None

    # Try dict/mapping access
    if isinstance(obj, Mapping):
        return obj.get(key)

    # Try attribute access
    return getattr(obj, key, None)


# ─── Filter functions ──────────────────────────────────────────────────


def _parse_filter(expr: str) -> tuple[str, list[str]]:
    """Parse 'filter_name(arg1, arg2)' into (name, [args])."""
    paren = expr.find("(")
    if paren == -1:
        return expr, []
    name = expr[:paren]
    args_str = expr[paren + 1:expr.rindex(")")]
    if not args_str:
        return name, []
    # If the entire arg is a single quoted string, preserve it as one arg
    stripped = args_str.strip()
    if (stripped.startswith("'") and stripped.endswith("'")) or \
       (stripped.startswith('"') and stripped.endswith('"')):
        return name, [stripped[1:-1]]
    # If no commas, return the single raw arg (preserves spaces)
    if "," not in args_str:
        return name, [args_str.strip()]
    args = [a.strip().strip("'\"") for a in args_str.split(",")]
    return name, args


def _filter_identity(value: Any, args: list[str], ctx: ExpressionContext) -> Any:
    return value


def _filter_upper(value: Any, args: list[str], ctx: ExpressionContext) -> Any:
    return str(value).upper() if value is not None else ""


def _filter_lower(value: Any, args: list[str], ctx: ExpressionContext) -> Any:
    return str(value).lower() if value is not None else ""


def _filter_trim(value: Any, args: list[str], ctx: ExpressionContext) -> Any:
    return str(value).strip() if value is not None else ""


def _filter_first(value: Any, args: list[str], ctx: ExpressionContext) -> Any:
    if isinstance(value, (list, tuple)) and value:
        return value[0]
    return None


def _filter_last(value: Any, args: list[str], ctx: ExpressionContext) -> Any:
    if isinstance(value, (list, tuple)) and value:
        return value[-1]
    return None


def _filter_count(value: Any, args: list[str], ctx: ExpressionContext) -> int:
    if isinstance(value, (list, tuple, dict, str)):
        return len(value)
    return 0


def _filter_join(value: Any, args: list[str], ctx: ExpressionContext) -> str:
    sep = args[0] if args else ", "
    if isinstance(value, (list, tuple)):
        return sep.join(str(v) for v in value)
    return str(value) if value is not None else ""


def _filter_default(value: Any, args: list[str], ctx: ExpressionContext) -> Any:
    if value is None or value == "":
        return args[0] if args else ""
    return value


def _filter_required(value: Any, args: list[str], ctx: ExpressionContext) -> Any:
    if value is None:
        raise ValueError(f"Required value is None")
    return value


def _filter_json(value: Any, args: list[str], ctx: ExpressionContext) -> str:
    import json
    return json.dumps(value, default=str)


def _filter_parse_json(value: Any, args: list[str], ctx: ExpressionContext) -> Any:
    import json
    if isinstance(value, str):
        return json.loads(value)
    return value


def _filter_matches(value: Any, args: list[str], ctx: ExpressionContext) -> bool:
    if not args or value is None:
        return False
    pattern = args[0]
    return bool(re.search(pattern, str(value)))


def _filter_replace(value: Any, args: list[str], ctx: ExpressionContext) -> str:
    if len(args) < 2 or value is None:
        return str(value) if value is not None else ""
    return str(value).replace(args[0], args[1])


def _filter_split(value: Any, args: list[str], ctx: ExpressionContext) -> list[str]:
    if value is None:
        return []
    sep = args[0] if args else None
    if sep == "":
        sep = None  # empty string → split on whitespace (Python default)
    return str(value).split(sep)


def _filter_truncate(value: Any, args: list[str], ctx: ExpressionContext) -> str:
    if value is None:
        return ""
    s = str(value)
    max_len = int(args[0]) if args else 100
    if len(s) <= max_len:
        return s
    return s[:max_len] + "..."


def _filter_slice(value: Any, args: list[str], ctx: ExpressionContext) -> Any:
    if not isinstance(value, (list, tuple)):
        return value
    start = int(args[0]) if len(args) > 0 else 0
    end = int(args[1]) if len(args) > 1 else len(value)
    return list(value[start:end])


def _filter_sort(value: Any, args: list[str], ctx: ExpressionContext) -> Any:
    if not isinstance(value, list):
        return value
    if args:
        key = args[0]
        return sorted(value, key=lambda x: _access(x, key) or "")
    return sorted(value, key=lambda x: str(x))


def _filter_unique(value: Any, args: list[str], ctx: ExpressionContext) -> Any:
    if isinstance(value, list):
        seen: set[str] = set()
        result = []
        for v in value:
            k = str(v)
            if k not in seen:
                seen.add(k)
                result.append(v)
        return result
    return value


def _filter_filter(value: Any, args: list[str], ctx: ExpressionContext) -> Any:
    """Filter a list by glob pattern or field value."""
    if not isinstance(value, list) or not args:
        return value
    pattern = args[0]
    if "*" in pattern:
        # Glob-style filter on string items
        import fnmatch
        return [v for v in value if fnmatch.fnmatch(str(v), pattern)]
    # Field-based filter
    return [v for v in value if _access(v, pattern)]


def _filter_map(value: Any, args: list[str], ctx: ExpressionContext) -> Any:
    """Map: extract a field from each item in a list."""
    if not isinstance(value, list) or not args:
        return value
    field = args[0]
    return [_access(v, field) for v in value]


def _filter_basename(value: Any, args: list[str], ctx: ExpressionContext) -> str:
    return Path(str(value)).name if value else ""


def _filter_dirname(value: Any, args: list[str], ctx: ExpressionContext) -> str:
    return str(Path(str(value)).parent) if value else ""


def _filter_startswith(value: Any, args: list[str], ctx: ExpressionContext) -> bool:
    if not args or value is None:
        return False
    return str(value).startswith(args[0])


def _filter_endswith(value: Any, args: list[str], ctx: ExpressionContext) -> bool:
    if not args or value is None:
        return False
    return str(value).endswith(args[0])


def _filter_round(value: Any, args: list[str], ctx: ExpressionContext) -> Any:
    try:
        digits = int(args[0]) if args else 0
        return round(float(value), digits)
    except (TypeError, ValueError):
        return value


def _filter_abs(value: Any, args: list[str], ctx: ExpressionContext) -> Any:
    try:
        return abs(float(value))
    except (TypeError, ValueError):
        return value


def _filter_descriptions(value: Any, args: list[str], ctx: ExpressionContext) -> str:
    """Format a list of tools/agents as descriptions for LLM context."""
    if not isinstance(value, list):
        return str(value)
    lines = []
    for item in value:
        if isinstance(item, dict):
            name = item.get("name", item.get("id", "unknown"))
            desc = item.get("description", "")
            lines.append(f"- {name}: {desc}")
        else:
            lines.append(f"- {item}")
    return "\n".join(lines)


# ─── Filter registry ──────────────────────────────────────────────────

_FILTERS: dict[str, Any] = {
    "upper": _filter_upper,
    "lower": _filter_lower,
    "trim": _filter_trim,
    "first": _filter_first,
    "last": _filter_last,
    "count": _filter_count,
    "join": _filter_join,
    "default": _filter_default,
    "required": _filter_required,
    "json": _filter_json,
    "parse_json": _filter_parse_json,
    "matches": _filter_matches,
    "replace": _filter_replace,
    "split": _filter_split,
    "truncate": _filter_truncate,
    "slice": _filter_slice,
    "sort": _filter_sort,
    "unique": _filter_unique,
    "filter": _filter_filter,
    "map": _filter_map,
    "basename": _filter_basename,
    "dirname": _filter_dirname,
    "startswith": _filter_startswith,
    "endswith": _filter_endswith,
    "round": _filter_round,
    "abs": _filter_abs,
    "descriptions": _filter_descriptions,
}


# ─── Comparison helpers ────────────────────────────────────────────────


def _compare(a: Any, b: Any, op: str) -> bool:
    """Compare two values, coercing types as needed."""
    try:
        a_num, b_num = float(a), float(b)
        if op == ">":
            return a_num > b_num
        if op == "<":
            return a_num < b_num
        if op == ">=":
            return a_num >= b_num
        if op == "<=":
            return a_num <= b_num
    except (TypeError, ValueError):
        pass
    # Fall back to string comparison
    if op == ">":
        return str(a) > str(b)
    if op == "<":
        return str(a) < str(b)
    if op == ">=":
        return str(a) >= str(b)
    if op == "<=":
        return str(a) <= str(b)
    return False
