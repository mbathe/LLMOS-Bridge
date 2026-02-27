"""Unit tests for ActionSpec security fields (permissions, risk_level, etc.).

Verifies that the new security decorator metadata fields on ActionSpec have
correct defaults, serialize properly in to_dict(), and do not break the
existing to_json_schema() and to_langchain_tool_schema() methods.
"""

from __future__ import annotations

import pytest

from llmos_bridge.modules.manifest import ActionSpec, ModuleManifest, ParamSpec


@pytest.mark.unit
class TestActionSpecSecurityDefaults:
    """Verify default values for new security fields."""

    def test_default_security_fields(self) -> None:
        spec = ActionSpec(name="read_file", description="Read a file")
        assert spec.permissions == []
        assert spec.risk_level == ""
        assert spec.irreversible is False
        assert spec.data_classification == ""


@pytest.mark.unit
class TestActionSpecSecuritySerialization:
    """Verify security fields appear in serialized output."""

    def test_to_dict_includes_security_fields(self) -> None:
        spec = ActionSpec(
            name="delete_file",
            description="Delete a file permanently",
            permissions=["filesystem_write"],
            risk_level="high",
            irreversible=True,
            data_classification="internal",
        )
        manifest = ModuleManifest(
            module_id="filesystem",
            version="1.0.0",
            description="Filesystem module",
            actions=[spec],
        )
        d = manifest.to_dict()
        action_dict = d["actions"][0]
        assert action_dict["permissions"] == ["filesystem_write"]
        assert action_dict["risk_level"] == "high"
        assert action_dict["irreversible"] is True
        assert action_dict["data_classification"] == "internal"

    def test_action_spec_set_fields_round_trip(self) -> None:
        spec = ActionSpec(
            name="kill_process",
            description="Kill an OS process",
            permissions=["process_execute", "process_kill"],
            risk_level="critical",
            irreversible=True,
            data_classification="sensitive",
        )
        # Build a manifest and serialize
        manifest = ModuleManifest(
            module_id="os_exec",
            version="1.0.0",
            description="OS exec module",
            actions=[spec],
        )
        d = manifest.to_dict()
        a = d["actions"][0]
        assert a["name"] == "kill_process"
        assert a["permissions"] == ["process_execute", "process_kill"]
        assert a["risk_level"] == "critical"
        assert a["irreversible"] is True
        assert a["data_classification"] == "sensitive"


@pytest.mark.unit
class TestActionSpecMethodsNotBroken:
    """Verify to_json_schema() and to_langchain_tool_schema() still work."""

    def test_to_json_schema_with_security_fields(self) -> None:
        spec = ActionSpec(
            name="write_file",
            description="Write content to a file",
            params=[
                ParamSpec(name="path", type="string", description="Target path"),
                ParamSpec(name="content", type="string", description="File content"),
            ],
            permissions=["filesystem_write"],
            risk_level="medium",
            irreversible=False,
            data_classification="internal",
        )
        schema = spec.to_json_schema()
        assert schema["type"] == "object"
        assert "path" in schema["properties"]
        assert "content" in schema["properties"]
        assert schema["required"] == ["path", "content"]

    def test_to_langchain_tool_schema_with_security_fields(self) -> None:
        spec = ActionSpec(
            name="run_command",
            description="Execute an OS command",
            params=[
                ParamSpec(name="command", type="array", description="Command tokens"),
            ],
            permissions=["process_execute"],
            risk_level="high",
            irreversible=False,
        )
        tool_schema = spec.to_langchain_tool_schema()
        assert tool_schema["name"] == "run_command"
        assert tool_schema["description"] == "Execute an OS command"
        assert "parameters" in tool_schema
        assert tool_schema["parameters"]["type"] == "object"
        assert "command" in tool_schema["parameters"]["properties"]
