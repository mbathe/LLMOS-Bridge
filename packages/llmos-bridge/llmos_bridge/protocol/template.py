"""IML Protocol v2 — Template resolution engine.

Resolves ``{{result.action_id.field}}``, ``{{memory.key}}``, and
``{{env.VAR_NAME}}`` expressions in action params before dispatch.

Template syntax:
    {{result.<action_id>.<field>}}       Output field from a completed action
    {{result.<action_id>}}               Full output dict of a completed action
    {{memory.<key>}}                     Value from the key-value memory store
    {{env.<VAR_NAME>}}                   OS environment variable
"""

from __future__ import annotations

import os
import re
from typing import Any

from llmos_bridge.exceptions import TemplateResolutionError
from llmos_bridge.protocol.constants import (
    TEMPLATE_CLOSE,
    TEMPLATE_OPEN,
    TEMPLATE_PREFIX_ENV,
    TEMPLATE_PREFIX_MEMORY,
    TEMPLATE_PREFIX_RESULT,
)

_TEMPLATE_RE = re.compile(
    re.escape(TEMPLATE_OPEN) + r"(\w+)\.(\w+)(?:\.(\w+))?" + re.escape(TEMPLATE_CLOSE)
)


class TemplateResolver:
    """Resolves template expressions in action params.

    Usage::

        resolver = TemplateResolver(
            execution_results={"a1": {"content": "hello"}},
            memory_store={"api_key": "secret"},
        )
        resolved = resolver.resolve({"path": "{{result.a1.content}}"})
        # {"path": "hello"}
    """

    def __init__(
        self,
        execution_results: dict[str, Any] | None = None,
        memory_store: dict[str, Any] | None = None,
        allow_env: bool = True,
    ) -> None:
        self._results: dict[str, Any] = execution_results or {}
        self._memory: dict[str, Any] = memory_store or {}
        self._allow_env = allow_env

    def resolve(self, params: dict[str, Any]) -> dict[str, Any]:
        """Return a new dict with all template expressions resolved.

        Args:
            params: Action params potentially containing template strings.

        Returns:
            A deep copy of *params* with all templates substituted.

        Raises:
            TemplateResolutionError: A template could not be resolved.
        """
        return self._resolve_value(params)  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _resolve_value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self._resolve_string(value)
        if isinstance(value, dict):
            return {k: self._resolve_value(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._resolve_value(item) for item in value]
        return value

    def _resolve_string(self, value: str) -> Any:
        """Resolve all template expressions in a string.

        If the entire string is a single template, the resolved value
        may be of any type (dict, list, int, …).  If the string contains
        embedded templates, all parts are cast to str and concatenated.
        """
        matches = list(_TEMPLATE_RE.finditer(value))
        if not matches:
            return value

        # Single-expression shorthand — preserve original type.
        if len(matches) == 1 and matches[0].group(0) == value:
            return self._resolve_expression(
                matches[0].group(1),
                matches[0].group(2),
                matches[0].group(3),
                original=value,
            )

        # Multi-expression or partial substitution — stringify all parts.
        result = value
        for match in matches:
            resolved = self._resolve_expression(
                match.group(1), match.group(2), match.group(3), original=match.group(0)
            )
            result = result.replace(match.group(0), str(resolved))
        return result

    def _resolve_expression(
        self, prefix: str, ref: str, field: str | None, original: str
    ) -> Any:
        if prefix == TEMPLATE_PREFIX_RESULT:
            return self._resolve_result(ref, field, original)
        if prefix == TEMPLATE_PREFIX_MEMORY:
            return self._resolve_memory(ref, original)
        if prefix == TEMPLATE_PREFIX_ENV:
            return self._resolve_env(ref, original)
        raise TemplateResolutionError(
            original, f"Unknown template prefix '{prefix}'. Supported: result, memory, env."
        )

    def _resolve_result(self, action_id: str, field: str | None, original: str) -> Any:
        if action_id not in self._results:
            raise TemplateResolutionError(
                original,
                f"Action '{action_id}' has not produced a result yet. "
                "Check that it appears in 'depends_on'.",
            )
        action_result = self._results[action_id]
        if field is None:
            return action_result
        if not isinstance(action_result, dict):
            raise TemplateResolutionError(
                original,
                f"Action '{action_id}' result is not a dict — cannot access field '{field}'.",
            )
        if field not in action_result:
            raise TemplateResolutionError(
                original,
                f"Action '{action_id}' result has no field '{field}'. "
                f"Available fields: {sorted(action_result.keys())}",
            )
        return action_result[field]

    def _resolve_memory(self, key: str, original: str) -> Any:
        if key not in self._memory:
            raise TemplateResolutionError(
                original,
                f"Memory key '{key}' not found. "
                f"Available keys: {sorted(self._memory.keys())}",
            )
        return self._memory[key]

    def _resolve_env(self, var_name: str, original: str) -> str:
        if not self._allow_env:
            raise TemplateResolutionError(
                original, "Environment variable access is disabled in the current security profile."
            )
        value = os.environ.get(var_name)
        if value is None:
            raise TemplateResolutionError(
                original, f"Environment variable '{var_name}' is not set."
            )
        return value
