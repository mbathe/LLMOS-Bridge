"""Tests for HubClient — ratings and module security (Phase 4)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from llmos_bridge.hub.client import HubClient


@pytest.fixture()
def client():
    return HubClient("http://hub.test/api/v1", api_key="rate-key-456")


class TestRateModule:
    async def test_rate_module(self, client):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "success": True,
            "average_rating": 4.2,
            "rating_count": 15,
        }
        mock_response.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_response)
        client._client = mock_http

        result = await client.rate_module("smart_sensor", stars=4)
        assert result["success"] is True
        assert result["average_rating"] == 4.2

        # Verify auth header.
        call_kwargs = mock_http.post.call_args
        headers = call_kwargs.kwargs.get("headers", {})
        assert headers["X-Hub-API-Key"] == "rate-key-456"

        # Verify URL and body.
        assert call_kwargs.args[0] == "http://hub.test/api/v1/modules/smart_sensor/rate"
        body = call_kwargs.kwargs["json"]
        assert body["stars"] == 4
        assert body["comment"] == ""

    async def test_rate_module_with_comment(self, client):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "success": True,
            "average_rating": 4.5,
            "rating_count": 16,
        }
        mock_response.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_response)
        client._client = mock_http

        result = await client.rate_module(
            "smart_sensor", stars=5, comment="Excellent module!"
        )
        assert result["success"] is True
        assert result["rating_count"] == 16

        body = mock_http.post.call_args.kwargs["json"]
        assert body["stars"] == 5
        assert body["comment"] == "Excellent module!"


class TestGetRatings:
    async def test_get_ratings(self, client):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "module_id": "smart_sensor",
            "average_rating": 4.2,
            "rating_count": 15,
            "ratings": [
                {"stars": 5, "comment": "Great!", "user": "alice"},
                {"stars": 3, "comment": "", "user": "bob"},
            ],
        }
        mock_response.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_response)
        client._client = mock_http

        result = await client.get_ratings("smart_sensor")
        assert result["average_rating"] == 4.2
        assert len(result["ratings"]) == 2
        mock_http.get.assert_called_once_with(
            "http://hub.test/api/v1/modules/smart_sensor/ratings"
        )


class TestGetModuleSecurity:
    async def test_get_module_security(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "module_id": "smart_sensor",
            "scan_score": 92,
            "last_scanned": "2026-03-01T12:00:00Z",
            "vulnerabilities": [],
            "permissions_required": ["FILE_READ", "NET_CONNECT"],
        }
        mock_response.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_response)
        client._client = mock_http

        result = await client.get_module_security("smart_sensor")
        assert result is not None
        assert result["scan_score"] == 92
        assert result["vulnerabilities"] == []
        mock_http.get.assert_called_once_with(
            "http://hub.test/api/v1/modules/smart_sensor/security"
        )

    async def test_get_module_security_not_found(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 404

        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_response)
        client._client = mock_http

        result = await client.get_module_security("nonexistent")
        assert result is None
