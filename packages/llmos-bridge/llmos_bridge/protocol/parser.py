"""IML Protocol v2 — Plan parser.

Responsibilities:
  1. Accept raw input (str, bytes, or dict)
  2. Deserialise JSON
  3. Validate the top-level structure against IMLPlan
  4. Validate each action's params against its registered schema
  5. Return a fully-typed, immutable IMLPlan

The parser does NOT run security checks — that is the Security layer's job.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from llmos_bridge.exceptions import IMLParseError, IMLValidationError
from llmos_bridge.logging import get_logger
from llmos_bridge.protocol.models import IMLPlan
from llmos_bridge.protocol.params import ALL_PARAMS
from llmos_bridge.protocol.repair import IMLRepair

_log = get_logger(__name__)
_repairer = IMLRepair()


class IMLParser:
    """Stateless IML plan parser.

    Usage::

        parser = IMLParser()
        plan = parser.parse(raw_json_string)
    """

    def parse(self, raw: str | bytes | dict[str, Any]) -> IMLPlan:
        """Parse and validate *raw* into an :class:`IMLPlan`.

        Args:
            raw: A JSON string, UTF-8 bytes, or an already-deserialised dict.

        Returns:
            A fully validated :class:`IMLPlan` instance.

        Raises:
            IMLParseError: JSON is malformed or top-level keys are wrong.
            IMLValidationError: Pydantic validation failed.
        """
        data = self._deserialise(raw)
        plan = self._validate_plan(data)
        self._validate_all_params(plan)
        return plan

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _deserialise(self, raw: str | bytes | dict[str, Any]) -> dict[str, Any]:
        if isinstance(raw, dict):
            return raw

        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # Attempt automatic repair (trailing commas, single quotes, etc.).
            try:
                result = _repairer.repair(raw)
                _log.info(
                    "iml_auto_repair",
                    transformations=result.transformations_applied,
                )
                data = result.parsed
            except IMLParseError as repair_exc:
                raise IMLParseError(
                    f"Invalid JSON (repair failed): {repair_exc.message}",
                    raw_payload=raw[:500],
                ) from repair_exc

        if not isinstance(data, dict):
            raise IMLParseError(
                f"Expected a JSON object at the top level, got {type(data).__name__}.",
                raw_payload=str(raw)[:500],
            )

        return data  # type: ignore[return-value]

    def _validate_plan(self, data: dict[str, Any]) -> IMLPlan:
        try:
            return IMLPlan.model_validate(data)
        except ValidationError as exc:
            errors = exc.errors(include_url=False)
            # Build a message that embeds the actual Pydantic error messages so
            # callers (and tests) can match on the specific violation text.
            messages = "; ".join(e.get("msg", "") for e in errors)
            raise IMLValidationError(
                f"Plan validation failed: {messages}",
                errors=errors,
            ) from exc

    def _validate_all_params(self, plan: IMLPlan) -> None:
        """Validate each action's params against its registered schema.

        Unknown modules / actions are allowed at parse time — the Module
        Registry will raise ``ActionNotFoundError`` at execution time.
        """
        errors: list[dict[str, Any]] = []

        for action in plan.actions:
            module_params = ALL_PARAMS.get(action.module)
            if module_params is None:
                continue  # Unknown module — defer to runtime

            params_model = module_params.get(action.action)
            if params_model is None:
                continue  # Unknown action — defer to runtime

            try:
                params_model.model_validate(action.params)
            except ValidationError as exc:
                errors.append(
                    {
                        "action_id": action.id,
                        "module": action.module,
                        "action": action.action,
                        "errors": exc.errors(include_url=False),
                    }
                )

        if errors:
            summary = "; ".join(
                f"action '{e['action_id']}' ({e['module']}.{e['action']}): "
                + ", ".join(str(err["msg"]) for err in e["errors"])
                for e in errors
            )
            raise IMLValidationError(
                f"Params validation failed for {len(errors)} action(s): {summary}",
                errors=errors,
            )

    def parse_partial(self, raw: str | bytes | dict[str, Any]) -> IMLPlan:
        """Like :meth:`parse` but skips per-action params validation.

        Useful for quickly checking plan structure without requiring all
        module schemas to be registered (e.g. during unit tests).
        """
        data = self._deserialise(raw)
        return self._validate_plan(data)

    @staticmethod
    def to_json(plan: IMLPlan, indent: int = 2) -> str:
        """Serialise a plan back to a JSON string."""
        return plan.model_dump_json(indent=indent, exclude_none=True)

    @staticmethod
    def to_dict(plan: IMLPlan) -> dict[str, Any]:
        """Serialise a plan to a plain dictionary."""
        return plan.model_dump(exclude_none=True)
