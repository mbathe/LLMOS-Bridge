"""Tests for LLMOSToolkit — tool generation and system prompt."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from langchain_llmos.client import LLMOSClient
from langchain_llmos.toolkit import LLMOSToolkit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_MOCK_MODULES = [
    {"module_id": "filesystem", "available": True},
    {"module_id": "os_exec", "available": True},
    {"module_id": "broken", "available": False},
]

_MOCK_FS_MANIFEST = {
    "module_id": "filesystem",
    "version": "1.0.0",
    "description": "File operations.",
    "platforms": ["all"],
    "actions": [
        {
            "name": "read_file",
            "description": "Read a file.",
            "params_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path."},
                },
                "required": ["path"],
            },
            "returns": "object",
            "permission_required": "readonly",
            "platforms": ["all"],
            "examples": [],
            "tags": [],
        },
        {
            "name": "write_file",
            "description": "Write to a file.",
            "params_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path."},
                    "content": {"type": "string", "description": "Content."},
                },
                "required": ["path", "content"],
            },
            "returns": "object",
            "permission_required": "local_worker",
            "platforms": ["all"],
            "examples": [],
            "tags": [],
        },
    ],
    "tags": [],
}

_MOCK_EXEC_MANIFEST = {
    "module_id": "os_exec",
    "version": "1.0.0",
    "description": "Command execution.",
    "platforms": ["all"],
    "actions": [
        {
            "name": "run_command",
            "description": "Run a shell command.",
            "params_schema": {
                "type": "object",
                "properties": {
                    "command": {"type": "array", "description": "Cmd args."},
                },
                "required": ["command"],
            },
            "returns": "object",
            "permission_required": "local_worker",
            "platforms": ["all"],
            "examples": [],
            "tags": [],
        },
    ],
    "tags": [],
}


def _make_mock_transport() -> httpx.MockTransport:
    """Mock transport for toolkit tests."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/modules":
            return httpx.Response(200, json=_MOCK_MODULES)
        if path == "/modules/filesystem":
            return httpx.Response(200, json=_MOCK_FS_MANIFEST)
        if path == "/modules/os_exec":
            return httpx.Response(200, json=_MOCK_EXEC_MANIFEST)
        if path == "/context":
            params = dict(request.url.params)
            if params.get("format") == "prompt":
                return httpx.Response(
                    200,
                    text="# LLMOS System Prompt\nYou are an assistant.",
                    headers={"content-type": "text/plain"},
                )
            return httpx.Response(
                200,
                json={
                    "system_prompt": "# LLMOS System Prompt\nYou are an assistant.",
                    "permission_profile": "local_worker",
                    "daemon_version": "0.8.0",
                    "modules": [
                        {"module_id": "filesystem", "version": "1.0.0", "action_count": 2},
                        {"module_id": "os_exec", "version": "1.0.0", "action_count": 1},
                    ],
                    "total_actions": 3,
                },
            )
        return httpx.Response(404)

    return httpx.MockTransport(handler)


@pytest.fixture
def toolkit() -> LLMOSToolkit:
    """Create a toolkit with mocked HTTP transport."""
    tk = LLMOSToolkit(base_url="http://test:40000")
    # Replace the internal httpx client with mock transport
    tk._client._http = httpx.Client(
        base_url="http://test:40000",
        transport=_make_mock_transport(),
    )
    return tk


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGetTools:
    def test_returns_all_tools(self, toolkit: LLMOSToolkit) -> None:
        tools = toolkit.get_tools(max_permission="local_worker")
        assert len(tools) == 3
        names = {t.name for t in tools}
        assert "filesystem__read_file" in names
        assert "filesystem__write_file" in names
        assert "os_exec__run_command" in names

    def test_filter_by_module(self, toolkit: LLMOSToolkit) -> None:
        tools = toolkit.get_tools(modules=["filesystem"])
        assert all("filesystem" in t.name for t in tools)
        assert len(tools) == 2

    def test_filter_by_permission(self, toolkit: LLMOSToolkit) -> None:
        tools = toolkit.get_tools(max_permission="readonly")
        assert len(tools) == 1
        assert tools[0].name == "filesystem__read_file"

    def test_unavailable_modules_excluded(self, toolkit: LLMOSToolkit) -> None:
        tools = toolkit.get_tools()
        names = {t.name for t in tools}
        assert not any("broken" in n for n in names)

    def test_tool_has_description(self, toolkit: LLMOSToolkit) -> None:
        tools = toolkit.get_tools(modules=["filesystem"])
        for tool in tools:
            assert "[filesystem]" in tool.description
            assert "permission:" in tool.description

    def test_tool_has_args_schema(self, toolkit: LLMOSToolkit) -> None:
        tools = toolkit.get_tools(modules=["filesystem"])
        read_tool = next(t for t in tools if t.name == "filesystem__read_file")
        schema = read_tool.args_schema
        assert schema is not None
        # Should have a 'path' field
        assert "path" in schema.model_fields


class TestGetSystemPrompt:
    def test_returns_string(self, toolkit: LLMOSToolkit) -> None:
        prompt = toolkit.get_system_prompt()
        assert isinstance(prompt, str)
        assert "LLMOS" in prompt

    def test_cached(self, toolkit: LLMOSToolkit) -> None:
        prompt1 = toolkit.get_system_prompt()
        prompt2 = toolkit.get_system_prompt()
        assert prompt1 is prompt2  # Same object — cached

    def test_refresh_clears_cache(self, toolkit: LLMOSToolkit) -> None:
        prompt1 = toolkit.get_system_prompt()
        toolkit.refresh()
        prompt2 = toolkit.get_system_prompt()
        assert prompt1 == prompt2  # Same content
        assert prompt1 is not prompt2  # Different object — re-fetched


class TestGetContext:
    def test_returns_dict(self, toolkit: LLMOSToolkit) -> None:
        context = toolkit.get_context()
        assert isinstance(context, dict)
        assert "system_prompt" in context
        assert "modules" in context

    def test_format_prompt(self, toolkit: LLMOSToolkit) -> None:
        result = toolkit.get_context(format="prompt")
        assert isinstance(result, str)


class TestToolkitLifecycle:
    def test_context_manager(self, toolkit: LLMOSToolkit) -> None:
        with toolkit as tk:
            tools = tk.get_tools()
            assert len(tools) > 0

    def test_manifest_caching(self, toolkit: LLMOSToolkit) -> None:
        toolkit.get_tools()
        # Manifests should be cached
        assert toolkit._manifests is not None
        first = toolkit._manifests
        toolkit.get_tools()  # Should not re-fetch
        assert toolkit._manifests is first

    def test_refresh_clears_manifests(self, toolkit: LLMOSToolkit) -> None:
        toolkit.get_tools()
        assert toolkit._manifests is not None
        toolkit.refresh()
        assert toolkit._manifests is None
