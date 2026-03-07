"""Tests for AppToolRegistry — tool resolution, filtering, formatting."""

import pytest

from llmos_bridge.apps.models import ToolDefinition, ToolConstraints
from llmos_bridge.apps.tool_registry import AppToolRegistry, ResolvedTool


@pytest.fixture
def module_info():
    return {
        "filesystem": {
            "actions": [
                {"name": "read_file", "description": "Read a file", "params": {
                    "path": {"type": "string", "description": "File path", "required": True},
                }},
                {"name": "write_file", "description": "Write a file", "params": {
                    "path": {"type": "string", "description": "File path", "required": True},
                    "content": {"type": "string", "description": "File content", "required": True},
                }},
                {"name": "delete_file", "description": "Delete a file", "params": {
                    "path": {"type": "string", "description": "File path", "required": True},
                }},
                {"name": "list_dir", "description": "List directory", "params": {
                    "path": {"type": "string", "description": "Dir path"},
                }},
            ],
        },
        "os_exec": {
            "actions": [
                {"name": "run_command", "description": "Run a shell command", "params": {
                    "command": {"type": "string", "description": "Command", "required": True},
                }},
            ],
        },
    }


@pytest.fixture
def registry(module_info):
    return AppToolRegistry(module_info)


class TestModuleResolution:
    def test_all_actions_from_module(self, registry):
        td = ToolDefinition(module="filesystem")
        result = registry.resolve_tools([td])
        assert len(result) == 4
        names = {t.name for t in result}
        assert "filesystem.read_file" in names
        assert "filesystem.write_file" in names

    def test_single_action(self, registry):
        td = ToolDefinition(module="filesystem", action="read_file")
        result = registry.resolve_tools([td])
        assert len(result) == 1
        assert result[0].name == "filesystem.read_file"
        assert result[0].module == "filesystem"
        assert result[0].action == "read_file"

    def test_action_subset(self, registry):
        td = ToolDefinition(module="filesystem", actions=["read_file", "list_dir"])
        result = registry.resolve_tools([td])
        assert len(result) == 2
        names = {t.name for t in result}
        assert names == {"filesystem.read_file", "filesystem.list_dir"}

    def test_exclude_actions(self, registry):
        td = ToolDefinition(module="filesystem", exclude=["delete_file"])
        result = registry.resolve_tools([td])
        assert len(result) == 3
        names = {t.name for t in result}
        assert "filesystem.delete_file" not in names

    def test_description_override_single_action(self, registry):
        td = ToolDefinition(module="filesystem", action="read_file", description="Custom desc")
        result = registry.resolve_tools([td])
        assert result[0].description == "Custom desc"

    def test_description_not_override_multi(self, registry):
        td = ToolDefinition(module="filesystem", description="Custom desc")
        result = registry.resolve_tools([td])
        assert result[0].description == "Read a file"

    def test_unknown_module_single_action(self, registry):
        td = ToolDefinition(module="unknown_mod", action="do_thing")
        result = registry.resolve_tools([td])
        assert len(result) == 1
        assert result[0].name == "unknown_mod.do_thing"
        assert result[0].parameters == {}

    def test_unknown_module_no_action(self, registry):
        td = ToolDefinition(module="unknown_mod")
        result = registry.resolve_tools([td])
        assert len(result) == 0

    def test_parameters_resolved(self, registry):
        td = ToolDefinition(module="filesystem", action="read_file")
        result = registry.resolve_tools([td])
        assert "path" in result[0].parameters

    def test_constraints_attached(self, registry):
        td = ToolDefinition(
            module="filesystem",
            action="read_file",
            constraints=ToolConstraints(timeout="30s", read_only=True),
        )
        result = registry.resolve_tools([td])
        assert result[0].constraints.get("timeout") == "30s"
        assert result[0].constraints.get("read_only") is True

    def test_dict_actions_format(self):
        """Module info with actions as a dict instead of list."""
        info = {
            "my_mod": {
                "actions": {
                    "act1": {"description": "Action 1", "params": {}},
                    "act2": {"description": "Action 2", "params": {}},
                },
            },
        }
        reg = AppToolRegistry(info)
        result = reg.resolve_tools([ToolDefinition(module="my_mod")])
        assert len(result) == 2


