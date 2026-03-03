"""Tests for Module Spec v3 — Virtual Module Factory.

Tests the VirtualModuleFactory, VirtualModule, VirtualAction, and
dynamic module generation patterns.
"""

from __future__ import annotations

from typing import Any

import pytest

from llmos_bridge.modules.base import Platform
from llmos_bridge.modules.manifest import Capability, ParamSpec
from llmos_bridge.modules.virtual import (
    VirtualAction,
    VirtualModule,
    VirtualModuleFactory,
)


# ---------------------------------------------------------------------------
# Handlers for testing
# ---------------------------------------------------------------------------


async def greet_handler(params: dict[str, Any]) -> dict[str, Any]:
    return {"greeting": f"Hello, {params.get('name', 'World')}!"}


async def calculate_handler(params: dict[str, Any]) -> dict[str, Any]:
    a = params.get("a", 0)
    b = params.get("b", 0)
    op = params.get("op", "add")
    if op == "add":
        return {"result": a + b}
    elif op == "multiply":
        return {"result": a * b}
    return {"error": f"Unknown op: {op}"}


# ---------------------------------------------------------------------------
# VirtualAction tests
# ---------------------------------------------------------------------------


class TestVirtualAction:
    def test_basic_action(self):
        va = VirtualAction(handler=greet_handler, description="Greet someone")
        assert va.handler is greet_handler
        assert va.description == "Greet someone"
        assert va.execution_mode == "async"
        assert va.capabilities == []

    def test_action_with_params(self):
        va = VirtualAction(
            handler=greet_handler,
            description="Greet",
            params=[ParamSpec("name", "string", "Name to greet")],
        )
        assert len(va.params) == 1
        assert va.params[0].name == "name"

    def test_action_with_capabilities(self):
        va = VirtualAction(
            handler=greet_handler,
            capabilities=[Capability("network.send")],
        )
        assert len(va.capabilities) == 1


# ---------------------------------------------------------------------------
# VirtualModule tests
# ---------------------------------------------------------------------------


class TestVirtualModule:
    def test_create_module(self):
        module = VirtualModule(
            module_id="test_virtual",
            version="1.0.0",
            description="Test virtual module",
            actions={
                "greet": VirtualAction(
                    handler=greet_handler,
                    description="Greet someone",
                    params=[ParamSpec("name", "string", "Name")],
                ),
            },
        )
        assert module.MODULE_ID == "test_virtual"
        assert module.VERSION == "1.0.0"

    def test_manifest(self):
        module = VirtualModule(
            module_id="test",
            version="2.0.0",
            description="Test",
            actions={
                "greet": VirtualAction(handler=greet_handler, description="Greet"),
                "calc": VirtualAction(handler=calculate_handler, description="Calculate"),
            },
            tags=["test", "virtual"],
        )
        manifest = module.get_manifest()
        assert manifest.module_id == "test"
        assert manifest.version == "2.0.0"
        assert len(manifest.actions) == 2
        assert sorted(manifest.action_names()) == ["calc", "greet"]
        assert manifest.tags == ["test", "virtual"]

    @pytest.mark.asyncio
    async def test_execute_action(self):
        module = VirtualModule(
            module_id="exec_test",
            version="1.0.0",
            description="Test",
            actions={
                "greet": VirtualAction(
                    handler=greet_handler,
                    description="Greet",
                ),
            },
        )
        result = await module.execute("greet", {"name": "Alice"})
        assert result == {"greeting": "Hello, Alice!"}

    @pytest.mark.asyncio
    async def test_execute_multiple_actions(self):
        module = VirtualModule(
            module_id="multi",
            version="1.0.0",
            description="Multi-action",
            actions={
                "greet": VirtualAction(handler=greet_handler, description="Greet"),
                "calc": VirtualAction(handler=calculate_handler, description="Calc"),
            },
        )
        greet_result = await module.execute("greet", {"name": "Bob"})
        calc_result = await module.execute("calc", {"a": 3, "b": 4, "op": "multiply"})

        assert greet_result["greeting"] == "Hello, Bob!"
        assert calc_result["result"] == 12

    @pytest.mark.asyncio
    async def test_execute_nonexistent_action(self):
        from llmos_bridge.exceptions import ActionNotFoundError

        module = VirtualModule(
            module_id="test",
            version="1.0.0",
            description="Test",
            actions={},
        )
        with pytest.raises(ActionNotFoundError):
            await module.execute("nonexistent", {})

    def test_with_declared_permissions(self):
        module = VirtualModule(
            module_id="test",
            version="1.0.0",
            description="Test",
            actions={},
            declared_permissions=["filesystem.read", "network.send"],
        )
        manifest = module.get_manifest()
        assert manifest.declared_permissions == ["filesystem.read", "network.send"]

    def test_with_declared_capabilities(self):
        module = VirtualModule(
            module_id="test",
            version="1.0.0",
            description="Test",
            actions={},
            declared_capabilities=[
                Capability("filesystem.write", scope="sandbox_only"),
            ],
        )
        manifest = module.get_manifest()
        assert len(manifest.declared_capabilities) == 1
        assert manifest.declared_capabilities[0].scope == "sandbox_only"


