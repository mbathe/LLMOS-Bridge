"""Tests for module rating endpoints (Phase 4)."""

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
    """Create a publisher 'test-pub' and return the API key."""
    _, store, _ = hub_app
    key = generate_api_key()
    await store.create_publisher("test-pub", "Test Publisher", hash_api_key(key), email="test@example.com")
    return key


@pytest.fixture()
async def rated_module(hub_app):
    """Create a module owned by a different publisher so test-pub can rate it."""
    _, store, _ = hub_app
    await store.upsert_module(ModuleRecord(
        module_id="rateable_mod",
        latest_version="1.0.0",
        description="A module to rate",
        publisher_id="other-publisher",
    ))
    return "rateable_mod"


class TestRateModule:
    async def test_rate_module_success(self, client, publisher_key, rated_module):
        resp = await client.post(
            f"/api/v1/modules/{rated_module}/rate",
            json={"stars": 4, "comment": "Great module!"},
            headers={"X-Hub-API-Key": publisher_key},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["stars"] == 4
        assert data["comment"] == "Great module!"
        assert data["average_rating"] == 4.0
        assert data["rating_count"] == 1

    async def test_rate_module_updates_average(self, client, hub_app, rated_module, publisher_key):
        """Two different publishers rate the same module; average is recalculated."""
        _, store, _ = hub_app

        # First rating by test-pub.
        resp1 = await client.post(
            f"/api/v1/modules/{rated_module}/rate",
            json={"stars": 5},
            headers={"X-Hub-API-Key": publisher_key},
        )
        assert resp1.status_code == 200
        assert resp1.json()["average_rating"] == 5.0

        # Create a second publisher.
        key2 = generate_api_key()
        await store.create_publisher("rater-2", "Rater Two", hash_api_key(key2), email="r2@x.com")

        resp2 = await client.post(
            f"/api/v1/modules/{rated_module}/rate",
            json={"stars": 3},
            headers={"X-Hub-API-Key": key2},
        )
        assert resp2.status_code == 200
        data = resp2.json()
        assert data["average_rating"] == 4.0  # (5 + 3) / 2
        assert data["rating_count"] == 2

    async def test_rate_duplicate_upserts(self, client, publisher_key, rated_module):
        """Rating the same module twice by the same publisher upserts (updates)."""
        headers = {"X-Hub-API-Key": publisher_key}

        resp1 = await client.post(
            f"/api/v1/modules/{rated_module}/rate",
            json={"stars": 2, "comment": "Meh"},
            headers=headers,
        )
        assert resp1.status_code == 200
        assert resp1.json()["stars"] == 2

        # Update the rating.
        resp2 = await client.post(
            f"/api/v1/modules/{rated_module}/rate",
            json={"stars": 5, "comment": "Actually great!"},
            headers=headers,
        )
        assert resp2.status_code == 200
        data = resp2.json()
        assert data["stars"] == 5
        assert data["comment"] == "Actually great!"
        # Still only 1 rating (upsert, not insert).
        assert data["rating_count"] == 1
        assert data["average_rating"] == 5.0

    async def test_rate_self_rating_prevented(self, client, hub_app, publisher_key):
        """A publisher cannot rate their own module."""
        _, store, _ = hub_app
        await store.upsert_module(ModuleRecord(
            module_id="my_mod", latest_version="1.0.0", publisher_id="test-pub",
        ))
        resp = await client.post(
            "/api/v1/modules/my_mod/rate",
            json={"stars": 5},
            headers={"X-Hub-API-Key": publisher_key},
        )
        assert resp.status_code == 403

    async def test_rate_invalid_stars(self, client, publisher_key, rated_module):
        resp = await client.post(
            f"/api/v1/modules/{rated_module}/rate",
            json={"stars": 6},
            headers={"X-Hub-API-Key": publisher_key},
        )
        assert resp.status_code == 422

        resp2 = await client.post(
            f"/api/v1/modules/{rated_module}/rate",
            json={"stars": 0},
            headers={"X-Hub-API-Key": publisher_key},
        )
        assert resp2.status_code == 422

    async def test_rate_no_auth(self, client, rated_module):
        resp = await client.post(
            f"/api/v1/modules/{rated_module}/rate",
            json={"stars": 3},
        )
        assert resp.status_code == 401


class TestGetRatings:
    async def test_get_ratings(self, client, hub_app, publisher_key, rated_module):
        _, store, _ = hub_app
        headers = {"X-Hub-API-Key": publisher_key}

        # Add a rating first.
        await client.post(
            f"/api/v1/modules/{rated_module}/rate",
            json={"stars": 4, "comment": "Good"},
            headers=headers,
        )

        resp = await client.get(f"/api/v1/modules/{rated_module}/ratings")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["ratings"][0]["stars"] == 4
        assert data["ratings"][0]["comment"] == "Good"
        assert data["average_rating"] == 4.0

    async def test_get_ratings_empty(self, client, hub_app):
        _, store, _ = hub_app
        await store.upsert_module(ModuleRecord(
            module_id="unrated", latest_version="1.0.0", publisher_id="other",
        ))
        resp = await client.get("/api/v1/modules/unrated/ratings")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["ratings"] == []

    async def test_get_ratings_404(self, client):
        resp = await client.get("/api/v1/modules/nonexistent/ratings")
        assert resp.status_code == 404
