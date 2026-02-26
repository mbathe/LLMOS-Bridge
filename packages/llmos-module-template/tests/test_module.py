"""Tests for the example module.

Community modules must achieve >= 80% coverage to qualify for the Plugin Registry.
"""

import pytest

from llmos_module_example.module import ExampleModule


@pytest.fixture
def module() -> ExampleModule:
    return ExampleModule()


class TestSayHello:
    async def test_informal_greeting(self, module: ExampleModule) -> None:
        result = await module._action_say_hello({"name": "Alice"})
        assert result["greeting"] == "Hello, Alice!"
        assert result["formal"] is False

    async def test_formal_greeting(self, module: ExampleModule) -> None:
        result = await module._action_say_hello({"name": "Dr. Smith", "formal": True})
        assert "Dr. Smith" in result["greeting"]
        assert result["formal"] is True

    async def test_missing_name_raises(self, module: ExampleModule) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            await module._action_say_hello({})


class TestCountWords:
    async def test_count_simple(self, module: ExampleModule) -> None:
        result = await module._action_count_words({"text": "hello world"})
        assert result["word_count"] == 2

    async def test_count_with_punctuation_excluded(self, module: ExampleModule) -> None:
        result = await module._action_count_words(
            {"text": "Hello, world! How are you?"}
        )
        assert result["word_count"] == 5

    async def test_empty_string(self, module: ExampleModule) -> None:
        result = await module._action_count_words({"text": ""})
        assert result["word_count"] == 0


class TestManifest:
    def test_manifest_module_id(self, module: ExampleModule) -> None:
        manifest = module.get_manifest()
        assert manifest.module_id == "example"

    def test_manifest_actions(self, module: ExampleModule) -> None:
        manifest = module.get_manifest()
        names = manifest.action_names()
        assert "say_hello" in names
        assert "count_words" in names

    def test_action_has_examples(self, module: ExampleModule) -> None:
        manifest = module.get_manifest()
        say_hello = manifest.get_action("say_hello")
        assert say_hello is not None
        assert len(say_hello.examples) >= 2