class TestBuiltinResolution:
    def test_ask_user(self, registry):
        td = ToolDefinition(builtin="ask_user")
        result = registry.resolve_tools([td])
        assert len(result) == 1
        assert result[0].name == "ask_user"
        assert result[0].is_builtin is True
        assert "question" in result[0].parameters

    def test_todo(self, registry):
        td = ToolDefinition(builtin="todo")
        result = registry.resolve_tools([td])
        assert result[0].name == "todo"
        assert result[0].is_builtin is True

    def test_delegate(self, registry):
        td = ToolDefinition(builtin="delegate")
        result = registry.resolve_tools([td])
        assert result[0].name == "delegate"

    def test_emit(self, registry):
        td = ToolDefinition(builtin="emit")
        result = registry.resolve_tools([td])
        assert result[0].name == "emit"

    def test_unknown_builtin(self, registry):
        td = ToolDefinition(builtin="nonexistent")
        result = registry.resolve_tools([td])
        assert len(result) == 1
        assert result[0].description == ""

    def test_builtin_description_override(self, registry):
        td = ToolDefinition(builtin="ask_user", description="Custom ask")
        result = registry.resolve_tools([td])
        assert result[0].description == "Custom ask"


class TestCustomTools:
    def test_custom_tool_by_id(self, registry):
        td = ToolDefinition(id="my_custom_tool", description="Does something", params={"x": {"type": "string"}})
        result = registry.resolve_tools([td])
        assert len(result) == 1
        assert result[0].name == "my_custom_tool"
        assert result[0].is_builtin is True

    def test_empty_tool_def(self, registry):
        td = ToolDefinition()
        result = registry.resolve_tools([td])
        assert len(result) == 0


class TestMultipleTools:
    def test_mixed_tools(self, registry):
        tools = [
            ToolDefinition(module="filesystem", action="read_file"),
            ToolDefinition(builtin="ask_user"),
            ToolDefinition(module="os_exec"),
        ]
        result = registry.resolve_tools(tools)
        assert len(result) == 3
        names = {t.name for t in result}
        assert "filesystem.read_file" in names
        assert "ask_user" in names
        assert "os_exec.run_command" in names


class TestFormatting:
    def test_format_for_llm(self, registry):
        tools = [
            ResolvedTool(
                name="filesystem.read_file",
                module="filesystem",
                action="read_file",
                description="Read a file",
                parameters={"path": {"type": "string", "description": "File path", "required": True}},
            ),
        ]
        text = registry.format_for_llm(tools)
        assert "filesystem.read_file" in text
        assert "Read a file" in text
        assert "path" in text
        assert "(required)" in text

    def test_to_openai_tools(self, registry):
        tools = [
            ResolvedTool(
                name="filesystem.read_file",
                module="filesystem",
                action="read_file",
                description="Read a file",
                parameters={
                    "path": {"type": "string", "description": "File path", "required": True},
                    "encoding": {"type": "string", "description": "Encoding"},
                },
            ),
        ]
        openai = registry.to_openai_tools(tools)
        assert len(openai) == 1
        func = openai[0]["function"]
        assert func["name"] == "filesystem__read_file"
        assert func["description"] == "Read a file"
        assert "path" in func["parameters"]["properties"]
        assert "path" in func["parameters"]["required"]
        assert "encoding" not in func["parameters"]["required"]

    def test_to_openai_tools_enum(self, registry):
        tools = [
            ResolvedTool(
                name="todo",
                module="",
                action="",
                description="Manage tasks",
                parameters={
                    "action": {"type": "string", "enum": ["add", "list"], "required": True},
                },
                is_builtin=True,
            ),
        ]
        openai = registry.to_openai_tools(tools)
        assert openai[0]["function"]["parameters"]["properties"]["action"]["enum"] == ["add", "list"]


class TestNoModules:
    def test_empty_registry(self):
        reg = AppToolRegistry()
        td = ToolDefinition(builtin="ask_user")
        result = reg.resolve_tools([td])
        assert len(result) == 1

    def test_none_modules(self):
        reg = AppToolRegistry(None)
        result = reg.resolve_tools([ToolDefinition(module="anything", action="act")])
        assert len(result) == 1
        assert result[0].parameters == {}
