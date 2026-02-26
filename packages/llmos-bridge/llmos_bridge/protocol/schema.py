"""IML Protocol v2 — JSONSchema and Capability Manifest generation.

Generates LLM-friendly JSON schemas for:
  - The full IMLPlan structure
  - Individual action param schemas (used in Capability Manifests)
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from llmos_bridge.protocol.models import IMLPlan
from llmos_bridge.protocol.params import ALL_PARAMS


class SchemaRegistry:
    """Generates and caches JSON schemas for IML plans and action params."""

    def __init__(self) -> None:
        self._cache: dict[str, dict[str, Any]] = {}

    def get_plan_schema(self) -> dict[str, Any]:
        """Return the full JSONSchema for an IMLPlan."""
        if "plan" not in self._cache:
            self._cache["plan"] = IMLPlan.model_json_schema()
        return self._cache["plan"]

    def get_action_params_schema(self, module_id: str, action_name: str) -> dict[str, Any]:
        """Return the JSONSchema for a specific action's params model.

        Args:
            module_id:   Module identifier (e.g. 'filesystem').
            action_name: Action name (e.g. 'read_file').

        Returns:
            A JSONSchema dict, or an empty object schema if not registered.
        """
        cache_key = f"{module_id}.{action_name}"
        if cache_key not in self._cache:
            module_params = ALL_PARAMS.get(module_id, {})
            params_model: type[BaseModel] | None = module_params.get(action_name)
            if params_model is None:
                schema: dict[str, Any] = {"type": "object", "properties": {}}
            else:
                schema = params_model.model_json_schema()
            self._cache[cache_key] = schema
        return self._cache[cache_key]

    def get_module_schema(self, module_id: str) -> dict[str, Any]:
        """Return schemas for all actions in a module."""
        module_params = ALL_PARAMS.get(module_id, {})
        return {
            action_name: self.get_action_params_schema(module_id, action_name)
            for action_name in module_params
        }

    def get_all_schemas(self) -> dict[str, Any]:
        """Return the complete schema registry as a serialisable dict."""
        return {
            "plan_schema": self.get_plan_schema(),
            "modules": {
                module_id: self.get_module_schema(module_id)
                for module_id in ALL_PARAMS
            },
        }

    def to_json(self, indent: int = 2) -> str:
        """Serialise the full registry to a JSON string."""
        return json.dumps(self.get_all_schemas(), indent=indent, default=str)

    def clear_cache(self) -> None:
        """Invalidate all cached schemas (e.g. after module registration)."""
        self._cache.clear()


# Module-level singleton
_registry: SchemaRegistry | None = None


def get_schema_registry() -> SchemaRegistry:
    global _registry
    if _registry is None:
        _registry = SchemaRegistry()
    return _registry
