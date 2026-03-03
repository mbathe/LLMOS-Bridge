"""Tests for hub.client — HubClient (mocked httpx)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llmos_bridge.hub.client import HubClient, HubModuleInfo


# ---------------------------------------------------------------------------
# HubModuleInfo
# ---------------------------------------------------------------------------

class TestHubModuleInfo:
    def test_defaults(self):
        info = HubModuleInfo(
            module_id="test",
            version="1.0",
            description="Desc",
            author="Author",
        )
        assert info.downloads == 0
        assert info.license == ""
        assert info.tags == []

    def test_full(self):
        info = HubModuleInfo(
            module_id="test",
            version="1.0",
            description="Desc",
            author="Author",
            downloads=100,
            license="MIT",
            tags=["iot"],
        )
        assert info.downloads == 100
        assert info.license == "MIT"
        assert info.tags == ["iot"]


# ---------------------------------------------------------------------------
# HubClient
# ---------------------------------------------------------------------------

class TestHubClient:
    def test_init(self):
        client = HubClient("https://hub.example.com/api/v1/", timeout=15.0)
        assert client._base_url == "https://hub.example.com/api/v1"
        assert client._timeout == 15.0
        assert client._client is None

    @pytest.mark.asyncio
    async def test_search(self):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "modules": [
                {
                    "module_id": "smart_sensor",
                    "version": "1.0.0",
                    "description": "A smart sensor",
                    "author": "Jane",
                    "downloads": 50,
                    "tags": ["iot"],
                }
            ]
        }
        mock_response.raise_for_status = MagicMock()

        mock_http_client = AsyncMock()
        mock_http_client.get.return_value = mock_response

        client = HubClient("https://hub.example.com/api/v1")
        client._client = mock_http_client

        results = await client.search("sensor", limit=5)
        assert len(results) == 1
        assert results[0].module_id == "smart_sensor"
        assert results[0].downloads == 50
        mock_http_client.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_module_info(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "module_id": "test",
            "version": "1.0",
            "description": "Test mod",
            "author": "Dev",
        }
        mock_response.raise_for_status = MagicMock()

        mock_http_client = AsyncMock()
        mock_http_client.get.return_value = mock_response

        client = HubClient("https://hub.example.com/api/v1")
        client._client = mock_http_client

        info = await client.get_module_info("test")
        assert info is not None
        assert info.module_id == "test"

    @pytest.mark.asyncio
    async def test_get_module_info_not_found(self):
        mock_response = MagicMock()
        mock_response.status_code = 404

        mock_http_client = AsyncMock()
        mock_http_client.get.return_value = mock_response

        client = HubClient("https://hub.example.com/api/v1")
        client._client = mock_http_client

        info = await client.get_module_info("nonexistent")
        assert info is None

    @pytest.mark.asyncio
    async def test_get_versions(self):
        mock_response = MagicMock()
        mock_response.json.return_value = {"versions": ["1.0.0", "1.1.0", "2.0.0"]}
        mock_response.raise_for_status = MagicMock()

        mock_http_client = AsyncMock()
        mock_http_client.get.return_value = mock_response

        client = HubClient("https://hub.example.com/api/v1")
        client._client = mock_http_client

        versions = await client.get_versions("test")
        assert versions == ["1.0.0", "1.1.0", "2.0.0"]

    @pytest.mark.asyncio
    async def test_download_package(self, tmp_path):
        mock_response = MagicMock()
        mock_response.content = b"fake-tarball-data"
        mock_response.raise_for_status = MagicMock()

        mock_http_client = AsyncMock()
        mock_http_client.get.return_value = mock_response

        client = HubClient("https://hub.example.com/api/v1")
        client._client = mock_http_client

        dest = tmp_path / "downloads"
        dest.mkdir()
        result_path = await client.download_package("test_mod", "1.0.0", dest)
        assert result_path.name == "test_mod-1.0.0.tar.gz"
        assert result_path.read_bytes() == b"fake-tarball-data"

    @pytest.mark.asyncio
    async def test_close(self):
        mock_http_client = AsyncMock()
        client = HubClient("https://hub.example.com/api/v1")
        client._client = mock_http_client

        await client.close()
        mock_http_client.aclose.assert_called_once()
        assert client._client is None

    @pytest.mark.asyncio
    async def test_close_when_not_opened(self):
        client = HubClient("https://hub.example.com/api/v1")
        await client.close()  # Should not raise.
