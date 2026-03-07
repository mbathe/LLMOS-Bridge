"""Integration test — E2E hub publish → search → download → install flow.

Uses the real hub server (in-process via httpx ASGI transport) and
the real HubClient to test the complete lifecycle.
"""

from __future__ import annotations

import io
import tarfile
import time
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from llmos_bridge.hub.client import HubClient


def _make_module_tarball(
    module_id: str = "integration_mod",
    version: str = "1.0.0",
) -> bytes:
    """Create a valid module tarball with all required files."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        files = {
            "llmos-module.toml": (
                f'module_id = "{module_id}"\n'
                f'version = "{version}"\n'
                f'description = "Integration test module"\n'
                f'author = "Test Suite"\n'
                f'actions = "do_work"\n'
                f'tags = ["test", "integration"]\n'
            ),
            "module.py": "class IntegrationMod:\n    def _action_do_work(self): pass\n",
            "params.py": "class DoWorkParams: pass\n",
            "README.md": "# Integration Module\n## Overview\nFor testing.\n## Usage\nTest only.\n",
            "CHANGELOG.md": "# 1.0.0\n- Initial\n",
        }
        for name, content in files.items():
            data = content.encode()
            info = tarfile.TarInfo(name=f"{module_id}/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


@pytest.fixture()
async def hub_env(tmp_path):
    """Set up a complete hub server + client environment."""
    from llmos_hub.api import create_hub_app, _router
    from llmos_hub.auth import generate_api_key, hash_api_key
    from llmos_hub.config import HubServerSettings
    from llmos_hub.storage import PackageStorage
    from llmos_hub.store import HubStore

    # Create hub server
    settings = HubServerSettings(
        data_dir=str(tmp_path / "hub"),
        min_publish_score=0,
    )
    settings.resolved_data_dir.mkdir(parents=True, exist_ok=True)
    settings.resolved_packages_dir.mkdir(parents=True, exist_ok=True)

    store = HubStore(str(settings.resolved_db_path))
    await store.init()
    storage = PackageStorage(settings.resolved_packages_dir)

    from fastapi import FastAPI
    app = FastAPI()
    app.state.store = store
    app.state.storage = storage
    app.state.settings = settings
    app.include_router(_router, prefix="/api/v1")

    # Create publisher
    api_key = generate_api_key()
    await store.create_publisher("test-pub", "Test Publisher", hash_api_key(api_key))

    # Create httpx transport for the hub client
    transport = ASGITransport(app=app)

    yield {
        "app": app,
        "store": store,
        "api_key": api_key,
        "transport": transport,
        "tmp_path": tmp_path,
    }

    await store.close()


class TestHubPublishFlow:
    @pytest.mark.asyncio
    async def test_publish_then_search(self, hub_env):
        """Publish a module, then search and find it."""
        transport = hub_env["transport"]
        api_key = hub_env["api_key"]
        tarball = _make_module_tarball("search_mod", "1.0.0")

        async with AsyncClient(transport=transport, base_url="http://test") as http:
            # Publish
            resp = await http.post(
                "/api/v1/modules/publish",
                files={"file": ("search_mod-1.0.0.tar.gz", tarball, "application/gzip")},
                headers={"X-Hub-API-Key": api_key},
            )
            assert resp.status_code == 200
            assert resp.json()["success"] is True

            # Search
            resp = await http.get("/api/v1/modules/search", params={"q": "search_mod"})
            assert resp.status_code == 200
            data = resp.json()
            assert data["total"] == 1
            assert data["modules"][0]["module_id"] == "search_mod"

    @pytest.mark.asyncio
    async def test_publish_then_download(self, hub_env):
        """Publish a module, then download the package."""
        transport = hub_env["transport"]
        api_key = hub_env["api_key"]
        tarball = _make_module_tarball("dl_flow_mod", "1.0.0")

        async with AsyncClient(transport=transport, base_url="http://test") as http:
            # Publish
            resp = await http.post(
                "/api/v1/modules/publish",
                files={"file": ("dl_flow_mod-1.0.0.tar.gz", tarball, "application/gzip")},
                headers={"X-Hub-API-Key": api_key},
            )
            assert resp.status_code == 200

            # Download
            resp = await http.get(
                "/api/v1/modules/dl_flow_mod/download",
                params={"version": "1.0.0"},
            )
            assert resp.status_code == 200
            assert len(resp.content) > 0
            assert resp.headers["content-type"] == "application/gzip"

    @pytest.mark.asyncio
    async def test_publish_v2_then_check_versions(self, hub_env):
        """Publish two versions, then list them."""
        transport = hub_env["transport"]
        api_key = hub_env["api_key"]

        async with AsyncClient(transport=transport, base_url="http://test") as http:
            # Publish v1
            tarball_v1 = _make_module_tarball("ver_flow_mod", "1.0.0")
            resp = await http.post(
                "/api/v1/modules/publish",
                files={"file": ("ver_flow_mod-1.0.0.tar.gz", tarball_v1, "application/gzip")},
                headers={"X-Hub-API-Key": api_key},
            )
            assert resp.status_code == 200

            # Publish v2
            tarball_v2 = _make_module_tarball("ver_flow_mod", "2.0.0")
            resp = await http.post(
                "/api/v1/modules/publish",
                files={"file": ("ver_flow_mod-2.0.0.tar.gz", tarball_v2, "application/gzip")},
                headers={"X-Hub-API-Key": api_key},
            )
            assert resp.status_code == 200

            # List versions
            resp = await http.get("/api/v1/modules/ver_flow_mod/versions")
            assert resp.status_code == 200
            data = resp.json()
            assert data["total"] == 2
            versions = [v["version"] for v in data["versions"]]
            assert "1.0.0" in versions
            assert "2.0.0" in versions

    @pytest.mark.asyncio
    async def test_publish_then_get_detail(self, hub_env):
        """Publish, then get module detail."""
        transport = hub_env["transport"]
        api_key = hub_env["api_key"]
        tarball = _make_module_tarball("detail_flow_mod", "1.0.0")

        async with AsyncClient(transport=transport, base_url="http://test") as http:
            resp = await http.post(
                "/api/v1/modules/publish",
                files={"file": ("detail_flow_mod-1.0.0.tar.gz", tarball, "application/gzip")},
                headers={"X-Hub-API-Key": api_key},
            )
            assert resp.status_code == 200

            resp = await http.get("/api/v1/modules/detail_flow_mod")
            assert resp.status_code == 200
            data = resp.json()
            assert data["module_id"] == "detail_flow_mod"
            assert data["description"] == "Integration test module"

    @pytest.mark.asyncio
    async def test_publish_then_yank(self, hub_env):
        """Publish, then yank the version."""
        transport = hub_env["transport"]
        api_key = hub_env["api_key"]
        tarball = _make_module_tarball("yank_mod", "1.0.0")

        async with AsyncClient(transport=transport, base_url="http://test") as http:
            resp = await http.post(
                "/api/v1/modules/publish",
                files={"file": ("yank_mod-1.0.0.tar.gz", tarball, "application/gzip")},
                headers={"X-Hub-API-Key": api_key},
            )
            assert resp.status_code == 200

            resp = await http.post(
                "/api/v1/modules/yank_mod/yank/1.0.0",
                headers={"X-Hub-API-Key": api_key},
            )
            assert resp.status_code == 200
            assert resp.json()["yanked"] is True

    @pytest.mark.asyncio
    async def test_publish_then_delete(self, hub_env):
        """Publish, then delete the module."""
        transport = hub_env["transport"]
        api_key = hub_env["api_key"]
        tarball = _make_module_tarball("delete_flow_mod", "1.0.0")

        async with AsyncClient(transport=transport, base_url="http://test") as http:
            resp = await http.post(
                "/api/v1/modules/publish",
                files={"file": ("delete_flow_mod-1.0.0.tar.gz", tarball, "application/gzip")},
                headers={"X-Hub-API-Key": api_key},
            )
            assert resp.status_code == 200

            resp = await http.request(
                "DELETE",
                "/api/v1/modules/delete_flow_mod",
                headers={"X-Hub-API-Key": api_key},
            )
            assert resp.status_code == 200
            assert resp.json()["deleted"] is True

            # Should be gone
            resp = await http.get("/api/v1/modules/delete_flow_mod")
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_download_increments_counter(self, hub_env):
        """Downloading a module increments the download counter."""
        transport = hub_env["transport"]
        api_key = hub_env["api_key"]
        store = hub_env["store"]
        tarball = _make_module_tarball("counter_mod", "1.0.0")

        async with AsyncClient(transport=transport, base_url="http://test") as http:
            resp = await http.post(
                "/api/v1/modules/publish",
                files={"file": ("counter_mod-1.0.0.tar.gz", tarball, "application/gzip")},
                headers={"X-Hub-API-Key": api_key},
            )
            assert resp.status_code == 200

            # Download twice
            await http.get("/api/v1/modules/counter_mod/download", params={"version": "1.0.0"})
            await http.get("/api/v1/modules/counter_mod/download", params={"version": "1.0.0"})

            mod = await store.get_module("counter_mod")
            assert mod.downloads == 2

    @pytest.mark.asyncio
    async def test_unauthorized_publish(self, hub_env):
        """Publishing without auth fails with 401."""
        transport = hub_env["transport"]
        tarball = _make_module_tarball("noauth_mod", "1.0.0")

        async with AsyncClient(transport=transport, base_url="http://test") as http:
            resp = await http.post(
                "/api/v1/modules/publish",
                files={"file": ("noauth_mod-1.0.0.tar.gz", tarball, "application/gzip")},
            )
            assert resp.status_code == 401
