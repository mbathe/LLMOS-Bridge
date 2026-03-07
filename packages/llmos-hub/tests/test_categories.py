"""Tests for category listing and search-by-category / sort-by endpoints (Phase 4)."""

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


class TestGetCategories:
    async def test_get_categories_empty(self, client):
        resp = await client.get("/api/v1/categories")
        assert resp.status_code == 200
        data = resp.json()
        assert data["categories"] == []

    async def test_get_categories_with_modules(self, client, hub_app):
        _, store, _ = hub_app
        await store.upsert_module(ModuleRecord(module_id="web1", latest_version="1.0.0", category="web"))
        await store.upsert_module(ModuleRecord(module_id="web2", latest_version="1.0.0", category="web"))
        await store.upsert_module(ModuleRecord(module_id="db1", latest_version="1.0.0", category="database"))
        # Module with no category should not appear.
        await store.upsert_module(ModuleRecord(module_id="misc", latest_version="1.0.0", category=""))

        resp = await client.get("/api/v1/categories")
        assert resp.status_code == 200
        cats = resp.json()["categories"]
        # Should be sorted by count desc: web(2) > database(1).
        assert len(cats) == 2
        assert cats[0]["name"] == "web"
        assert cats[0]["count"] == 2
        assert cats[1]["name"] == "database"
        assert cats[1]["count"] == 1


class TestSearchByCategory:
    async def test_search_by_category(self, client, hub_app):
        _, store, _ = hub_app
        await store.upsert_module(ModuleRecord(module_id="web_mod", latest_version="1.0.0", category="web"))
        await store.upsert_module(ModuleRecord(module_id="db_mod", latest_version="1.0.0", category="database"))

        resp = await client.get("/api/v1/modules/search", params={"category": "web"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["modules"][0]["module_id"] == "web_mod"


class TestSearchSortBy:
    async def test_search_sort_by_rating(self, client, hub_app):
        """Modules should be sorted by average_rating DESC when sort_by=rating."""
        _, store, _ = hub_app
        # Insert modules with different ratings.
        m1 = ModuleRecord(module_id="low_rated", latest_version="1.0.0")
        m2 = ModuleRecord(module_id="high_rated", latest_version="1.0.0")
        await store.upsert_module(m1)
        await store.upsert_module(m2)

        # Create two publishers to rate.
        key_a = generate_api_key()
        key_b = generate_api_key()
        await store.create_publisher("rater_a", "Rater A", hash_api_key(key_a), email="a@x.com")
        await store.create_publisher("rater_b", "Rater B", hash_api_key(key_b), email="b@x.com")

        # Rate via store directly.
        await store.add_rating("low_rated", "rater_a", 2)
        await store.add_rating("high_rated", "rater_b", 5)

        resp = await client.get("/api/v1/modules/search", params={"sort_by": "rating"})
        assert resp.status_code == 200
        modules = resp.json()["modules"]
        assert len(modules) == 2
        assert modules[0]["module_id"] == "high_rated"
        assert modules[1]["module_id"] == "low_rated"

    async def test_search_sort_by_newest(self, client, hub_app):
        """Modules should be sorted by updated_at DESC when sort_by=newest."""
        _, store, _ = hub_app
        # Insert old module first, then new module.
        await store.upsert_module(ModuleRecord(module_id="old_mod", latest_version="1.0.0"))
        await store.upsert_module(ModuleRecord(module_id="new_mod", latest_version="1.0.0"))

        resp = await client.get("/api/v1/modules/search", params={"sort_by": "newest"})
        assert resp.status_code == 200
        modules = resp.json()["modules"]
        assert len(modules) == 2
        # new_mod was inserted last, so it has a later updated_at.
        assert modules[0]["module_id"] == "new_mod"
