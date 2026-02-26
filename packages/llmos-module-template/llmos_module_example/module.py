"""Example LLMOS Bridge module — Implementation.

Demonstrates the minimum required structure:
  1. Subclass BaseModule
  2. Set MODULE_ID, VERSION, SUPPORTED_PLATFORMS
  3. Implement one ``_action_<name>`` method per action
  4. Implement ``get_manifest()``
  5. Optionally implement ``_check_dependencies()``
"""

from __future__ import annotations

from typing import Any

from llmos_bridge.modules.base import BaseModule, Platform
from llmos_bridge.modules.manifest import ActionSpec, ModuleManifest, ParamSpec

from llmos_module_example.params import CountWordsParams, SayHelloParams


class ExampleModule(BaseModule):
    """Example module that greets people and counts words."""

    MODULE_ID = "example"
    VERSION = "0.1.0"
    SUPPORTED_PLATFORMS = [Platform.ALL]

    # ------------------------------------------------------------------
    # Optional: Check that required dependencies are installed.
    # Raise ModuleLoadError if a required package is missing.
    # ------------------------------------------------------------------

    def _check_dependencies(self) -> None:
        pass  # This module has no external dependencies.

    # ------------------------------------------------------------------
    # Actions — one method per action, named ``_action_<action_name>``.
    # Must be async. Must return a JSON-serialisable value.
    # ------------------------------------------------------------------

    async def _action_say_hello(self, params: dict[str, Any]) -> dict[str, Any]:
        p = SayHelloParams.model_validate(params)
        if p.formal:
            greeting = f"Good day, {p.name}. I trust you are well."
        else:
            greeting = f"Hello, {p.name}!"
        return {"greeting": greeting, "formal": p.formal}

    async def _action_count_words(self, params: dict[str, Any]) -> dict[str, Any]:
        p = CountWordsParams.model_validate(params)
        words = p.text.split()
        if not p.include_punctuation:
            words = [w.strip(".,!?;:\"'()[]{}") for w in words if w.strip(".,!?;:\"'()[]{}")]
        return {"text": p.text, "word_count": len(words)}

    # ------------------------------------------------------------------
    # Manifest — describes the module for the Capability Manifest system.
    # ------------------------------------------------------------------

    def get_manifest(self) -> ModuleManifest:
        return ModuleManifest(
            module_id=self.MODULE_ID,
            version=self.VERSION,
            description="Example module: greets people and counts words. Use as a template.",
            author="Your Name <you@example.com>",
            homepage="https://github.com/you/llmos-module-example",
            platforms=["all"],
            tags=["example", "template", "demo"],
            actions=[
                ActionSpec(
                    name="say_hello",
                    description="Generate a greeting for a person.",
                    params=[
                        ParamSpec("name", "string", "Name of the person to greet."),
                        ParamSpec("formal", "boolean", "Use a formal greeting.", required=False, default=False),
                    ],
                    returns="object",
                    returns_description='{"greeting": str, "formal": bool}',
                    permission_required="readonly",
                    examples=[
                        {
                            "description": "Informal greeting",
                            "params": {"name": "Alice"},
                            "expected_output": {"greeting": "Hello, Alice!", "formal": False},
                        },
                        {
                            "description": "Formal greeting",
                            "params": {"name": "Dr. Smith", "formal": True},
                            "expected_output": {
                                "greeting": "Good day, Dr. Smith. I trust you are well.",
                                "formal": True,
                            },
                        },
                    ],
                ),
                ActionSpec(
                    name="count_words",
                    description="Count the number of words in a text string.",
                    params=[
                        ParamSpec("text", "string", "Text to count words in."),
                        ParamSpec("include_punctuation", "boolean", "Count punctuation marks.", required=False, default=False),
                    ],
                    returns="object",
                    returns_description='{"text": str, "word_count": int}',
                    permission_required="readonly",
                    examples=[
                        {
                            "description": "Count words",
                            "params": {"text": "Hello world, this is a test."},
                            "expected_output": {"word_count": 6},
                        }
                    ],
                ),
            ],
        )
