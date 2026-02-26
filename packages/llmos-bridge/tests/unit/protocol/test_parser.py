"""Unit tests — IML Parser.

Coverage targets:
  - Valid plan parsing (minimal, full)
  - JSON syntax errors
  - Missing required fields
  - Invalid field values (bad IDs, unknown enums)
  - Duplicate action IDs
  - Unknown depends_on references
  - Self-dependency detection
  - Template params pass through (no resolution at parse time)
  - Params validation (filesystem, os_exec)
  - Partial parsing mode
"""

import json

import pytest

from llmos_bridge.exceptions import IMLParseError, IMLValidationError
from llmos_bridge.protocol.models import (
    ExecutionMode,
    IMLAction,
    IMLPlan,
    OnErrorBehavior,
    RetryConfig,
)
from llmos_bridge.protocol.parser import IMLParser


@pytest.fixture
def parser() -> IMLParser:
    return IMLParser()


# ---------------------------------------------------------------------------
# Happy-path parsing
# ---------------------------------------------------------------------------


class TestValidPlans:
    def test_parse_minimal_plan(self, parser: IMLParser) -> None:
        raw = {
            "protocol_version": "2.0",
            "description": "Minimal plan",
            "actions": [
                {
                    "id": "a1",
                    "action": "read_file",
                    "module": "filesystem",
                    "params": {"path": "/tmp/test.txt"},
                }
            ],
        }
        plan = parser.parse(raw)
        assert plan.description == "Minimal plan"
        assert len(plan.actions) == 1
        assert plan.actions[0].id == "a1"

    def test_parse_from_json_string(self, parser: IMLParser) -> None:
        raw = json.dumps(
            {
                "protocol_version": "2.0",
                "description": "From string",
                "actions": [
                    {"id": "x1", "action": "get_file_info", "module": "filesystem", "params": {"path": "/"}}
                ],
            }
        )
        plan = parser.parse(raw)
        assert plan.actions[0].id == "x1"

    def test_parse_from_bytes(self, parser: IMLParser) -> None:
        raw = b'{"protocol_version":"2.0","description":"bytes","actions":[{"id":"b1","action":"get_system_info","module":"os_exec","params":{}}]}'
        plan = parser.parse(raw)
        assert plan.actions[0].id == "b1"

    def test_parse_with_depends_on(self, parser: IMLParser) -> None:
        raw = {
            "protocol_version": "2.0",
            "description": "With deps",
            "actions": [
                {"id": "a1", "action": "read_file", "module": "filesystem", "params": {"path": "/a"}},
                {"id": "a2", "action": "write_file", "module": "filesystem",
                 "params": {"path": "/b", "content": "x"}, "depends_on": ["a1"]},
            ],
        }
        plan = parser.parse(raw)
        assert plan.actions[1].depends_on == ["a1"]

    def test_parse_with_retry_config(self, parser: IMLParser) -> None:
        raw = {
            "protocol_version": "2.0",
            "description": "With retry",
            "actions": [
                {
                    "id": "a1",
                    "action": "http_get",
                    "module": "api_http",
                    "params": {"url": "https://example.com"},
                    "on_error": "retry",
                    "retry": {"max_attempts": 5, "delay_seconds": 2.0, "backoff_factor": 1.5},
                }
            ],
        }
        plan = parser.parse(raw)
        assert plan.actions[0].retry is not None
        assert plan.actions[0].retry.max_attempts == 5

    def test_parse_auto_generates_plan_id(self, parser: IMLParser) -> None:
        raw = {
            "protocol_version": "2.0",
            "description": "Auto ID",
            "actions": [
                {"id": "a1", "action": "read_file", "module": "filesystem", "params": {"path": "/"}}
            ],
        }
        plan = parser.parse(raw)
        assert plan.plan_id  # Non-empty UUID

    def test_parse_explicit_plan_id(self, parser: IMLParser) -> None:
        raw = {
            "plan_id": "my-plan-001",
            "protocol_version": "2.0",
            "description": "Explicit ID",
            "actions": [
                {"id": "a1", "action": "read_file", "module": "filesystem", "params": {"path": "/"}}
            ],
        }
        plan = parser.parse(raw)
        assert plan.plan_id == "my-plan-001"

    def test_parse_parallel_execution_mode(self, parser: IMLParser) -> None:
        raw = {
            "protocol_version": "2.0",
            "description": "Parallel",
            "execution_mode": "parallel",
            "actions": [
                {"id": "a1", "action": "read_file", "module": "filesystem", "params": {"path": "/a"}},
                {"id": "a2", "action": "read_file", "module": "filesystem", "params": {"path": "/b"}},
            ],
        }
        plan = parser.parse(raw)
        assert plan.execution_mode == ExecutionMode.PARALLEL

    def test_template_params_pass_through(self, parser: IMLParser) -> None:
        raw = {
            "protocol_version": "2.0",
            "description": "Templates",
            "actions": [
                {"id": "a1", "action": "read_file", "module": "filesystem", "params": {"path": "/a"}},
                {
                    "id": "a2",
                    "action": "write_file",
                    "module": "filesystem",
                    "params": {"path": "/b", "content": "{{result.a1.content}}"},
                    "depends_on": ["a1"],
                },
            ],
        }
        # Templates are not resolved at parse time — they must pass through.
        plan = parser.parse(raw)
        assert "{{result.a1.content}}" in plan.actions[1].params["content"]

    def test_to_json_roundtrip(self, parser: IMLParser) -> None:
        plan = IMLPlan(
            plan_id="rt-001",
            description="Roundtrip",
            actions=[IMLAction(id="a1", action="read_file", module="filesystem", params={"path": "/"})],
        )
        json_str = IMLParser.to_json(plan)
        plan2 = parser.parse(json_str)
        assert plan2.plan_id == "rt-001"

    def test_parse_with_metadata(self, parser: IMLParser) -> None:
        raw = {
            "protocol_version": "2.0",
            "description": "With metadata",
            "metadata": {"created_by": "test-agent", "llm_model": "claude-3-5-sonnet", "tags": ["test"]},
            "actions": [
                {"id": "a1", "action": "read_file", "module": "filesystem", "params": {"path": "/"}}
            ],
        }
        plan = parser.parse(raw)
        assert plan.metadata is not None
        assert plan.metadata.created_by == "test-agent"


