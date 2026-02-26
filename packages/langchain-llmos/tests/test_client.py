"""Tests for LLMOSClient and AsyncLLMOSClient.

Uses httpx mock transport to avoid real HTTP calls.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from langchain_llmos.client import AsyncLLMOSClient, LLMOSClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_transport(responses: dict[str, Any]) -> httpx.MockTransport:
    """Create a mock transport that returns canned responses by path."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path in responses:
            resp_data = responses[path]
            if isinstance(resp_data, str):
                return httpx.Response(200, text=resp_data, headers={"content-type": "text/plain"})
            return httpx.Response(200, json=resp_data)
        return httpx.Response(404, json={"error": "not found"})

    return httpx.MockTransport(handler)


def _make_client(responses: dict[str, Any]) -> LLMOSClient:
    """Create an LLMOSClient with mocked transport."""
    client = LLMOSClient.__new__(LLMOSClient)
    client._http = httpx.Client(
        base_url="http://test:40000",
        transport=_mock_transport(responses),
    )
    client._base_url = "http://test:40000"
    client._api_token = None
    client._timeout = 30.0
    return client


# ---------------------------------------------------------------------------
# LLMOSClient tests
# ---------------------------------------------------------------------------


class TestLLMOSClient:
    def test_health(self) -> None:
        client = _make_client({"/health": {"status": "ok"}})
        assert client.health() == {"status": "ok"}
        client.close()

    def test_list_modules(self) -> None:
        client = _make_client({"/modules": [{"module_id": "filesystem", "available": True}]})
        modules = client.list_modules()
        assert len(modules) == 1
        assert modules[0]["module_id"] == "filesystem"
        client.close()

    def test_get_module_manifest(self) -> None:
        manifest = {"module_id": "filesystem", "version": "1.0.0", "actions": []}
        client = _make_client({"/modules/filesystem": manifest})
        assert client.get_module_manifest("filesystem") == manifest
        client.close()

    def test_submit_plan(self) -> None:
        client = _make_client({"/plans": {"plan_id": "p1", "status": "completed"}})
        result = client.submit_plan({"plan_id": "p1"}, async_execution=False)
        assert result["status"] == "completed"
        client.close()

    def test_get_plan(self) -> None:
        client = _make_client({"/plans/p1": {"plan_id": "p1", "status": "completed"}})
        assert client.get_plan("p1")["plan_id"] == "p1"
        client.close()

    def test_get_context_full(self) -> None:
        context_data = {
            "system_prompt": "You are...",
            "permission_profile": "local_worker",
            "modules": [],
            "total_actions": 0,
            "daemon_version": "0.8.0",
        }
        client = _make_client({"/context": context_data})
        result = client.get_context()
        assert isinstance(result, dict)
        assert result["system_prompt"] == "You are..."
        client.close()

    def test_get_context_prompt_format(self) -> None:
        # When format=prompt, response is plain text
        def handler(request: httpx.Request) -> httpx.Response:
            params = dict(request.url.params)
            if params.get("format") == "prompt":
                return httpx.Response(200, text="# LLMOS System Prompt", headers={"content-type": "text/plain"})
            return httpx.Response(200, json={"system_prompt": "..."})

        client = LLMOSClient.__new__(LLMOSClient)
        client._http = httpx.Client(
            base_url="http://test:40000",
            transport=httpx.MockTransport(handler),
        )
        client._base_url = "http://test:40000"
        client._api_token = None
        client._timeout = 30.0

        result = client.get_context(format="prompt")
        assert isinstance(result, str)
        assert "LLMOS" in result
        client.close()

    def test_get_system_prompt_convenience(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="System prompt text", headers={"content-type": "text/plain"})

        client = LLMOSClient.__new__(LLMOSClient)
        client._http = httpx.Client(
            base_url="http://test:40000",
            transport=httpx.MockTransport(handler),
        )
        client._base_url = "http://test:40000"
        client._api_token = None
        client._timeout = 30.0

        prompt = client.get_system_prompt()
        assert prompt == "System prompt text"
        client.close()

    def test_context_manager(self) -> None:
        with _make_client({"/health": {"status": "ok"}}) as client:
            assert client.health()["status"] == "ok"

    def test_api_token_header(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            token = request.headers.get("X-LLMOS-Token")
            return httpx.Response(200, json={"token": token})

        client = LLMOSClient(base_url="http://test:40000", api_token="secret123")
        client._http = httpx.Client(
            base_url="http://test:40000",
            headers={"X-LLMOS-Token": "secret123"},
            transport=httpx.MockTransport(handler),
        )
        result = client.health()
        assert result["token"] == "secret123"
        client.close()


# ---------------------------------------------------------------------------
# AsyncLLMOSClient tests
# ---------------------------------------------------------------------------


def _async_mock_transport(responses: dict[str, Any]) -> httpx.MockTransport:
    """Create a mock transport for async client."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path in responses:
            resp_data = responses[path]
            if isinstance(resp_data, str):
                return httpx.Response(200, text=resp_data, headers={"content-type": "text/plain"})
            return httpx.Response(200, json=resp_data)
        return httpx.Response(404, json={"error": "not found"})

    return httpx.MockTransport(handler)


def _make_async_client(responses: dict[str, Any]) -> AsyncLLMOSClient:
    """Create an AsyncLLMOSClient with mocked transport."""
    client = AsyncLLMOSClient.__new__(AsyncLLMOSClient)
    client._http = httpx.AsyncClient(
        base_url="http://test:40000",
        transport=_async_mock_transport(responses),
    )
    return client


@pytest.mark.asyncio
class TestAsyncLLMOSClient:
    async def test_health(self) -> None:
        client = _make_async_client({"/health": {"status": "ok"}})
        result = await client.health()
        assert result == {"status": "ok"}
        await client.close()

    async def test_list_modules(self) -> None:
        client = _make_async_client({"/modules": [{"module_id": "fs", "available": True}]})
        modules = await client.list_modules()
        assert len(modules) == 1
        await client.close()

    async def test_submit_plan(self) -> None:
        client = _make_async_client({"/plans": {"plan_id": "p1", "status": "completed"}})
        result = await client.submit_plan({"plan_id": "p1"}, async_execution=False)
        assert result["status"] == "completed"
        await client.close()

    async def test_get_system_prompt(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="Async prompt", headers={"content-type": "text/plain"})

        client = AsyncLLMOSClient.__new__(AsyncLLMOSClient)
        client._http = httpx.AsyncClient(
            base_url="http://test:40000",
            transport=httpx.MockTransport(handler),
        )
        prompt = await client.get_system_prompt()
        assert prompt == "Async prompt"
        await client.close()

    async def test_context_manager(self) -> None:
        client = _make_async_client({"/health": {"status": "ok"}})
        async with client:
            result = await client.health()
            assert result["status"] == "ok"
