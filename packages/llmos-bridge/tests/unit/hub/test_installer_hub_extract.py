"""Tests for HubClient.download_package — cache, extraction, path traversal."""

from __future__ import annotations

import io
import tarfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from llmos_bridge.hub.cache import PackageCache
from llmos_bridge.hub.client import HubClient


def _make_tarball(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, content in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return buf.getvalue()


@pytest.fixture()
def cache(tmp_path):
    return PackageCache(tmp_path / "cache")


class TestDownloadPackage:
    async def test_download_and_extract(self, tmp_path):
        tarball = _make_tarball({"module.py": b"class Mod: pass\n"})

        mock_response = MagicMock()
        mock_response.content = tarball
        mock_response.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_response)

        client = HubClient("http://hub.test/api/v1")
        client._client = mock_http

        dest = tmp_path / "my_mod"
        result = await client.download_package("my_mod", "1.0.0", dest)
        assert result == dest
        assert (dest / "module.py").exists()

    async def test_cache_hit(self, tmp_path, cache):
        tarball = _make_tarball({"cached.py": b"cached = True\n"})
        await cache.store("cached_mod", "1.0.0", tarball)

        mock_http = AsyncMock()
        client = HubClient("http://hub.test/api/v1", cache=cache)
        client._client = mock_http

        dest = tmp_path / "cached_mod"
        await client.download_package("cached_mod", "1.0.0", dest)

        # HTTP client should NOT have been called (cache hit).
        mock_http.get.assert_not_called()
        assert (dest / "cached.py").exists()

    async def test_cache_miss_downloads(self, tmp_path, cache):
        tarball = _make_tarball({"fresh.py": b"fresh = True\n"})

        mock_response = MagicMock()
        mock_response.content = tarball
        mock_response.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_response)

        client = HubClient("http://hub.test/api/v1", cache=cache)
        client._client = mock_http

        dest = tmp_path / "fresh_mod"
        await client.download_package("fresh_mod", "1.0.0", dest)

        # HTTP was called (cache miss).
        mock_http.get.assert_called_once()
        # Cache should now have the data.
        assert cache.get("fresh_mod", "1.0.0") is not None

    async def test_path_traversal_rejected(self, tmp_path):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            data = b"evil"
            info = tarfile.TarInfo(name="../../../etc/passwd")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

        mock_response = MagicMock()
        mock_response.content = buf.getvalue()
        mock_response.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_response)

        client = HubClient("http://hub.test/api/v1")
        client._client = mock_http

        dest = tmp_path / "evil_mod"
        with pytest.raises(ValueError, match="traversal"):
            await client.download_package("evil_mod", "1.0.0", dest)

    async def test_download_error(self, tmp_path):
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(side_effect=Exception("Network error"))

        client = HubClient("http://hub.test/api/v1")
        client._client = mock_http

        dest = tmp_path / "error_mod"
        with pytest.raises(Exception, match="Network error"):
            await client.download_package("error_mod", "1.0.0", dest)
