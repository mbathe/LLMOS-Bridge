"""Tests for the ExpressionEngine — template resolution, filters, operators."""

import os
import pytest

from llmos_bridge.apps.expression import ExpressionContext, ExpressionEngine


@pytest.fixture
def engine():
    return ExpressionEngine()


@pytest.fixture
def ctx():
    return ExpressionContext(
        variables={"workspace": "/home/test/project", "data_dir": "/home/test/.data", "count": 42},
        results={
            "step1": {"content": "hello world", "lines": ["a", "b", "c"]},
            "step2": {"exit_code": 0, "output": "all passed"},
            "step3": {"files": ["main.py", "test.py", "readme.md", "setup.cfg"]},
        },
        trigger={"input": "fix the bug", "source": "cli"},
        memory={"project": "This is a Python project", "key1": "value1"},
        secrets={"API_KEY": "sk-secret-123"},
        agent={"name": "test-agent", "no_tool_calls": False},
        app={"name": "test-app", "version": "1.0.0"},
        loop={"iteration": 3, "history": ["a", "b", "c"]},
        extra={"session_id": "abc123"},
    )


class TestBasicResolution:
    def test_plain_string(self, engine, ctx):
        assert engine.resolve("hello world", ctx) == "hello world"

    def test_single_variable(self, engine, ctx):
        assert engine.resolve("{{workspace}}", ctx) == "/home/test/project"

    def test_variable_interpolation(self, engine, ctx):
        assert engine.resolve("path: {{workspace}}/src", ctx) == "path: /home/test/project/src"

    def test_nested_access(self, engine, ctx):
        assert engine.resolve("{{result.step1.content}}", ctx) == "hello world"

    def test_deep_nested(self, engine, ctx):
        assert engine.resolve("{{result.step2.exit_code}}", ctx) == 0

    def test_trigger_access(self, engine, ctx):
        assert engine.resolve("{{trigger.input}}", ctx) == "fix the bug"

    def test_memory_access(self, engine, ctx):
        assert engine.resolve("{{memory.project}}", ctx) == "This is a Python project"

    def test_app_access(self, engine, ctx):
        assert engine.resolve("{{app.name}}", ctx) == "test-app"

    def test_context_access(self, engine, ctx):
        assert engine.resolve("{{context.session_id}}", ctx) == "abc123"

    def test_loop_access(self, engine, ctx):
        assert engine.resolve("{{loop.iteration}}", ctx) == 3

    def test_env_access(self, engine, ctx):
        os.environ["TEST_LLMOS_VAR"] = "test_value"
        try:
            assert engine.resolve("{{env.TEST_LLMOS_VAR}}", ctx) == "test_value"
        finally:
            del os.environ["TEST_LLMOS_VAR"]

    def test_none_returns_none(self, engine, ctx):
        assert engine.resolve("{{result.nonexistent}}", ctx) is None

    def test_preserves_type_int(self, engine, ctx):
        result = engine.resolve("{{count}}", ctx)
        assert result == 42
        assert isinstance(result, int)

    def test_preserves_type_list(self, engine, ctx):
        result = engine.resolve("{{result.step1.lines}}", ctx)
        assert result == ["a", "b", "c"]

    def test_multi_template_stringifies(self, engine, ctx):
        result = engine.resolve("{{app.name}} v{{app.version}}", ctx)
        assert result == "test-app v1.0.0"

    def test_dict_resolution(self, engine, ctx):
        data = {"key": "{{workspace}}", "nested": {"val": "{{app.name}}"}}
        result = engine.resolve(data, ctx)
        assert result == {"key": "/home/test/project", "nested": {"val": "test-app"}}

    def test_list_resolution(self, engine, ctx):
        data = ["{{app.name}}", "{{app.version}}"]
        result = engine.resolve(data, ctx)
        assert result == ["test-app", "1.0.0"]

    def test_non_string_passthrough(self, engine, ctx):
        assert engine.resolve(42, ctx) == 42
        assert engine.resolve(True, ctx) is True
        assert engine.resolve(None, ctx) is None


