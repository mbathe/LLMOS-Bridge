"""Tests for module deprecation (Phase 4)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from llmos_hub.auth import generate_api_key, hash_api_key
from llmos_hub.config import HubServerSettings
from llmos_hub.models import ModuleRecord
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


class TestDeprecation:
    async def test_deprecate_module(self, client, hub_app, publisher_key):
        _, store, _ = hub_app
        await store.upsert_module(ModuleRecord(
            module_id="old_mod", latest_version="1.0.0", publisher_id="test-pub",
        ))

        resp = await client.post(
            "/api/v1/modules/old_mod/deprecate",
            json={"message": "Use new_mod instead", "replacement_module_id": "new_mod"},
            headers={"X-Hub-API-Key": publisher_key},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["deprecated"] is True
        assert data["message"] == "Use new_mod instead"
        assert data["replacement_module_id"] == "new_mod"

    async def test_deprecate_wrong_publisher(self, client, hub_app, publisher_key):
        """Only the module publisher can deprecate it."""
        _, store, _ = hub_app
        await store.upsert_module(ModuleRecord(
            module_id="someone_else_mod", latest_version="1.0.0", publisher_id="other-publisher",
        ))

        resp = await client.post(
            "/api/v1/modules/someone_else_mod/deprecate",
            json={"message": "deprecated"},
            headers={"X-Hub-API-Key": publisher_key},
        )
        assert resp.status_code == 403

    async def test_search_excludes_deprecated_by_default(self, client, hub_app, publisher_key):
        _, store, _ = hub_app
        await store.upsert_module(ModuleRecord(module_id="active_mod", latest_version="1.0.0"))
        await store.upsert_module(ModuleRecord(
            module_id="dep_mod", latest_version="1.0.0", publisher_id="test-pub",
        ))
        # Deprecate dep_mod.
        await store.deprecate_module("dep_mod", "Deprecated")

        resp = await client.get("/api/v1/modules/search")
        assert resp.status_code == 200
        data = resp.json()
        ids = [m["module_id"] for m in data["modules"]]
        assert "active_mod" in ids
        assert "dep_mod" not in ids

    async def test_search_includes_deprecated_when_requested(self, client, hub_app, publisher_key):
        _, store, _ = hub_app
        await store.upsert_module(ModuleRecord(module_id="active2", latest_version="1.0.0"))
        await store.upsert_module(ModuleRecord(
            module_id="dep_mod2", latest_version="1.0.0", publisher_id="test-pub",
        ))
        await store.deprecate_module("dep_mod2", "Old module")

        resp = await client.get(
            "/api/v1/modules/search",
            params={"include_deprecated": "true"},
        )
        assert resp.status_code == 200
        ids = [m["module_id"] for m in resp.json()["modules"]]
        assert "active2" in ids
        assert "dep_mod2" in ids

    async def test_deprecation_includes_replacement(self, client, hub_app, publisher_key):
        """When viewing a deprecated module, the response includes deprecation info."""
        _, store, _ = hub_app
        await store.upsert_module(ModuleRecord(
            module_id="replaced_mod", latest_version="1.0.0", publisher_id="test-pub",
        ))
        await store.upsert_module(ModuleRecord(
            module_id="replacement_mod", latest_version="2.0.0",
        ))
        await store.deprecate_module("replaced_mod", "Replaced by replacement_mod", "replacement_mod")

        resp = await client.get("/api/v1/modules/replaced_mod")
        assert resp.status_code == 200
        data = resp.json()
        assert data["deprecated"] is True
        assert data["deprecated_message"] == "Replaced by replacement_mod"
        assert data["replacement_module_id"] == "replacement_mod"
