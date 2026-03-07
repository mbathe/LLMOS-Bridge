"""Tests for HubClient — publisher management and categories (Phase 4)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from llmos_bridge.hub.client import HubClient


@pytest.fixture()
def client():
    return HubClient("http://hub.test/api/v1", api_key="pub-key-123")


class TestRegisterPublisher:
    async def test_register_publisher(self, client):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "publisher_id": "pub_abc",
            "name": "Alice",
            "api_key": "new-api-key-xyz",
        }
        mock_response.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_response)
        client._client = mock_http

        result = await client.register_publisher("Alice", email="alice@example.com")
        assert result["publisher_id"] == "pub_abc"
        assert result["name"] == "Alice"
        assert result["api_key"] == "new-api-key-xyz"

        # Verify correct URL and JSON body.
        call_args = mock_http.post.call_args
        assert call_args.args[0] == "http://hub.test/api/v1/publishers/register"
        body = call_args.kwargs["json"]
        assert body["name"] == "Alice"
        assert body["email"] == "alice@example.com"


class TestGetPublisher:
    async def test_get_publisher(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "publisher_id": "pub_abc",
            "name": "Alice",
            "module_count": 5,
        }
        mock_response.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_response)
        client._client = mock_http

        result = await client.get_publisher("pub_abc")
        assert result is not None
        assert result["publisher_id"] == "pub_abc"
        assert result["name"] == "Alice"
        mock_http.get.assert_called_once_with(
            "http://hub.test/api/v1/publishers/pub_abc"
        )

    async def test_get_publisher_not_found(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 404

        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_response)
        client._client = mock_http

        result = await client.get_publisher("nonexistent")
        assert result is None


class TestRotateKey:
    async def test_rotate_key(self, client):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "api_key": "rotated-key-999",
            "expires_at": "2027-01-01T00:00:00Z",
        }
        mock_response.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_response)
        client._client = mock_http

        result = await client.rotate_key("pub_abc")
        assert result["api_key"] == "rotated-key-999"

        # Verify auth header was sent.
        call_kwargs = mock_http.post.call_args
        headers = call_kwargs.kwargs.get("headers", {})
        assert headers["X-Hub-API-Key"] == "pub-key-123"

        # Verify correct endpoint.
        assert call_kwargs.args[0] == "http://hub.test/api/v1/publishers/pub_abc/rotate-key"


class TestGetCategories:
    async def test_get_categories(self, client):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "categories": [
                {"name": "automation", "count": 12},
                {"name": "iot", "count": 8},
                {"name": "security", "count": 5},
            ]
        }
        mock_response.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_response)
        client._client = mock_http

        result = await client.get_categories()
        assert len(result["categories"]) == 3
        assert result["categories"][0]["name"] == "automation"
        mock_http.get.assert_called_once_with(
            "http://hub.test/api/v1/categories"
        )
