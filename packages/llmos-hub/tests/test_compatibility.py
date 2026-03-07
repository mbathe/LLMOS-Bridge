"""Tests for version compatibility metadata (Phase 4)."""

from __future__ import annotations

import io
import tarfile
import time

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from llmos_hub.auth import generate_api_key, hash_api_key
from llmos_hub.config import HubServerSettings
from llmos_hub.models import ModuleRecord, VersionRecord
from llmos_hub.storage import PackageStorage
from llmos_hub.store import HubStore


@pytest.fixture()
async def hub_app(tmp_path):
    settings = HubServerSettings(
        data_dir=str(tmp_path / "hub"),
        min_publish_score=0,
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
    _, store, _ = hub_app
    key = generate_api_key()
    await store.create_publisher("test-pub", "Test Publisher", hash_api_key(key), email="test@example.com")
    return key


def _make_tarball_with_compat(
    module_id: str = "compat_mod",
    version: str = "1.0.0",
    min_bridge: str = "0.8.0",
    max_bridge: str = "2.0.0",
    python_requires: str = ">=3.11",
) -> bytes:
    """Build a tarball with a [compatibility] section in llmos-module.toml."""
    toml_content = f"""\
module_id = "{module_id}"
version = "{version}"
description = "Compat test"
author = "Tester"
actions = "do_something"

[compatibility]
min_bridge_version = "{min_bridge}"
max_bridge_version = "{max_bridge}"
python_requires = "{python_requires}"
"""
    module_code = "class Mod:\n    def _action_do_something(self): pass\n"

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, content in [("llmos-module.toml", toml_content), ("module.py", module_code)]:
            data = content.encode()
            info = tarfile.TarInfo(name=f"{module_id}/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class TestCompatibility:
    async def test_compatibility_in_version_record(self, hub_app):
        """VersionRecord correctly stores compatibility fields."""
        _, store, _ = hub_app
        ver = VersionRecord(
            module_id="compat_test",
            version="1.0.0",
            package_path="p",
            checksum="c",
            published_at=time.time(),
            min_bridge_version="0.8.0",
            max_bridge_version="2.0.0",
            python_requires=">=3.11",
        )
        await store.add_version(ver)
        versions = await store.get_versions("compat_test")
        assert len(versions) == 1
        v = versions[0]
        assert v.min_bridge_version == "0.8.0"
        assert v.max_bridge_version == "2.0.0"
        assert v.python_requires == ">=3.11"

    async def test_publish_stores_compatibility(self, client, hub_app, publisher_key):
        """Publishing a module with [compatibility] stores the values in the version."""
        _, store, _ = hub_app
        tarball = _make_tarball_with_compat(
            "compat_pub_mod", "1.0.0",
            min_bridge="0.9.0", max_bridge="3.0.0", python_requires=">=3.12",
        )
        resp = await client.post(
            "/api/v1/modules/publish",
            files={"file": ("compat_pub_mod-1.0.0.tar.gz", tarball, "application/gzip")},
            headers={"X-Hub-API-Key": publisher_key},
        )
        assert resp.status_code == 200
        assert resp.json()["module_id"] == "compat_pub_mod"

        # Verify the version record stored the compatibility info.
        versions = await store.get_versions("compat_pub_mod")
        assert len(versions) == 1
        v = versions[0]
        assert v.min_bridge_version == "0.9.0"
        assert v.max_bridge_version == "3.0.0"
        assert v.python_requires == ">=3.12"

    async def test_version_dict_includes_compatibility(self, client, hub_app, publisher_key):
        """The version dict returned by the API includes compatibility fields."""
        _, store, _ = hub_app
        tarball = _make_tarball_with_compat(
            "compat_api_mod", "1.0.0",
            min_bridge="1.0.0", max_bridge="4.0.0", python_requires=">=3.11,<3.13",
        )
        resp = await client.post(
            "/api/v1/modules/publish",
            files={"file": ("compat_api_mod-1.0.0.tar.gz", tarball, "application/gzip")},
            headers={"X-Hub-API-Key": publisher_key},
        )
        assert resp.status_code == 200

        # GET module detail includes 'latest' version with compatibility.
        resp2 = await client.get("/api/v1/modules/compat_api_mod")
        assert resp2.status_code == 200
        data = resp2.json()
        latest = data["latest"]
        assert latest["min_bridge_version"] == "1.0.0"
        assert latest["max_bridge_version"] == "4.0.0"
        assert latest["python_requires"] == ">=3.11,<3.13"

        # Also check via versions list endpoint.
        resp3 = await client.get("/api/v1/modules/compat_api_mod/versions")
        assert resp3.status_code == 200
        ver = resp3.json()["versions"][0]
        assert ver["min_bridge_version"] == "1.0.0"
        assert ver["max_bridge_version"] == "4.0.0"
        assert ver["python_requires"] == ">=3.11,<3.13"