class TestFilters:
    def test_upper(self, engine, ctx):
        assert engine.resolve("{{result.step1.content | upper}}", ctx) == "HELLO WORLD"

    def test_lower(self, engine, ctx):
        assert engine.resolve("{{app.name | lower}}", ctx) == "test-app"

    def test_trim(self, engine, ctx):
        ctx.variables["spaced"] = "  hello  "
        assert engine.resolve("{{spaced | trim}}", ctx) == "hello"

    def test_first(self, engine, ctx):
        assert engine.resolve("{{result.step1.lines | first}}", ctx) == "a"

    def test_last(self, engine, ctx):
        assert engine.resolve("{{result.step1.lines | last}}", ctx) == "c"

    def test_count(self, engine, ctx):
        assert engine.resolve("{{result.step1.lines | count}}", ctx) == 3

    def test_join(self, engine, ctx):
        assert engine.resolve("{{result.step1.lines | join(', ')}}", ctx) == "a, b, c"

    def test_default_when_none(self, engine, ctx):
        assert engine.resolve("{{result.missing | default('N/A')}}", ctx) == "N/A"

    def test_default_when_present(self, engine, ctx):
        assert engine.resolve("{{app.name | default('N/A')}}", ctx) == "test-app"

    def test_json(self, engine, ctx):
        result = engine.resolve("{{result.step1.lines | json}}", ctx)
        assert result == '["a", "b", "c"]'

    def test_matches_true(self, engine, ctx):
        assert engine.resolve("{{result.step2.output | matches('passed')}}", ctx) is True

    def test_matches_false(self, engine, ctx):
        assert engine.resolve("{{result.step2.output | matches('failed')}}", ctx) is False

    def test_replace(self, engine, ctx):
        assert engine.resolve("{{result.step1.content | replace(hello, hi)}}", ctx) == "hi world"

    def test_split(self, engine, ctx):
        result = engine.resolve("{{result.step1.content | split( )}}", ctx)
        assert result == ["hello", "world"]

    def test_truncate(self, engine, ctx):
        result = engine.resolve("{{result.step1.content | truncate(5)}}", ctx)
        assert result == "hello..."

    def test_filter_glob(self, engine, ctx):
        result = engine.resolve("{{result.step3.files | filter(*.py)}}", ctx)
        assert result == ["main.py", "test.py"]

    def test_map_field(self, engine, ctx):
        ctx.results["items"] = [{"name": "a", "val": 1}, {"name": "b", "val": 2}]
        result = engine.resolve("{{result.items | map(name)}}", ctx)
        assert result == ["a", "b"]

    def test_slice(self, engine, ctx):
        result = engine.resolve("{{result.step1.lines | slice(0, 2)}}", ctx)
        assert result == ["a", "b"]

    def test_unique(self, engine, ctx):
        ctx.variables["dupes"] = [1, 2, 2, 3, 3, 3]
        result = engine.resolve("{{dupes | unique}}", ctx)
        assert result == [1, 2, 3]

    def test_basename(self, engine, ctx):
        assert engine.resolve("{{workspace | basename}}", ctx) == "project"

    def test_dirname(self, engine, ctx):
        assert engine.resolve("{{workspace | dirname}}", ctx) == "/home/test"

    def test_endswith(self, engine, ctx):
        assert engine.resolve("{{workspace | endswith(project)}}", ctx) is True

    def test_round(self, engine, ctx):
        ctx.variables["pi"] = 3.14159
        assert engine.resolve("{{pi | round(2)}}", ctx) == 3.14

    def test_chained_filters(self, engine, ctx):
        result = engine.resolve("{{result.step3.files | filter(*.py) | count}}", ctx)
        assert result == 2


class TestOperators:
    def test_equality_true(self, engine, ctx):
        assert engine.resolve("{{result.step2.exit_code == 0}}", ctx) is True

    def test_equality_false(self, engine, ctx):
        assert engine.resolve("{{result.step2.exit_code == 1}}", ctx) is False

    def test_not_equal(self, engine, ctx):
        assert engine.resolve("{{result.step2.exit_code != 1}}", ctx) is True

    def test_greater_than(self, engine, ctx):
        assert engine.resolve("{{count > 10}}", ctx) is True

    def test_less_than(self, engine, ctx):
        assert engine.resolve("{{count < 100}}", ctx) is True

    def test_and_true(self, engine, ctx):
        assert engine.resolve("{{count > 0 and count < 100}}", ctx) is True

    def test_or(self, engine, ctx):
        assert engine.resolve("{{count > 100 or count > 0}}", ctx) is True

    def test_not(self, engine, ctx):
        assert engine.resolve("{{not agent.no_tool_calls}}", ctx) is True

    def test_null_coalescing(self, engine, ctx):
        assert engine.resolve("{{result.missing ?? 'fallback'}}", ctx) == "fallback"

    def test_null_coalescing_present(self, engine, ctx):
        assert engine.resolve("{{app.name ?? 'fallback'}}", ctx) == "test-app"


class TestLiterals:
    def test_string_literal(self, engine, ctx):
        result = engine.resolve("{{'hello'}}", ctx)
        assert result == "hello"

    def test_integer_literal(self, engine, ctx):
        result = engine.resolve("{{42}}", ctx)
        assert result == 42

    def test_bool_true(self, engine, ctx):
        assert engine.resolve("{{true}}", ctx) is True

    def test_bool_false(self, engine, ctx):
        assert engine.resolve("{{false}}", ctx) is False

    def test_null(self, engine, ctx):
        assert engine.resolve("{{null}}", ctx) is None


class TestEvaluateCondition:
    def test_true_expression(self, engine, ctx):
        assert engine.evaluate_condition("{{result.step2.exit_code == 0}}", ctx) is True

    def test_false_expression(self, engine, ctx):
        assert engine.evaluate_condition("{{result.step2.exit_code == 1}}", ctx) is False

    def test_string_true(self, engine, ctx):
        assert engine.evaluate_condition("{{result.step1.content}}", ctx) is True

    def test_empty_string_false(self, engine, ctx):
        ctx.variables["empty"] = ""
        assert engine.evaluate_condition("{{empty}}", ctx) is False

    def test_bool_passthrough(self, engine, ctx):
        assert engine.evaluate_condition("{{agent.no_tool_calls}}", ctx) is False


class TestOptionalChaining:
    def test_optional_chain_exists(self, engine, ctx):
        result = engine.resolve("{{result.step1?.content}}", ctx)
        assert result == "hello world"

    def test_optional_chain_missing(self, engine, ctx):
        result = engine.resolve("{{result.missing?.nested?.field}}", ctx)
        assert result is None


class TestIndexAccess:
    def test_list_index(self, engine, ctx):
        assert engine.resolve("{{result.step1.lines[0]}}", ctx) == "a"

    def test_list_index_last(self, engine, ctx):
        assert engine.resolve("{{result.step1.lines[2]}}", ctx) == "c"