# ---------------------------------------------------------------------------
# JSON errors
# ---------------------------------------------------------------------------


class TestJSONErrors:
    def test_invalid_json_string(self, parser: IMLParser) -> None:
        with pytest.raises(IMLParseError, match="Invalid JSON"):
            parser.parse("{not valid json}")

    def test_json_array_instead_of_object(self, parser: IMLParser) -> None:
        with pytest.raises(IMLParseError, match="Expected a JSON object"):
            parser.parse("[1, 2, 3]")

    def test_empty_string(self, parser: IMLParser) -> None:
        with pytest.raises(IMLParseError):
            parser.parse("")

    def test_null_json(self, parser: IMLParser) -> None:
        with pytest.raises(IMLParseError):
            parser.parse("null")


# ---------------------------------------------------------------------------
# Structural validation errors
# ---------------------------------------------------------------------------


class TestStructuralErrors:
    def test_missing_description(self, parser: IMLParser) -> None:
        with pytest.raises(IMLValidationError):
            parser.parse(
                {
                    "protocol_version": "2.0",
                    "actions": [
                        {"id": "a1", "action": "read_file", "module": "filesystem", "params": {}}
                    ],
                }
            )

    def test_empty_actions_list(self, parser: IMLParser) -> None:
        with pytest.raises(IMLValidationError):
            parser.parse(
                {"protocol_version": "2.0", "description": "Empty", "actions": []}
            )

    def test_wrong_protocol_version(self, parser: IMLParser) -> None:
        with pytest.raises(IMLValidationError):
            parser.parse(
                {
                    "protocol_version": "1.0",  # Not "2.0"
                    "description": "Wrong version",
                    "actions": [
                        {"id": "a1", "action": "read_file", "module": "filesystem", "params": {}}
                    ],
                }
            )

    def test_invalid_action_id_characters(self, parser: IMLParser) -> None:
        with pytest.raises(IMLValidationError):
            parser.parse(
                {
                    "protocol_version": "2.0",
                    "description": "Bad ID",
                    "actions": [
                        {
                            "id": "action with spaces",  # Invalid
                            "action": "read_file",
                            "module": "filesystem",
                            "params": {},
                        }
                    ],
                }
            )

    def test_duplicate_action_ids(self, parser: IMLParser) -> None:
        with pytest.raises(IMLValidationError, match="Duplicate action IDs"):
            parser.parse(
                {
                    "protocol_version": "2.0",
                    "description": "Duplicates",
                    "actions": [
                        {"id": "a1", "action": "read_file", "module": "filesystem", "params": {}},
                        {"id": "a1", "action": "write_file", "module": "filesystem", "params": {}},
                    ],
                }
            )

    def test_depends_on_unknown_id(self, parser: IMLParser) -> None:
        with pytest.raises(IMLValidationError, match="unknown action"):
            parser.parse(
                {
                    "protocol_version": "2.0",
                    "description": "Bad dep",
                    "actions": [
                        {
                            "id": "a1",
                            "action": "write_file",
                            "module": "filesystem",
                            "params": {},
                            "depends_on": ["nonexistent"],
                        }
                    ],
                }
            )

    def test_self_dependency(self, parser: IMLParser) -> None:
        with pytest.raises(IMLValidationError, match="cannot depend on itself"):
            parser.parse(
                {
                    "protocol_version": "2.0",
                    "description": "Self dep",
                    "actions": [
                        {
                            "id": "a1",
                            "action": "read_file",
                            "module": "filesystem",
                            "params": {},
                            "depends_on": ["a1"],
                        }
                    ],
                }
            )

    def test_rollback_references_unknown_action(self, parser: IMLParser) -> None:
        with pytest.raises(IMLValidationError, match="unknown action"):
            parser.parse(
                {
                    "protocol_version": "2.0",
                    "description": "Bad rollback",
                    "actions": [
                        {
                            "id": "a1",
                            "action": "delete_file",
                            "module": "filesystem",
                            "params": {},
                            "rollback": {"action": "nonexistent", "params": {}},
                        }
                    ],
                }
            )

    def test_invalid_on_error_value(self, parser: IMLParser) -> None:
        with pytest.raises(IMLValidationError):
            parser.parse(
                {
                    "protocol_version": "2.0",
                    "description": "Bad on_error",
                    "actions": [
                        {
                            "id": "a1",
                            "action": "read_file",
                            "module": "filesystem",
                            "params": {},
                            "on_error": "explode",  # Not a valid value
                        }
                    ],
                }
            )

    def test_timeout_exceeds_maximum(self, parser: IMLParser) -> None:
        with pytest.raises(IMLValidationError):
            parser.parse(
                {
                    "protocol_version": "2.0",
                    "description": "Bad timeout",
                    "actions": [
                        {
                            "id": "a1",
                            "action": "read_file",
                            "module": "filesystem",
                            "params": {},
                            "timeout": 99999,  # > MAX_ACTION_TIMEOUT_SECONDS (3600)
                        }
                    ],
                }
            )


