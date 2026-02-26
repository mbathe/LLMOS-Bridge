"""Unit tests — ApiHttpModule with httpx mocked."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import llmos_bridge.modules.api_http.module as http_module_mod
from llmos_bridge.modules.api_http import ApiHttpModule


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_mock_response(
    status_code: int = 200,
    json_data: dict | None = None,
    text: str = "",
    url: str = "https://example.com",
    is_success: bool = True,
) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = {"content-type": "application/json" if json_data else "text/plain"}
    body = json.dumps(json_data) if json_data else text
    resp.text = body
    resp.json.return_value = json_data
    resp.url = MagicMock()
    resp.url.__str__ = lambda self: url
    resp.elapsed = None
    resp.is_success = is_success
    resp.raise_for_status = MagicMock()
    return resp


def make_mock_client(response: MagicMock) -> MagicMock:
    """Create a mock httpx.AsyncClient context manager."""
    client = AsyncMock()
    client.get.return_value = response
    client.head.return_value = response
    client.post.return_value = response
    client.put.return_value = response
    client.patch.return_value = response
    client.delete.return_value = response
    return client


class MockAsyncClientCtx:
    """Mock async context manager for httpx.AsyncClient."""

    def __init__(self, client: AsyncMock, **kwargs: Any) -> None:
        self._client = client

    async def __aenter__(self) -> AsyncMock:
        return self._client

    async def __aexit__(self, *args: Any) -> None:
        pass


@pytest.fixture
def module() -> ApiHttpModule:
    return ApiHttpModule()


@pytest.fixture
def ok_response() -> MagicMock:
    return make_mock_response(200, json_data={"result": "ok"})


# ---------------------------------------------------------------------------
# HTTP Methods
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHttpGet:
    async def test_http_get_returns_response(self, module: ApiHttpModule, ok_response: MagicMock) -> None:
        client = make_mock_client(ok_response)
        with patch.object(http_module_mod, "_httpx") as mock_httpx:
            mock_httpx.AsyncClient.side_effect = lambda **kw: MockAsyncClientCtx(client, **kw)
            mock_httpx.BasicAuth = MagicMock(return_value=None)
            result = await module._action_http_get(
                {"url": "https://example.com/api", "headers": {}, "params": {}}
            )
        assert result["status_code"] == 200
        assert result["is_success"] is True
        assert result["body_json"] == {"result": "ok"}

    async def test_http_get_with_headers(self, module: ApiHttpModule, ok_response: MagicMock) -> None:
        client = make_mock_client(ok_response)
        with patch.object(http_module_mod, "_httpx") as mock_httpx:
            mock_httpx.AsyncClient.side_effect = lambda **kw: MockAsyncClientCtx(client, **kw)
            mock_httpx.BasicAuth = MagicMock(return_value=None)
            result = await module._action_http_get(
                {
                    "url": "https://api.example.com",
                    "headers": {"Authorization": "Bearer token123"},
                    "params": {"q": "search"},
                }
            )
        client.get.assert_called_once()
        call_kwargs = client.get.call_args[1]
        assert call_kwargs["headers"]["Authorization"] == "Bearer token123"


@pytest.mark.unit
class TestHttpPost:
    async def test_http_post_with_json(self, module: ApiHttpModule, ok_response: MagicMock) -> None:
        client = make_mock_client(ok_response)
        with patch.object(http_module_mod, "_httpx") as mock_httpx:
            mock_httpx.AsyncClient.side_effect = lambda **kw: MockAsyncClientCtx(client, **kw)
            mock_httpx.BasicAuth = MagicMock(return_value=None)
            result = await module._action_http_post(
                {
                    "url": "https://example.com/api",
                    "body_json": {"key": "value"},
                }
            )
        assert result["status_code"] == 200
        call_kwargs = client.post.call_args[1]
        assert call_kwargs["json"] == {"key": "value"}

    async def test_http_post_with_form_data(self, module: ApiHttpModule, ok_response: MagicMock) -> None:
        client = make_mock_client(ok_response)
        with patch.object(http_module_mod, "_httpx") as mock_httpx:
            mock_httpx.AsyncClient.side_effect = lambda **kw: MockAsyncClientCtx(client, **kw)
            mock_httpx.BasicAuth = MagicMock(return_value=None)
            result = await module._action_http_post(
                {
                    "url": "https://example.com/form",
                    "data": {"field": "value"},
                }
            )
        assert result["status_code"] == 200

    async def test_http_post_with_raw_body(self, module: ApiHttpModule, ok_response: MagicMock) -> None:
        client = make_mock_client(ok_response)
        with patch.object(http_module_mod, "_httpx") as mock_httpx:
            mock_httpx.AsyncClient.side_effect = lambda **kw: MockAsyncClientCtx(client, **kw)
            mock_httpx.BasicAuth = MagicMock(return_value=None)
            result = await module._action_http_post(
                {"url": "https://example.com", "raw_body": "plain text body"}
            )
        assert result["status_code"] == 200
        call_kwargs = client.post.call_args[1]
        assert call_kwargs["content"] == b"plain text body"


@pytest.mark.unit
class TestHttpPutPatchDelete:
    async def test_http_put(self, module: ApiHttpModule, ok_response: MagicMock) -> None:
        client = make_mock_client(ok_response)
        with patch.object(http_module_mod, "_httpx") as mock_httpx:
            mock_httpx.AsyncClient.side_effect = lambda **kw: MockAsyncClientCtx(client, **kw)
            mock_httpx.BasicAuth = MagicMock(return_value=None)
            result = await module._action_http_put(
                {"url": "https://example.com/resource/1", "body_json": {"name": "updated"}}
            )
        assert result["status_code"] == 200

    async def test_http_patch(self, module: ApiHttpModule, ok_response: MagicMock) -> None:
        client = make_mock_client(ok_response)
        with patch.object(http_module_mod, "_httpx") as mock_httpx:
            mock_httpx.AsyncClient.side_effect = lambda **kw: MockAsyncClientCtx(client, **kw)
            result = await module._action_http_patch(
                {"url": "https://example.com/resource/1", "body_json": {"field": "patched"}}
            )
        assert result["status_code"] == 200

    async def test_http_delete(self, module: ApiHttpModule, ok_response: MagicMock) -> None:
        client = make_mock_client(ok_response)
        with patch.object(http_module_mod, "_httpx") as mock_httpx:
            mock_httpx.AsyncClient.side_effect = lambda **kw: MockAsyncClientCtx(client, **kw)
            result = await module._action_http_delete({"url": "https://example.com/resource/1"})
        assert result["status_code"] == 200

    async def test_http_head(self, module: ApiHttpModule, ok_response: MagicMock) -> None:
        client = make_mock_client(ok_response)
        with patch.object(http_module_mod, "_httpx") as mock_httpx:
            mock_httpx.AsyncClient.side_effect = lambda **kw: MockAsyncClientCtx(client, **kw)
            result = await module._action_http_head({"url": "https://example.com"})
        assert result["status_code"] == 200
        assert "headers" in result


# ---------------------------------------------------------------------------
# URL health
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCheckUrlAvailability:
    async def test_available_url(self, module: ApiHttpModule, ok_response: MagicMock) -> None:
        client = make_mock_client(ok_response)
        with patch.object(http_module_mod, "_httpx") as mock_httpx:
            mock_httpx.AsyncClient.side_effect = lambda **kw: MockAsyncClientCtx(client, **kw)
            mock_httpx.UnsupportedProtocol = Exception
            mock_httpx.TimeoutException = TimeoutError
            mock_httpx.ConnectError = ConnectionError
            result = await module._action_check_url_availability(
                {"url": "https://example.com"}
            )
        assert result["available"] is True
        assert result["status_code"] == 200
        assert result["latency_ms"] >= 0

    async def test_connection_error(self, module: ApiHttpModule) -> None:
        class _UnsupportedProtocol(Exception):
            pass

        client = AsyncMock()
        client.head.side_effect = ConnectionError("Connection refused")

        with patch.object(http_module_mod, "_httpx") as mock_httpx:
            mock_httpx.AsyncClient.side_effect = lambda **kw: MockAsyncClientCtx(client, **kw)
            mock_httpx.UnsupportedProtocol = _UnsupportedProtocol
            mock_httpx.TimeoutException = TimeoutError
            mock_httpx.ConnectError = ConnectionError
            result = await module._action_check_url_availability(
                {"url": "https://unreachable.example.com"}
            )
        assert result["available"] is False
        assert result["error"] is not None


# ---------------------------------------------------------------------------
# HTML parsing
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestParseHtml:
    async def test_parse_html_text_extraction(self, module: ApiHttpModule) -> None:
        html = "<html><body><h1>Hello</h1><p>World</p></body></html>"
        result = await module._action_parse_html({"html": html, "extract": "text"})
        assert "Hello" in result["result"]
        assert "World" in result["result"]

    async def test_parse_html_links(self, module: ApiHttpModule) -> None:
        html = '<html><body><a href="https://example.com">Link</a></body></html>'
        result = await module._action_parse_html({"html": html, "extract": "links"})
        assert result["extract"] == "links"
        links = result["result"]
        assert any(l.get("href") == "https://example.com" for l in links)

    async def test_parse_html_meta(self, module: ApiHttpModule) -> None:
        html = "<html><head><title>My Page</title></head><body></body></html>"
        result = await module._action_parse_html({"html": html, "extract": "meta"})
        assert result["extract"] == "meta"
        assert result["result"]["title"] == "My Page"

    async def test_parse_html_images(self, module: ApiHttpModule) -> None:
        html = '<html><body><img src="/img/logo.png" alt="Logo"/></body></html>'
        result = await module._action_parse_html({"html": html, "extract": "images"})
        assert result["extract"] == "images"

    async def test_parse_html_no_source_raises(self, module: ApiHttpModule) -> None:
        with pytest.raises(ValueError, match="Either 'html' or 'url'"):
            await module._action_parse_html({"extract": "text"})


# ---------------------------------------------------------------------------
# GraphQL
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGraphQL:
    async def test_graphql_query(self, module: ApiHttpModule) -> None:
        gql_response = make_mock_response(200, json_data={"data": {"user": {"id": "1"}}})
        client = make_mock_client(gql_response)
        with patch.object(http_module_mod, "_httpx") as mock_httpx:
            mock_httpx.AsyncClient.side_effect = lambda **kw: MockAsyncClientCtx(client, **kw)
            mock_httpx.BasicAuth = MagicMock(return_value=None)
            result = await module._action_graphql_query(
                {
                    "url": "https://api.example.com/graphql",
                    "query": "query { user(id: 1) { id name } }",
                    "variables": {"id": 1},
                }
            )
        assert result["status_code"] == 200
        assert result["data"] == {"user": {"id": "1"}}


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSessionManagement:
    async def test_set_and_close_session(self, module: ApiHttpModule) -> None:
        with patch.object(http_module_mod, "_httpx") as mock_httpx:
            mock_client = AsyncMock()
            mock_httpx.AsyncClient.return_value = mock_client
            result = await module._action_set_session(
                {
                    "session_id": "test-session",
                    "base_url": "https://api.example.com",
                    "headers": {"Authorization": "Bearer token"},
                }
            )
        assert result["session_id"] == "test-session"
        assert "test-session" in module._sessions

        close_result = await module._action_close_session({"session_id": "test-session"})
        assert close_result["closed"] is True
        assert "test-session" not in module._sessions

    async def test_close_nonexistent_session(self, module: ApiHttpModule) -> None:
        result = await module._action_close_session({"session_id": "ghost-session"})
        assert result["closed"] is False


# ---------------------------------------------------------------------------
# Webhook
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestWebhook:
    async def test_webhook_trigger(self, module: ApiHttpModule) -> None:
        ok_response = make_mock_response(200, json_data={"received": True})
        client = make_mock_client(ok_response)
        with patch.object(http_module_mod, "_httpx") as mock_httpx:
            mock_httpx.AsyncClient.side_effect = lambda **kw: MockAsyncClientCtx(client, **kw)
            result = await module._action_webhook_trigger(
                {
                    "url": "https://hooks.example.com/webhook",
                    "method": "POST",
                    "payload": {"event": "test", "data": {"id": 1}},
                }
            )
        assert result["status_code"] == 200

    async def test_webhook_with_hmac_secret(self, module: ApiHttpModule) -> None:
        ok_response = make_mock_response(200)
        client = make_mock_client(ok_response)
        with patch.object(http_module_mod, "_httpx") as mock_httpx:
            mock_httpx.AsyncClient.side_effect = lambda **kw: MockAsyncClientCtx(client, **kw)
            result = await module._action_webhook_trigger(
                {
                    "url": "https://hooks.example.com/webhook",
                    "method": "POST",
                    "payload": {"event": "deploy"},
                    "hmac_secret": "supersecret",
                }
            )
        assert result["status_code"] == 200
        # HMAC header should have been set
        call_kwargs = client.post.call_args[1]
        assert "X-Hub-Signature-256" in call_kwargs.get("headers", {})


# ---------------------------------------------------------------------------
# Download / Upload file (mocked)
# ---------------------------------------------------------------------------


class MockStreamCtx:
    """Async context manager returned by client.stream(...)."""

    def __init__(self, chunk_data: bytes) -> None:
        self._chunk = chunk_data

    async def __aenter__(self) -> "MockStreamCtx":
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass

    def raise_for_status(self) -> None:
        pass

    async def aiter_bytes(self, chunk_size: int = 65536):
        yield self._chunk


@pytest.mark.unit
class TestDownloadFile:
    async def test_download_file_success(self, module: ApiHttpModule, tmp_path: Path) -> None:
        dest = tmp_path / "downloaded.txt"
        chunk_data = b"hello world content"

        stream_ctx = MockStreamCtx(chunk_data)
        mock_client = AsyncMock()
        # stream() must return a sync async-context-manager, not a coroutine
        mock_client.stream = MagicMock(return_value=stream_ctx)

        with patch.object(http_module_mod, "_httpx") as mock_httpx:
            mock_httpx.AsyncClient.side_effect = lambda **kw: MockAsyncClientCtx(mock_client, **kw)
            result = await module._action_download_file(
                {
                    "url": "https://example.com/file.txt",
                    "destination": str(dest),
                }
            )
        assert result["bytes_downloaded"] == len(chunk_data)
        assert dest.read_bytes() == chunk_data

    async def test_download_file_no_overwrite_raises(
        self, module: ApiHttpModule, tmp_path: Path
    ) -> None:
        dest = tmp_path / "existing.txt"
        dest.write_bytes(b"original")
        with pytest.raises(FileExistsError):
            await module._action_download_file(
                {
                    "url": "https://example.com/file.txt",
                    "destination": str(dest),
                    "overwrite": False,
                }
            )


@pytest.mark.unit
class TestUploadFile:
    async def test_upload_file_success(
        self, module: ApiHttpModule, ok_response: MagicMock, tmp_path: Path
    ) -> None:
        upload_file = tmp_path / "upload.txt"
        upload_file.write_bytes(b"file content")

        client = make_mock_client(ok_response)
        with patch.object(http_module_mod, "_httpx") as mock_httpx:
            mock_httpx.AsyncClient.side_effect = lambda **kw: MockAsyncClientCtx(client, **kw)
            mock_httpx.BasicAuth = MagicMock(return_value=None)
            result = await module._action_upload_file(
                {
                    "url": "https://example.com/upload",
                    "file_path": str(upload_file),
                }
            )
        assert result["status_code"] == 200
        client.post.assert_called_once()

    async def test_upload_file_not_found_raises(
        self, module: ApiHttpModule, tmp_path: Path
    ) -> None:
        with pytest.raises(FileNotFoundError):
            await module._action_upload_file(
                {
                    "url": "https://example.com/upload",
                    "file_path": str(tmp_path / "ghost.txt"),
                }
            )


# ---------------------------------------------------------------------------
# OAuth2
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestOAuth2:
    async def test_oauth2_get_token_success(self, module: ApiHttpModule) -> None:
        token_response = make_mock_response(
            200,
            json_data={
                "access_token": "tok123",
                "token_type": "Bearer",
                "expires_in": 3600,
            },
            is_success=True,
        )
        client = make_mock_client(token_response)
        with patch.object(http_module_mod, "_httpx") as mock_httpx:
            mock_httpx.AsyncClient.side_effect = lambda **kw: MockAsyncClientCtx(client, **kw)
            result = await module._action_oauth2_get_token(
                {
                    "token_url": "https://auth.example.com/token",
                    "client_id": "my-client",
                    "client_secret": "my-secret",
                    "grant_type": "client_credentials",
                }
            )
        assert result["access_token"] == "tok123"
        assert result["token_type"] == "Bearer"

    async def test_oauth2_get_token_failure_raises(self, module: ApiHttpModule) -> None:
        error_response = make_mock_response(
            401,
            json_data={"error": "invalid_client", "error_description": "Client auth failed"},
            is_success=False,
        )
        client = make_mock_client(error_response)
        with patch.object(http_module_mod, "_httpx") as mock_httpx:
            mock_httpx.AsyncClient.side_effect = lambda **kw: MockAsyncClientCtx(client, **kw)
            with pytest.raises(PermissionError, match="OAuth2 token request failed"):
                await module._action_oauth2_get_token(
                    {
                        "token_url": "https://auth.example.com/token",
                        "client_id": "bad-client",
                        "grant_type": "client_credentials",
                    }
                )


# ---------------------------------------------------------------------------
# HTML parsing — additional branches
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestParseHtmlExtended:
    async def test_parse_html_tables(self, module: ApiHttpModule) -> None:
        html = """<html><body>
        <table><tr><th>Name</th><th>Age</th></tr>
        <tr><td>Alice</td><td>30</td></tr></table>
        </body></html>"""
        result = await module._action_parse_html({"html": html, "extract": "tables"})
        assert result["extract"] == "tables"
        assert isinstance(result["result"], list)

    async def test_parse_html_attrs(self, module: ApiHttpModule) -> None:
        html = '<html><body><div class="container" id="main">content</div></body></html>'
        result = await module._action_parse_html(
            {"html": html, "extract": "attrs", "selector": "div"}
        )
        assert result["extract"] == "attrs"

    async def test_parse_html_raw_html(self, module: ApiHttpModule) -> None:
        html = "<html><body><h1>Title</h1></body></html>"
        result = await module._action_parse_html({"html": html, "extract": "html"})
        assert result["extract"] == "html"
        assert isinstance(result["result"], list)


# ---------------------------------------------------------------------------
# Webhook with retry
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestWebhookRetry:
    async def test_webhook_get_method(self, module: ApiHttpModule) -> None:
        ok_response = make_mock_response(200, json_data={"ok": True})
        client = make_mock_client(ok_response)
        with patch.object(http_module_mod, "_httpx") as mock_httpx:
            mock_httpx.AsyncClient.side_effect = lambda **kw: MockAsyncClientCtx(client, **kw)
            result = await module._action_webhook_trigger(
                {
                    "url": "https://hooks.example.com/event",
                    "method": "GET",
                    "payload": {},
                }
            )
        assert result["status_code"] == 200
        client.get.assert_called_once()

    async def test_webhook_put_method(self, module: ApiHttpModule) -> None:
        ok_response = make_mock_response(200)
        client = make_mock_client(ok_response)
        with patch.object(http_module_mod, "_httpx") as mock_httpx:
            mock_httpx.AsyncClient.side_effect = lambda **kw: MockAsyncClientCtx(client, **kw)
            result = await module._action_webhook_trigger(
                {
                    "url": "https://hooks.example.com/event",
                    "method": "PUT",
                    "payload": {"key": "value"},
                }
            )
        assert result["status_code"] == 200
        client.put.assert_called_once()

    async def test_webhook_patch_method(self, module: ApiHttpModule) -> None:
        ok_response = make_mock_response(200)
        client = make_mock_client(ok_response)
        with patch.object(http_module_mod, "_httpx") as mock_httpx:
            mock_httpx.AsyncClient.side_effect = lambda **kw: MockAsyncClientCtx(client, **kw)
            result = await module._action_webhook_trigger(
                {
                    "url": "https://hooks.example.com/event",
                    "method": "PATCH",
                    "payload": {"field": "updated"},
                }
            )
        assert result["status_code"] == 200
        client.patch.assert_called_once()
