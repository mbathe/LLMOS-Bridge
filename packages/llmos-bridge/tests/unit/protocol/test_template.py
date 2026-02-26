"""Unit tests — Template resolver."""

import os

import pytest

from llmos_bridge.exceptions import TemplateResolutionError
from llmos_bridge.protocol.template import TemplateResolver


@pytest.fixture
def resolver() -> TemplateResolver:
    return TemplateResolver(
        execution_results={
            "a1": {"content": "Hello World", "size": 42, "lines": ["l1", "l2"]},
            "a2": {"rows": [{"name": "Alice"}, {"name": "Bob"}]},
        },
        memory_store={"api_key": "secret123", "last_run": "2025-01-01"},
    )


class TestResultTemplates:
    def test_simple_field_access(self, resolver: TemplateResolver) -> None:
        params = {"content": "{{result.a1.content}}"}
        resolved = resolver.resolve(params)
        assert resolved["content"] == "Hello World"

    def test_integer_field(self, resolver: TemplateResolver) -> None:
        params = {"size": "{{result.a1.size}}"}
        resolved = resolver.resolve(params)
        assert resolved["size"] == 42

    def test_list_field(self, resolver: TemplateResolver) -> None:
        params = {"data": "{{result.a1.lines}}"}
        resolved = resolver.resolve(params)
        assert resolved["data"] == ["l1", "l2"]

    def test_full_result_dict(self, resolver: TemplateResolver) -> None:
        params = {"all": "{{result.a1}}"}
        resolved = resolver.resolve(params)
        assert resolved["all"]["content"] == "Hello World"

    def test_embedded_template_in_string(self, resolver: TemplateResolver) -> None:
        params = {"message": "Size is {{result.a1.size}} bytes"}
        resolved = resolver.resolve(params)
        assert resolved["message"] == "Size is 42 bytes"

    def test_unknown_action_raises(self, resolver: TemplateResolver) -> None:
        params = {"x": "{{result.nonexistent.field}}"}
        with pytest.raises(TemplateResolutionError, match="has not produced a result"):
            resolver.resolve(params)

    def test_unknown_field_raises(self, resolver: TemplateResolver) -> None:
        params = {"x": "{{result.a1.nonexistent}}"}
        with pytest.raises(TemplateResolutionError, match="has no field"):
            resolver.resolve(params)


class TestMemoryTemplates:
    def test_memory_key(self, resolver: TemplateResolver) -> None:
        params = {"key": "{{memory.api_key}}"}
        resolved = resolver.resolve(params)
        assert resolved["key"] == "secret123"

    def test_missing_memory_key_raises(self, resolver: TemplateResolver) -> None:
        params = {"x": "{{memory.missing_key}}"}
        with pytest.raises(TemplateResolutionError, match="Memory key"):
            resolver.resolve(params)


class TestEnvTemplates:
    def test_env_var(self, resolver: TemplateResolver, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MY_VAR", "hello_from_env")
        params = {"x": "{{env.MY_VAR}}"}
        resolved = resolver.resolve(params)
        assert resolved["x"] == "hello_from_env"

    def test_missing_env_var_raises(self, resolver: TemplateResolver) -> None:
        params = {"x": "{{env.NONEXISTENT_XYZ_123}}"}
        with pytest.raises(TemplateResolutionError, match="not set"):
            resolver.resolve(params)

    def test_env_disabled_raises(self) -> None:
        r = TemplateResolver(allow_env=False)
        params = {"x": "{{env.HOME}}"}
        with pytest.raises(TemplateResolutionError, match="disabled"):
            r.resolve(params)


class TestNestedParams:
    def test_nested_dict(self, resolver: TemplateResolver) -> None:
        params = {"outer": {"inner": "{{result.a1.content}}"}}
        resolved = resolver.resolve(params)
        assert resolved["outer"]["inner"] == "Hello World"

    def test_list_of_templates(self, resolver: TemplateResolver) -> None:
        params = {"items": ["{{result.a1.content}}", "static"]}
        resolved = resolver.resolve(params)
        assert resolved["items"][0] == "Hello World"
        assert resolved["items"][1] == "static"

    def test_no_templates_passes_through(self, resolver: TemplateResolver) -> None:
        params = {"path": "/tmp/file.txt", "encoding": "utf-8", "count": 42}
        resolved = resolver.resolve(params)
        assert resolved == params

    def test_unknown_prefix_raises(self, resolver: TemplateResolver) -> None:
        params = {"x": "{{unknown.ref.field}}"}
        with pytest.raises(TemplateResolutionError, match="Unknown template prefix"):
            resolver.resolve(params)
