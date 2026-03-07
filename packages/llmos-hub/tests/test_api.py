"""Tests for hub API endpoints."""

from __future__ import annotations

import io
import tarfile
import time
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from llmos_hub.api import create_hub_app
from llmos_hub.auth import generate_api_key, hash_api_key
from llmos_hub.config import HubServerSettings
from llmos_hub.models import ModuleRecord, VersionRecord
from llmos_hub.storage import PackageStorage
from llmos_hub.store import HubStore


@pytest.fixture()
async def hub_app(tmp_path):
    """Create a hub app with a real store and storage for testing."""
    settings = HubServerSettings(
        data_dir=str(tmp_path / "hub"),
        min_publish_score=0,  # Allow low-score modules in tests
    )
    store = HubStore(str(settings.resolved_db_path))
    settings.resolved_data_dir.mkdir(parents=True, exist_ok=True)
    settings.resolved_packages_dir.mkdir(parents=True, exist_ok=True)
    await store.init()

    storage = PackageStorage(settings.resolved_packages_dir)

    app = FastAPI()
    app.state.store = store
    app.state.storage = storage
    app.state.settings = settings

    from llmos_hub.api import _router
    app.include_router(_router, prefix="/api/v1")

    yield app, store, storage

    await store.close()


@pytest.fixture()
async def client(hub_app):
    app, _, _ = hub_app
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as c:
        yield c


@pytest.fixture()
async def publisher_key(hub_app):
    """Create a publisher and return the API key."""
    _, store, _ = hub_app
    key = generate_api_key()
    await store.create_publisher("test-pub", "Test Publisher", hash_api_key(key))
    return key


def _make_tarball(module_id: str = "test_mod", version: str = "1.0.0") -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        files = {
            "llmos-module.toml": f'module_id = "{module_id}"\nversion = "{version}"\ndescription = "Test"\nauthor = "Tester"\nactions = "do_something"\n',
            "module.py": "class Mod:\n    def _action_do_something(self): pass\n",
        }
        for name, content in files.items():
            data = content.encode()
            info = tarfile.TarInfo(name=f"{module_id}/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class TestHealth:
    async def test_health_ok(self, client):
        resp = await client.get("/api/v1/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestSearch:
    async def test_search_empty(self, client):
        resp = await client.get("/api/v1/modules/search", params={"q": "test"})
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    async def test_search_finds_module(self, client, hub_app):
        _, store, _ = hub_app
        await store.upsert_module(ModuleRecord(module_id="searchable", latest_version="1.0.0", description="A searchable module"))
        resp = await client.get("/api/v1/modules/search", params={"q": "searchable"})
        assert resp.status_code == 200
        assert resp.json()["total"] == 1


class TestModuleDetail:
    async def test_get_module(self, client, hub_app):
        _, store, _ = hub_app
        await store.upsert_module(ModuleRecord(module_id="detail_mod", latest_version="2.0.0"))
        resp = await client.get("/api/v1/modules/detail_mod")
        assert resp.status_code == 200
        assert resp.json()["module_id"] == "detail_mod"

    async def test_get_module_404(self, client):
        resp = await client.get("/api/v1/modules/nonexistent")
        assert resp.status_code == 404


class TestDownload:
    async def test_download_module(self, client, hub_app):
        _, store, storage = hub_app
        data = b"fake tarball"
        rel_path, checksum = await storage.save("dl_mod", "1.0.0", data)
        await store.upsert_module(ModuleRecord(module_id="dl_mod", latest_version="1.0.0"))
        await store.add_version(VersionRecord(module_id="dl_mod", version="1.0.0", package_path=rel_path, checksum=checksum, published_at=time.time()))

        resp = await client.get("/api/v1/modules/dl_mod/download", params={"version": "1.0.0"})
        assert resp.status_code == 200
        assert resp.content == data

    async def test_download_404(self, client):
        resp = await client.get("/api/v1/modules/nope/download")
        assert resp.status_code == 404


class TestPublish:
    async def test_publish_success(self, client, publisher_key):
        tarball = _make_tarball("pub_mod", "1.0.0")
        resp = await client.post(
            "/api/v1/modules/publish",
            files={"file": ("pub_mod-1.0.0.tar.gz", tarball, "application/gzip")},
            headers={"X-Hub-API-Key": publisher_key},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["module_id"] == "pub_mod"
        assert data["version"] == "1.0.0"

    async def test_publish_no_auth(self, client):
        tarball = _make_tarball()
        resp = await client.post(
            "/api/v1/modules/publish",
            files={"file": ("test.tar.gz", tarball, "application/gzip")},
        )
        assert resp.status_code == 401

    async def test_publish_bad_key(self, client):
        tarball = _make_tarball()
        resp = await client.post(
            "/api/v1/modules/publish",
            files={"file": ("test.tar.gz", tarball, "application/gzip")},
            headers={"X-Hub-API-Key": "bad_key"},
        )
        assert resp.status_code == 401


class TestVersions:
    async def test_list_versions(self, client, hub_app):
        _, store, _ = hub_app
        await store.upsert_module(ModuleRecord(module_id="ver_mod", latest_version="2.0.0"))
        await store.add_version(VersionRecord(module_id="ver_mod", version="1.0.0", package_path="p1", checksum="c1", published_at=1000.0))
        await store.add_version(VersionRecord(module_id="ver_mod", version="2.0.0", package_path="p2", checksum="c2", published_at=2000.0))

        resp = await client.get("/api/v1/modules/ver_mod/versions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2


class TestDelete:
    async def test_delete_module(self, client, hub_app, publisher_key):
        _, store, _ = hub_app
        await store.upsert_module(ModuleRecord(module_id="del_mod", latest_version="1.0.0", publisher_id="test-pub"))

        resp = await client.request(
            "DELETE",
            "/api/v1/modules/del_mod",
            headers={"X-Hub-API-Key": publisher_key},
        )
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

    async def test_delete_403_wrong_publisher(self, client, hub_app, publisher_key):
        _, store, _ = hub_app
        await store.upsert_module(ModuleRecord(module_id="other_mod", latest_version="1.0.0", publisher_id="someone-else"))

        resp = await client.request(
            "DELETE",
            "/api/v1/modules/other_mod",
            headers={"X-Hub-API-Key": publisher_key},
        )
        assert resp.status_code == 403
