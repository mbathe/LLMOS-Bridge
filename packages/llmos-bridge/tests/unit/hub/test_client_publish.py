"""Tests for HubClient — publish and check_updates methods."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llmos_bridge.hub.client import HubClient


@pytest.fixture()
def client():
    return HubClient("http://hub.test/api/v1", api_key="test_key")


class TestPublish:
    async def test_publish_sends_file(self, client, tmp_path):
        tarball = tmp_path / "mod-1.0.0.tar.gz"
        tarball.write_bytes(b"tarball content")

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "success": True,
            "module_id": "mod",
            "version": "1.0.0",
            "score": 85,
            "checksum": "abc123",
        }
        mock_response.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_response)
        client._client = mock_http

        result = await client.publish(tarball)
        assert result["success"] is True
        assert result["module_id"] == "mod"

        # Verify auth header was sent.
        call_kwargs = mock_http.post.call_args
        assert "X-Hub-API-Key" in call_kwargs.kwargs.get("headers", {})

    async def test_publish_sends_auth_header(self, client, tmp_path):
        tarball = tmp_path / "test.tar.gz"
        tarball.write_bytes(b"data")

        mock_response = MagicMock()
        mock_response.json.return_value = {"success": True}
        mock_response.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_response)
        client._client = mock_http

        await client.publish(tarball)
        headers = mock_http.post.call_args.kwargs.get("headers", {})
        assert headers["X-Hub-API-Key"] == "test_key"


class TestCheckUpdates:
    async def test_finds_updates(self, client):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "versions": [
                {"version": "2.0.0", "yanked": False},
                {"version": "1.0.0", "yanked": False},
            ]
        }
        mock_response.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_response)
        client._client = mock_http

        updates = await client.check_updates({"my_mod": "1.0.0"})
        assert len(updates) == 1
        assert updates[0]["latest_version"] == "2.0.0"

    async def test_skips_current_version(self, client):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "versions": [{"version": "1.0.0", "yanked": False}]
        }
        mock_response.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_response)
        client._client = mock_http

        updates = await client.check_updates({"my_mod": "1.0.0"})
        assert len(updates) == 0

    async def test_skips_yanked_versions(self, client):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "versions": [
                {"version": "2.0.0", "yanked": True},
                {"version": "1.0.0", "yanked": False},
            ]
        }
        mock_response.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_response)
        client._client = mock_http

        updates = await client.check_updates({"my_mod": "1.0.0"})
        assert len(updates) == 0

    async def test_hub_error_graceful(self, client):
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(side_effect=Exception("Connection refused"))
        client._client = mock_http

        updates = await client.check_updates({"my_mod": "1.0.0"})
        assert len(updates) == 0


class TestAuthHeaders:
    def test_auth_headers_with_key(self):
        c = HubClient("http://test", api_key="my_key")
        assert c._auth_headers() == {"X-Hub-API-Key": "my_key"}

    def test_auth_headers_without_key(self):
        c = HubClient("http://test")
        assert c._auth_headers() == {}
