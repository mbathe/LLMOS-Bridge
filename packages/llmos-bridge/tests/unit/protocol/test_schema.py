"""Unit tests — SchemaRegistry."""

from __future__ import annotations

import pytest

from llmos_bridge.protocol.schema import SchemaRegistry, get_schema_registry


@pytest.mark.unit
class TestSchemaRegistry:
    def setup_method(self) -> None:
        self.registry = SchemaRegistry()

    def test_get_plan_schema_returns_dict(self) -> None:
        schema = self.registry.get_plan_schema()
        assert isinstance(schema, dict)
        assert "properties" in schema

    def test_get_plan_schema_is_cached(self) -> None:
        schema1 = self.registry.get_plan_schema()
        schema2 = self.registry.get_plan_schema()
        assert schema1 is schema2  # Same object — cached

    def test_get_action_params_schema_known_module(self) -> None:
        schema = self.registry.get_action_params_schema("filesystem", "read_file")
        assert isinstance(schema, dict)
        assert "properties" in schema
        assert "path" in schema["properties"]

    def test_get_action_params_schema_unknown_action(self) -> None:
        schema = self.registry.get_action_params_schema("filesystem", "nonexistent_action")
        assert schema == {"type": "object", "properties": {}}

    def test_get_action_params_schema_unknown_module(self) -> None:
        schema = self.registry.get_action_params_schema("no_such_module", "action")
        assert schema == {"type": "object", "properties": {}}

    def test_get_action_params_schema_is_cached(self) -> None:
        schema1 = self.registry.get_action_params_schema("filesystem", "write_file")
        schema2 = self.registry.get_action_params_schema("filesystem", "write_file")
        assert schema1 is schema2

    def test_get_module_schema_returns_all_actions(self) -> None:
        schema = self.registry.get_module_schema("filesystem")
        assert isinstance(schema, dict)
        assert "read_file" in schema
        assert "write_file" in schema

    def test_get_module_schema_unknown_module_returns_empty(self) -> None:
        schema = self.registry.get_module_schema("no_such_module")
        assert schema == {}

    def test_get_all_schemas_contains_plan_and_modules(self) -> None:
        all_schemas = self.registry.get_all_schemas()
        assert "plan_schema" in all_schemas
        assert "modules" in all_schemas
        assert "filesystem" in all_schemas["modules"]

    def test_to_json_produces_valid_json(self) -> None:
        import json
        text = self.registry.to_json()
        parsed = json.loads(text)
        assert "plan_schema" in parsed

    def test_to_json_with_indent(self) -> None:
        text = self.registry.to_json(indent=4)
        assert "    " in text  # Indented

    def test_clear_cache_invalidates_cached_schemas(self) -> None:
        schema1 = self.registry.get_plan_schema()
        self.registry.clear_cache()
        schema2 = self.registry.get_plan_schema()
        # After clearing, a new object is returned
        assert schema1 is not schema2
        # But the content should be the same
        assert schema1 == schema2


@pytest.mark.unit
class TestGetSchemaRegistry:
    def test_get_schema_registry_returns_singleton(self) -> None:
        r1 = get_schema_registry()
        r2 = get_schema_registry()
        assert r1 is r2

    def test_get_schema_registry_returns_schema_registry_instance(self) -> None:
        r = get_schema_registry()
        assert isinstance(r, SchemaRegistry)
