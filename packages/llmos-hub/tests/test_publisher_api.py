"""Tests for publisher API endpoints (Phase 4)."""

from __future__ import annotations

import io
import tarfile

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
    """Create a hub app with a real store and storage for testing."""
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
    """Create a publisher and return (api_key, publisher_id)."""
    _, store, _ = hub_app
    key = generate_api_key()
    pub = await store.create_publisher("test-pub", "Test Publisher", hash_api_key(key), email="test@example.com")
    return key, pub.publisher_id


class TestRegisterPublisher:
    async def test_register_publisher_success(self, client):
        resp = await client.post(
            "/api/v1/publishers/register",
            json={"name": "Alice", "email": "alice@example.com", "description": "A publisher", "website": "https://alice.dev"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "publisher_id" in data
        assert data["name"] == "Alice"
        assert "api_key" in data
        assert data["api_key"].startswith("llmos_hub_")

    async def test_register_missing_name(self, client):
        resp = await client.post(
            "/api/v1/publishers/register",
            json={"email": "no-name@example.com"},
        )
        assert resp.status_code == 422

    async def test_register_missing_email(self, client):
        resp = await client.post(
            "/api/v1/publishers/register",
            json={"name": "Bob"},
        )
        assert resp.status_code == 422


class TestGetPublisher:
    async def test_get_publisher_profile(self, client, publisher_key):
        _, pub_id = publisher_key
        resp = await client.get(f"/api/v1/publishers/{pub_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["publisher_id"] == pub_id
        assert data["name"] == "Test Publisher"
        assert data["email"] == "test@example.com"

    async def test_get_publisher_404(self, client):
        resp = await client.get("/api/v1/publishers/nonexistent-id")
        assert resp.status_code == 404

    async def test_publisher_profile_hides_key_hash(self, client, publisher_key):
        _, pub_id = publisher_key
        resp = await client.get(f"/api/v1/publishers/{pub_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert "api_key_hash" not in data
        assert "api_key" not in data


class TestListPublisherModules:
    async def test_list_publisher_modules(self, client, hub_app, publisher_key):
        _, store, _ = hub_app
        _, pub_id = publisher_key
        await store.upsert_module(ModuleRecord(module_id="pub_mod_1", latest_version="1.0.0", publisher_id=pub_id))
        await store.upsert_module(ModuleRecord(module_id="pub_mod_2", latest_version="2.0.0", publisher_id=pub_id))

        resp = await client.get(f"/api/v1/publishers/{pub_id}/modules")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        ids = {m["module_id"] for m in data["modules"]}
        assert ids == {"pub_mod_1", "pub_mod_2"}

    async def test_list_publisher_modules_empty(self, client, publisher_key):
        _, pub_id = publisher_key
        resp = await client.get(f"/api/v1/publishers/{pub_id}/modules")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["modules"] == []


class TestUpdatePublisher:
    async def test_update_publisher_profile(self, client, publisher_key):
        key, pub_id = publisher_key
        resp = await client.put(
            f"/api/v1/publishers/{pub_id}",
            json={"name": "Updated Name", "website": "https://updated.dev"},
            headers={"X-Hub-API-Key": key},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Updated Name"
        assert data["website"] == "https://updated.dev"

    async def test_update_publisher_wrong_key(self, client, hub_app, publisher_key):
        """A publisher cannot update another publisher's profile."""
        _, store, _ = hub_app
        key, _ = publisher_key

        # Create a second publisher.
        other_key = generate_api_key()
        other_pub = await store.create_publisher(
            "other-pub", "Other Publisher", hash_api_key(other_key), email="other@example.com",
        )

        # Try to update other-pub using test-pub's key.
        resp = await client.put(
            f"/api/v1/publishers/{other_pub.publisher_id}",
            json={"name": "Hacked"},
            headers={"X-Hub-API-Key": key},
        )
        assert resp.status_code == 403


class TestRotateKey:
    async def test_rotate_key_success(self, client, publisher_key):
        key, pub_id = publisher_key
        resp = await client.post(
            f"/api/v1/publishers/{pub_id}/rotate-key",
            headers={"X-Hub-API-Key": key},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["publisher_id"] == pub_id
        assert "new_api_key" in data
        assert data["new_api_key"] != key

        # Old key should no longer work.
        resp2 = await client.post(
            f"/api/v1/publishers/{pub_id}/rotate-key",
            headers={"X-Hub-API-Key": key},
        )
        assert resp2.status_code == 401

        # New key should work.
        resp3 = await client.post(
            f"/api/v1/publishers/{pub_id}/rotate-key",
            headers={"X-Hub-API-Key": data["new_api_key"]},
        )
        assert resp3.status_code == 200

    async def test_rotate_key_wrong_publisher(self, client, hub_app, publisher_key):
        """A publisher cannot rotate another publisher's key."""
        _, store, _ = hub_app
        key, _ = publisher_key

        other_key = generate_api_key()
        other_pub = await store.create_publisher(
            "other-pub-2", "Other", hash_api_key(other_key), email="other@x.com",
        )

        resp = await client.post(
            f"/api/v1/publishers/{other_pub.publisher_id}/rotate-key",
            headers={"X-Hub-API-Key": key},
        )
        assert resp.status_code == 403
