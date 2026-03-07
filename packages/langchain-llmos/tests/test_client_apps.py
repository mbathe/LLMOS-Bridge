"""Tests for App Language SDK client methods.

Uses httpx mock transport to avoid real HTTP calls.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from langchain_llmos.client import AsyncLLMOSClient, LLMOSClient


# ─── Helpers ──────────────────────────────────────────────────────────


def _mock_transport(responses: dict[str, Any]) -> httpx.MockTransport:
    """Create a mock transport that returns canned responses by path+method."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method

        # Check method+path combinations
        key = f"{method}:{path}"
        if key in responses:
            resp_data = responses[key]
            status = 200
            if isinstance(resp_data, tuple):
                status, resp_data = resp_data
            if resp_data is None:
                return httpx.Response(status)
            return httpx.Response(status, json=resp_data)

        # Fallback to path-only
        if path in responses:
            resp_data = responses[path]
            status = 200
            if isinstance(resp_data, tuple):
                status, resp_data = resp_data
            if resp_data is None:
                return httpx.Response(status)
            return httpx.Response(status, json=resp_data)

        return httpx.Response(404, json={"error": "not found"})

    return httpx.MockTransport(handler)


def _make_client(responses: dict[str, Any]) -> LLMOSClient:
    client = LLMOSClient.__new__(LLMOSClient)
    client._http = httpx.Client(
        base_url="http://test:40000",
        transport=_mock_transport(responses),
    )
    client._base_url = "http://test:40000"
    client._api_token = None
    client._timeout = 30.0
    return client


def _make_async_client(responses: dict[str, Any]) -> AsyncLLMOSClient:
    client = AsyncLLMOSClient.__new__(AsyncLLMOSClient)
    client._http = httpx.AsyncClient(
        base_url="http://test:40000",
        transport=_mock_transport(responses),
    )
    client._base_url = "http://test:40000"
    client._app_id = "default"
    client.session_id = None
    return client


APP_RECORD = {
    "id": "abc123",
    "name": "test-app",
    "version": "1.0",
    "description": "A test app",
    "author": "tester",
    "file_path": "/tmp/test.app.yaml",
    "status": "registered",
    "tags": ["test"],
    "created_at": 1700000000,
    "updated_at": 1700000000,
    "last_run_at": 0,
    "run_count": 0,
    "error_message": "",
}

RUN_RESULT = {
    "success": True,
    "output": "Task complete.",
    "error": None,
    "duration_ms": 150.0,
    "total_turns": 2,
    "stop_reason": "task_complete",
}


# ─── Sync Client Tests ───────────────────────────────────────────────


class TestSyncRegisterApp:
    def test_register_from_yaml(self):
        client = _make_client({
            "POST:/apps/register": (201, APP_RECORD),
        })
        result = client.register_app(yaml_text="app:\n  name: test\n")
        assert result["name"] == "test-app"
        client.close()

    def test_register_from_file(self):
        client = _make_client({
            "POST:/apps/register": (201, APP_RECORD),
        })
        result = client.register_app(file_path="/tmp/test.app.yaml")
        assert result["id"] == "abc123"
        client.close()


class TestSyncListApps:
    def test_list_all(self):
        client = _make_client({"/apps": [APP_RECORD]})
        apps = client.list_apps()
        assert len(apps) == 1
        assert apps[0]["name"] == "test-app"
        client.close()

    def test_list_empty(self):
        client = _make_client({"/apps": []})
        assert client.list_apps() == []
        client.close()


class TestSyncGetApp:
    def test_get_app(self):
        client = _make_client({"/apps/abc123": APP_RECORD})
        result = client.get_app("abc123")
        assert result["id"] == "abc123"
        client.close()

    def test_get_nonexistent(self):
        client = _make_client({})
        with pytest.raises(httpx.HTTPStatusError):
            client.get_app("nonexistent")
        client.close()


class TestSyncDeleteApp:
    def test_delete_app(self):
        client = _make_client({"DELETE:/apps/abc123": (204, None)})
        client.delete_app("abc123")  # should not raise
        client.close()


class TestSyncRunApp:
    def test_run_app(self):
        client = _make_client({"POST:/apps/abc123/run": RUN_RESULT})
        result = client.run_app("abc123", "Hello world")
        assert result["success"] is True
        assert result["output"] == "Task complete."
        client.close()

    def test_run_with_variables(self):
        client = _make_client({"POST:/apps/abc123/run": RUN_RESULT})
        result = client.run_app("abc123", "Hello", variables={"key": "val"})
        assert result["success"] is True
        client.close()


class TestSyncValidateApp:
    def test_validate(self):
        client = _make_client({
            "POST:/apps/abc123/validate": {"valid": True, "errors": []},
        })
        result = client.validate_app("abc123")
        assert result["valid"] is True
        client.close()


# ─── Async Client Tests ──────────────────────────────────────────────


class TestAsyncRegisterApp:
    @pytest.mark.asyncio
    async def test_register(self):
        client = _make_async_client({
            "POST:/apps/register": (201, APP_RECORD),
        })
        result = await client.register_app(yaml_text="app:\n  name: test\n")
        assert result["name"] == "test-app"
        await client.close()


class TestAsyncListApps:
    @pytest.mark.asyncio
    async def test_list(self):
        client = _make_async_client({"/apps": [APP_RECORD]})
        apps = await client.list_apps()
        assert len(apps) == 1
        await client.close()


class TestAsyncGetApp:
    @pytest.mark.asyncio
    async def test_get(self):
        client = _make_async_client({"/apps/abc123": APP_RECORD})
        result = await client.get_app("abc123")
        assert result["id"] == "abc123"
        await client.close()


class TestAsyncDeleteApp:
    @pytest.mark.asyncio
    async def test_delete(self):
        client = _make_async_client({"DELETE:/apps/abc123": (204, None)})
        await client.delete_app("abc123")
        await client.close()


class TestAsyncRunApp:
    @pytest.mark.asyncio
    async def test_run(self):
        client = _make_async_client({"POST:/apps/abc123/run": RUN_RESULT})
        result = await client.run_app("abc123", "Hello world")
        assert result["success"] is True
        assert result["output"] == "Task complete."
        await client.close()


class TestAsyncValidateApp:
    @pytest.mark.asyncio
    async def test_validate(self):
        client = _make_async_client({
            "POST:/apps/abc123/validate": {"valid": True, "errors": []},
        })
        result = await client.validate_app("abc123")
        assert result["valid"] is True
        await client.close()
