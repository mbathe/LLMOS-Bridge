"""Unit tests — IMLRepair and CorrectionPromptFormatter."""

from __future__ import annotations

import pytest

from llmos_bridge.exceptions import IMLParseError
from llmos_bridge.protocol.repair import (
    CorrectionPromptFormatter,
    IMLRepair,
    RepairResult,
    _close_open_structure,
    _python_literals,
    _remove_js_comments,
    _single_to_double_quotes,
    _trailing_commas,
    _unquoted_keys,
)


# ---------------------------------------------------------------------------
# Low-level repair functions
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRepairFunctions:
    def test_remove_js_line_comments(self) -> None:
        text = '{"key": "value"} // this is a comment'
        result = _remove_js_comments(text)
        assert "//" not in result
        assert '"key"' in result

    def test_remove_js_block_comments(self) -> None:
        text = '{"key": /* block comment */ "value"}'
        result = _remove_js_comments(text)
        assert "/*" not in result
        assert "block comment" not in result

    def test_trailing_commas_object(self) -> None:
        text = '{"a": 1, "b": 2,}'
        result = _trailing_commas(text)
        assert result == '{"a": 1, "b": 2}'

    def test_trailing_commas_array(self) -> None:
        text = '[1, 2, 3,]'
        result = _trailing_commas(text)
        assert result == '[1, 2, 3]'

    def test_python_literals_true(self) -> None:
        text = '{"flag": True}'
        result = _python_literals(text)
        assert '"flag": true' in result

    def test_python_literals_false(self) -> None:
        text = '{"flag": False}'
        result = _python_literals(text)
        assert '"flag": false' in result

    def test_python_literals_none(self) -> None:
        text = '{"val": None}'
        result = _python_literals(text)
        assert '"val": null' in result

    def test_unquoted_keys(self) -> None:
        text = '{key: "value", another: 42}'
        result = _unquoted_keys(text)
        assert '"key"' in result
        assert '"another"' in result

    def test_single_to_double_quotes(self) -> None:
        text = "{'key': 'value'}"
        result = _single_to_double_quotes(text)
        assert '"key"' in result
        assert '"value"' in result

    def test_close_open_structure_missing_brace(self) -> None:
        text = '{"key": "value"'
        result = _close_open_structure(text)
        assert result.endswith("}")

    def test_close_open_structure_missing_bracket(self) -> None:
        text = '[1, 2, 3'
        result = _close_open_structure(text)
        assert result.endswith("]")

    def test_close_open_structure_already_closed(self) -> None:
        text = '{"key": "value"}'
        result = _close_open_structure(text)
        assert result == text


# ---------------------------------------------------------------------------
# IMLRepair — valid JSON fast path
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIMLRepairFastPath:
    def setup_method(self) -> None:
        self.repair = IMLRepair()

    def test_valid_json_not_modified(self) -> None:
        text = '{"plan_id": "p1", "actions": []}'
        result = self.repair.repair(text)
        assert isinstance(result, RepairResult)
        assert result.was_modified is False
        assert result.parsed["plan_id"] == "p1"

    def test_strips_markdown_code_block(self) -> None:
        text = '```json\n{"key": "value"}\n```'
        result = self.repair.repair(text)
        assert result.parsed["key"] == "value"

    def test_strips_plain_code_block(self) -> None:
        text = '```\n{"key": "val"}\n```'
        result = self.repair.repair(text)
        assert result.parsed["key"] == "val"


# ---------------------------------------------------------------------------
# IMLRepair — repair transformations
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIMLRepairTransformations:
    def setup_method(self) -> None:
        self.repair = IMLRepair()

    def test_trailing_comma_repaired(self) -> None:
        text = '{"a": 1, "b": 2,}'
        result = self.repair.repair(text)
        assert result.was_modified is True
        assert result.parsed == {"a": 1, "b": 2}

    def test_python_true_false_none_repaired(self) -> None:
        text = '{"flag": True, "disabled": False, "val": None}'
        result = self.repair.repair(text)
        assert result.parsed["flag"] is True
        assert result.parsed["disabled"] is False
        assert result.parsed["val"] is None

    def test_js_comment_removed(self) -> None:
        text = '{"key": "value" // end of line\n}'
        result = self.repair.repair(text)
        assert result.parsed["key"] == "value"

    def test_truncated_json_closed(self) -> None:
        text = '{"plan_id": "test"'
        result = self.repair.repair(text)
        assert result.parsed["plan_id"] == "test"

    def test_single_quotes_converted(self) -> None:
        text = "{'plan_id': 'test', 'version': '2.0'}"
        result = self.repair.repair(text)
        assert result.parsed["plan_id"] == "test"

    def test_repair_fails_raises_iml_parse_error(self) -> None:
        # Completely unparseable garbage
        with pytest.raises(IMLParseError):
            self.repair.repair("this is not json at all !!!! @@@")

    def test_repair_result_metadata(self) -> None:
        text = '{"a": 1,}'
        result = self.repair.repair(text)
        assert result.original_text == text
        assert isinstance(result.transformations_applied, list)
        assert len(result.transformations_applied) > 0

    def test_unquoted_keys_repaired(self) -> None:
        text = "{plan_id: \"test\", version: \"2.0\"}"
        result = self.repair.repair(text)
        assert result.parsed["plan_id"] == "test"


# ---------------------------------------------------------------------------
# CorrectionPromptFormatter
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCorrectionPromptFormatter:
    def setup_method(self) -> None:
        self.formatter = CorrectionPromptFormatter()

    def test_format_parse_error_contains_header(self) -> None:
        err = IMLParseError("bad json")
        result = self.formatter.format_parse_error('{"bad": }', err)
        assert "CORRECTION REQUEST" in result
        assert "JSON syntax error" in result
        assert "bad json" in result

    def test_format_parse_error_with_hint(self) -> None:
        err = IMLParseError("trailing comma")
        result = self.formatter.format_parse_error("{}", err, hint="Remove the comma after the last item")
        assert "ADDITIONAL HINT" in result
        assert "Remove the comma" in result

    def test_format_parse_error_footer_present(self) -> None:
        err = IMLParseError("some error")
        result = self.formatter.format_parse_error("{}", err)
        assert "END CORRECTION REQUEST" in result

    def test_format_validation_error_contains_header(self) -> None:
        err = Exception("missing required field: plan_id")
        result = self.formatter.format_validation_error("{}", err)
        assert "CORRECTION REQUEST" in result
        assert "schema validation error" in result

    def test_format_validation_error_with_hint(self) -> None:
        err = Exception("invalid module name")
        result = self.formatter.format_validation_error("{}", err, hint="Use snake_case for module names")
        assert "ADDITIONAL HINT" in result
        assert "snake_case" in result

    def test_format_parse_error_common_fixes_listed(self) -> None:
        err = IMLParseError("parse error")
        result = self.formatter.format_parse_error("{}", err)
        assert "trailing commas" in result
        assert "double quotes" in result

    def test_format_validation_error_common_fixes_listed(self) -> None:
        err = Exception("validation failed")
        result = self.formatter.format_validation_error("{}", err)
        assert "protocol_version" in result
        assert "on_error" in result