# ---------------------------------------------------------------------------
# Params validation
# ---------------------------------------------------------------------------


class TestParamsValidation:
    def test_filesystem_read_file_valid_params(self, parser: IMLParser) -> None:
        plan = parser.parse(
            {
                "protocol_version": "2.0",
                "description": "Valid params",
                "actions": [
                    {
                        "id": "a1",
                        "action": "read_file",
                        "module": "filesystem",
                        "params": {"path": "/tmp/x.txt", "encoding": "utf-8", "start_line": 1},
                    }
                ],
            }
        )
        assert plan.actions[0].params["start_line"] == 1

    def test_filesystem_write_file_missing_required_content(self, parser: IMLParser) -> None:
        with pytest.raises(IMLValidationError, match="Params validation failed"):
            parser.parse(
                {
                    "protocol_version": "2.0",
                    "description": "Missing content",
                    "actions": [
                        {
                            "id": "a1",
                            "action": "write_file",
                            "module": "filesystem",
                            "params": {"path": "/tmp/x.txt"},  # content is required
                        }
                    ],
                }
            )

    def test_os_exec_run_command_missing_command(self, parser: IMLParser) -> None:
        with pytest.raises(IMLValidationError, match="Params validation failed"):
            parser.parse(
                {
                    "protocol_version": "2.0",
                    "description": "Missing command",
                    "actions": [
                        {
                            "id": "a1",
                            "action": "run_command",
                            "module": "os_exec",
                            "params": {},  # command is required
                        }
                    ],
                }
            )

    def test_unknown_module_skips_params_validation(self, parser: IMLParser) -> None:
        plan = parser.parse(
            {
                "protocol_version": "2.0",
                "description": "Unknown module",
                "actions": [
                    {
                        "id": "a1",
                        "action": "custom_action",
                        "module": "my_community_module",
                        "params": {"anything": "goes"},
                    }
                ],
            }
        )
        assert plan.actions[0].module == "my_community_module"

    def test_partial_parse_skips_params_validation(self, parser: IMLParser) -> None:
        plan = parser.parse_partial(
            {
                "protocol_version": "2.0",
                "description": "Partial",
                "actions": [
                    {
                        "id": "a1",
                        "action": "write_file",
                        "module": "filesystem",
                        "params": {},  # Missing content — would fail full parse
                    }
                ],
            }
        )
        assert plan.actions[0].id == "a1"