# ---------------------------------------------------------------------------
# VirtualModuleFactory tests
# ---------------------------------------------------------------------------


class TestVirtualModuleFactory:
    def test_create(self):
        factory = VirtualModuleFactory()
        module = factory.create(
            module_id="factory_test",
            version="1.0.0",
            description="Created by factory",
            actions={
                "greet": VirtualAction(handler=greet_handler, description="Greet"),
            },
        )
        assert module.MODULE_ID == "factory_test"
        manifest = module.get_manifest()
        assert len(manifest.actions) == 1

    def test_from_callable(self):
        factory = VirtualModuleFactory()
        module = factory.from_callable(
            module_id="calculator",
            handler=calculate_handler,
            action_name="calculate",
            description="Perform a calculation",
        )
        assert module.MODULE_ID == "calculator"
        manifest = module.get_manifest()
        assert manifest.action_names() == ["calculate"]

    @pytest.mark.asyncio
    async def test_from_callable_execute(self):
        factory = VirtualModuleFactory()
        module = factory.from_callable(
            module_id="calc",
            handler=calculate_handler,
            action_name="compute",
        )
        result = await module.execute("compute", {"a": 5, "b": 3, "op": "add"})
        assert result["result"] == 8

    def test_from_tool_schema(self):
        factory = VirtualModuleFactory()
        schemas = [
            {
                "name": "greet",
                "description": "Greet someone",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Name"},
                    },
                    "required": ["name"],
                },
            },
            {
                "name": "calc",
                "description": "Calculate",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "a": {"type": "number", "description": "First number"},
                        "b": {"type": "number", "description": "Second number"},
                    },
                    "required": ["a", "b"],
                },
            },
        ]
        module = factory.from_tool_schema(
            module_id="tools",
            tool_schemas=schemas,
            handler_map={"greet": greet_handler, "calc": calculate_handler},
        )
        manifest = module.get_manifest()
        assert sorted(manifest.action_names()) == ["calc", "greet"]

        # Check params were parsed from schema.
        greet_spec = manifest.get_action("greet")
        assert greet_spec is not None
        assert len(greet_spec.params) == 1
        assert greet_spec.params[0].name == "name"
        assert greet_spec.params[0].required is True

    def test_from_tool_schema_missing_handler_skipped(self):
        factory = VirtualModuleFactory()
        schemas = [
            {"name": "greet", "description": "Greet", "parameters": {}},
            {"name": "missing", "description": "Missing handler", "parameters": {}},
        ]
        module = factory.from_tool_schema(
            module_id="partial",
            tool_schemas=schemas,
            handler_map={"greet": greet_handler},
        )
        manifest = module.get_manifest()
        assert manifest.action_names() == ["greet"]

    @pytest.mark.asyncio
    async def test_from_tool_schema_execute(self):
        factory = VirtualModuleFactory()
        module = factory.from_tool_schema(
            module_id="exec_test",
            tool_schemas=[
                {
                    "name": "greet",
                    "description": "Greet",
                    "parameters": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                    },
                },
            ],
            handler_map={"greet": greet_handler},
        )
        result = await module.execute("greet", {"name": "Charlie"})
        assert result["greeting"] == "Hello, Charlie!"

    def test_register_in_registry(self):
        from llmos_bridge.modules.registry import ModuleRegistry

        factory = VirtualModuleFactory()
        module = factory.create(
            module_id="registry_test",
            actions={
                "ping": VirtualAction(
                    handler=greet_handler, description="Ping"
                ),
            },
        )
        registry = ModuleRegistry()
        registry.register_instance(module)

        assert registry.is_available("registry_test")
        manifest = registry.get_manifest("registry_test")
        assert manifest.action_names() == ["ping"]
