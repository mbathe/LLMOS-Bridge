"""Tests for the GeminiProvider — Google Generative AI backend.

All tests mock the ``google.generativeai`` SDK so no real API calls are made.
"""

from __future__ import annotations

import json
import sys
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from langchain_llmos.providers.base import (
    LLMTurn,
    ToolCall,
    ToolDefinition,
    ToolResult,
)


# ── Mock the google.generativeai SDK ────────────────────────────────────

def _build_genai_mock() -> ModuleType:
    """Create a fake ``google.generativeai`` module tree."""
    genai = ModuleType("google.generativeai")
    genai.configure = MagicMock()

    # types sub-module
    types_mod = ModuleType("google.generativeai.types")

    class FunctionDeclaration:
        def __init__(self, name="", description="", parameters=None):
            self.name = name
            self.description = description
            self.parameters = parameters

    class Tool:
        def __init__(self, function_declarations=None):
            self.function_declarations = function_declarations or []

    class GenerationConfig:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    types_mod.FunctionDeclaration = FunctionDeclaration
    types_mod.Tool = Tool
    types_mod.ContentDict = dict
    types_mod.GenerationConfig = GenerationConfig

    genai.types = types_mod
    genai.GenerativeModel = MagicMock

    return genai, types_mod, FunctionDeclaration, Tool


@pytest.fixture(autouse=True)
def _mock_genai():
    """Inject a mock google.generativeai before importing GeminiProvider."""
    genai, types_mod, FunctionDecl, ToolCls = _build_genai_mock()

    # Inject into sys.modules
    mods_to_inject = {
        "google": ModuleType("google"),
        "google.generativeai": genai,
        "google.generativeai.types": types_mod,
    }
    mods_to_inject["google"].generativeai = genai

    with patch.dict(sys.modules, mods_to_inject):
        # Re-import to pick up the mock
        if "langchain_llmos.providers.gemini_provider" in sys.modules:
            del sys.modules["langchain_llmos.providers.gemini_provider"]

        from langchain_llmos.providers.gemini_provider import GeminiProvider

        yield {
            "GeminiProvider": GeminiProvider,
            "genai": genai,
            "FunctionDeclaration": FunctionDecl,
            "Tool": ToolCls,
        }


# ═══════════════════════════════════════════════════════════════════════
# Helper
# ═══════════════════════════════════════════════════════════════════════

def _make_response(parts: list[dict[str, Any]]) -> MagicMock:
    """Create a mock Gemini response with given parts."""
    mock_parts = []
    for p in parts:
        part = MagicMock()
        if "text" in p:
            part.text = p["text"]
            part.function_call = MagicMock()
            part.function_call.name = ""
        elif "function_call" in p:
            part.text = ""
            fc = MagicMock()
            fc.name = p["function_call"]["name"]
            fc.args = p["function_call"].get("args", {})
            part.function_call = fc
        mock_parts.append(part)

    candidate = MagicMock()
    candidate.content.parts = mock_parts

    response = MagicMock()
    response.candidates = [candidate]
    return response


# ═══════════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════════


class TestGeminiProvider:
    def test_init_sets_model(self, _mock_genai):
        provider = _mock_genai["GeminiProvider"](api_key="test-key", model="gemini-2.5-pro")
        assert provider._model_name == "gemini-2.5-pro"
        _mock_genai["genai"].configure.assert_called_once_with(api_key="test-key")

    def test_supports_vision(self, _mock_genai):
        provider = _mock_genai["GeminiProvider"](api_key="k")
        assert provider.supports_vision is True

    def test_format_tool_definitions(self, _mock_genai):
        provider = _mock_genai["GeminiProvider"](api_key="k")
        tools = [
            ToolDefinition(
                name="fs__read_file",
                description="Read a file",
                parameters_schema={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                },
            ),
        ]
        result = provider.format_tool_definitions(tools)
        assert len(result) == 1  # Single Tool wrapper
        assert len(result[0].function_declarations) == 1
        decl = result[0].function_declarations[0]
        assert decl.name == "fs__read_file"
        assert decl.description == "Read a file"

    def test_build_user_message(self, _mock_genai):
        provider = _mock_genai["GeminiProvider"](api_key="k")
        msgs = provider.build_user_message("hello")
        assert len(msgs) == 1
        assert msgs[0]["role"] == "user"
        assert msgs[0]["parts"] == [{"text": "hello"}]

    def test_build_assistant_message(self, _mock_genai):
        provider = _mock_genai["GeminiProvider"](api_key="k")
        turn = LLMTurn(
            text="I see.",
            tool_calls=[ToolCall(id="c1", name="read_file", arguments={"path": "/a"})],
            is_done=False,
            raw_response=None,
        )
        msg = provider.build_assistant_message(turn)
        assert msg["role"] == "model"
        assert len(msg["parts"]) == 2  # text + function_call
        assert msg["parts"][0] == {"text": "I see."}
        assert msg["parts"][1]["function_call"]["name"] == "read_file"

    def test_build_tool_results_message(self, _mock_genai):
        provider = _mock_genai["GeminiProvider"](api_key="k")
        results = [
            ToolResult(
                tool_call_id="call_read_file_0",
                text='{"content": "hello world"}',
            ),
        ]
        msgs = provider.build_tool_results_message(results)
        assert len(msgs) == 1
        assert msgs[0]["role"] == "user"
        assert len(msgs[0]["parts"]) == 1
        fr = msgs[0]["parts"][0]["function_response"]
        assert fr["name"] == "read_file"
        assert fr["response"]["content"] == "hello world"

    def test_build_tool_results_with_image(self, _mock_genai):
        provider = _mock_genai["GeminiProvider"](api_key="k")
        results = [
            ToolResult(
                tool_call_id="call_screenshot_0",
                text='{"status": "ok"}',
                image_b64="AAAA",
                image_media_type="image/png",
            ),
        ]
        msgs = provider.build_tool_results_message(results)
        assert len(msgs) == 2  # function response + image
        assert "function_response" in msgs[0]["parts"][0]
        assert msgs[1]["parts"][0]["inline_data"]["mime_type"] == "image/png"

    def test_build_tool_results_error(self, _mock_genai):
        provider = _mock_genai["GeminiProvider"](api_key="k")
        results = [
            ToolResult(
                tool_call_id="call_fs_0",
                text="File not found",
                is_error=True,
            ),
        ]
        msgs = provider.build_tool_results_message(results)
        fr = msgs[0]["parts"][0]["function_response"]
        assert fr["response"] == {"error": "File not found"}

    @pytest.mark.asyncio
    async def test_create_message_text_response(self, _mock_genai):
        provider = _mock_genai["GeminiProvider"](api_key="k")
        response = _make_response([{"text": "Hello!"}])
        mock_model = MagicMock()
        mock_model.generate_content_async = AsyncMock(return_value=response)

        with patch.object(_mock_genai["genai"], "GenerativeModel", return_value=mock_model):
            turn = await provider.create_message(
                system="You are helpful.",
                messages=[{"role": "user", "parts": [{"text": "Hi"}]}],
                tools=[],
            )

        assert turn.text == "Hello!"
        assert turn.is_done is True
        assert len(turn.tool_calls) == 0

    @pytest.mark.asyncio
    async def test_create_message_tool_call(self, _mock_genai):
        provider = _mock_genai["GeminiProvider"](api_key="k")
        response = _make_response([{
            "function_call": {"name": "fs__read_file", "args": {"path": "/tmp/x"}},
        }])
        mock_model = MagicMock()
        mock_model.generate_content_async = AsyncMock(return_value=response)

        with patch.object(_mock_genai["genai"], "GenerativeModel", return_value=mock_model):
            turn = await provider.create_message(
                system="sys",
                messages=[{"role": "user", "parts": [{"text": "Read /tmp/x"}]}],
                tools=[ToolDefinition("fs__read_file", "Read file", {"type": "object"})],
            )

        assert turn.is_done is False
        assert len(turn.tool_calls) == 1
        assert turn.tool_calls[0].name == "fs__read_file"
        assert turn.tool_calls[0].arguments == {"path": "/tmp/x"}

    @pytest.mark.asyncio
    async def test_close(self, _mock_genai):
        provider = _mock_genai["GeminiProvider"](api_key="k")
        await provider.close()  # Should not raise
