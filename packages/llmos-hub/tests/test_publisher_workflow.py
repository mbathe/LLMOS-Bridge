"""End-to-end publisher workflow tests (Phase 4).

Tests the complete lifecycle: register -> publish -> search -> rate ->
get ratings -> deprecate -> search (verify hidden) -> search with include_deprecated.
"""

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


def _make_tarball(module_id: str = "test_mod", version: str = "1.0.0") -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        files = {
            "llmos-module.toml": f'module_id = "{module_id}"\nversion = "{version}"\ndescription = "Test module"\nauthor = "Tester"\nactions = "do_something"\n',
            "module.py": "class Mod:\n    def _action_do_something(self): pass\n",
        }
        for name, content in files.items():
            data = content.encode()
            info = tarfile.TarInfo(name=f"{module_id}/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class TestFullWorkflow:
    async def test_full_workflow(self, client, hub_app):
        """Complete publisher lifecycle: register, publish, search, rate, deprecate."""
        _, store, _ = hub_app

        # ── Step 1: Register publisher ──
        reg_resp = await client.post(
            "/api/v1/publishers/register",
            json={"name": "Alice", "email": "alice@example.com", "description": "Module author"},
        )
        assert reg_resp.status_code == 200
        reg_data = reg_resp.json()
        publisher_id = reg_data["publisher_id"]
        api_key = reg_data["api_key"]
        headers = {"X-Hub-API-Key": api_key}

        # ── Step 2: Publish a module ──
        tarball = _make_tarball("workflow_mod", "1.0.0")
        pub_resp = await client.post(
            "/api/v1/modules/publish",
            files={"file": ("workflow_mod-1.0.0.tar.gz", tarball, "application/gzip")},
            headers=headers,
        )
        assert pub_resp.status_code == 200
        assert pub_resp.json()["module_id"] == "workflow_mod"
        assert pub_resp.json()["version"] == "1.0.0"

        # ── Step 3: Search finds the module ──
        search_resp = await client.get("/api/v1/modules/search", params={"q": "workflow"})
        assert search_resp.status_code == 200
        assert search_resp.json()["total"] == 1
        assert search_resp.json()["modules"][0]["module_id"] == "workflow_mod"

        # ── Step 4: Rate the module (needs a different publisher) ──
        rater_key = generate_api_key()
        await store.create_publisher("bob", "Bob", hash_api_key(rater_key), email="bob@x.com")
        rater_headers = {"X-Hub-API-Key": rater_key}

        rate_resp = await client.post(
            "/api/v1/modules/workflow_mod/rate",
            json={"stars": 5, "comment": "Excellent!"},
            headers=rater_headers,
        )
        assert rate_resp.status_code == 200
        assert rate_resp.json()["stars"] == 5
        assert rate_resp.json()["average_rating"] == 5.0

        # ── Step 5: Get ratings ──
        ratings_resp = await client.get("/api/v1/modules/workflow_mod/ratings")
        assert ratings_resp.status_code == 200
        assert ratings_resp.json()["total"] == 1
        assert ratings_resp.json()["average_rating"] == 5.0

        # ── Step 6: Deprecate the module (owner only) ──
        dep_resp = await client.post(
            "/api/v1/modules/workflow_mod/deprecate",
            json={"message": "Use workflow_mod_v2 instead", "replacement_module_id": "workflow_mod_v2"},
            headers=headers,
        )
        assert dep_resp.status_code == 200
        assert dep_resp.json()["deprecated"] is True

        # ── Step 7: Search excludes deprecated by default ──
        search2_resp = await client.get("/api/v1/modules/search", params={"q": "workflow"})
        assert search2_resp.status_code == 200
        assert search2_resp.json()["total"] == 0

        # ── Step 8: Search with include_deprecated finds it ──
        search3_resp = await client.get(
            "/api/v1/modules/search",
            params={"q": "workflow", "include_deprecated": "true"},
        )
        assert search3_resp.status_code == 200
        assert search3_resp.json()["total"] == 1
        assert search3_resp.json()["modules"][0]["deprecated"] is True


class TestPublishAndUpdateWorkflow:
    async def test_register_update_profile_and_rotate_key(self, client):
        """Register, update profile, rotate key, verify new key works."""
        # Register.
        reg = await client.post(
            "/api/v1/publishers/register",
            json={"name": "Charlie", "email": "charlie@example.com"},
        )
        assert reg.status_code == 200
        pub_id = reg.json()["publisher_id"]
        key = reg.json()["api_key"]
        headers = {"X-Hub-API-Key": key}

        # Update profile.
        update = await client.put(
            f"/api/v1/publishers/{pub_id}",
            json={"description": "Updated description", "website": "https://charlie.dev"},
            headers=headers,
        )
        assert update.status_code == 200
        assert update.json()["description"] == "Updated description"
        assert update.json()["website"] == "https://charlie.dev"

        # Rotate key.
        rotate = await client.post(
            f"/api/v1/publishers/{pub_id}/rotate-key",
            headers=headers,
        )
        assert rotate.status_code == 200
        new_key = rotate.json()["new_api_key"]

        # Old key should fail.
        old_update = await client.put(
            f"/api/v1/publishers/{pub_id}",
            json={"name": "Hacker"},
            headers=headers,
        )
        assert old_update.status_code == 401

        # New key should work.
        new_update = await client.put(
            f"/api/v1/publishers/{pub_id}",
            json={"name": "Charlie Updated"},
            headers={"X-Hub-API-Key": new_key},
        )
        assert new_update.status_code == 200
        assert new_update.json()["name"] == "Charlie Updated"


class TestMultiPublisherRating:
    async def test_multiple_publishers_rate_same_module(self, client, hub_app):
        """Multiple publishers rate the same module; average is correct."""
        _, store, _ = hub_app

        # Register owner.
        owner_reg = await client.post(
            "/api/v1/publishers/register",
            json={"name": "Owner", "email": "owner@x.com"},
        )
        owner_key = owner_reg.json()["api_key"]

        # Publish a module.
        tarball = _make_tarball("multi_rate_mod", "1.0.0")
        await client.post(
            "/api/v1/modules/publish",
            files={"file": ("multi_rate_mod-1.0.0.tar.gz", tarball, "application/gzip")},
            headers={"X-Hub-API-Key": owner_key},
        )

        # Register 3 raters and rate.
        ratings_given = [5, 3, 4]
        for i, stars in enumerate(ratings_given):
            reg = await client.post(
                "/api/v1/publishers/register",
                json={"name": f"Rater{i}", "email": f"rater{i}@x.com"},
            )
            resp = await client.post(
                "/api/v1/modules/multi_rate_mod/rate",
                json={"stars": stars},
                headers={"X-Hub-API-Key": reg.json()["api_key"]},
            )
            assert resp.status_code == 200

        # Check final average.
        ratings_resp = await client.get("/api/v1/modules/multi_rate_mod/ratings")
        assert ratings_resp.status_code == 200
        data = ratings_resp.json()
        assert data["total"] == 3
        assert data["average_rating"] == 4.0  # (5 + 3 + 4) / 3


class TestSelfRatingPrevention:
    async def test_owner_cannot_rate_own_module(self, client):
        """A publisher who owns a module cannot rate it."""
        reg = await client.post(
            "/api/v1/publishers/register",
            json={"name": "SelfRater", "email": "self@x.com"},
        )
        key = reg.json()["api_key"]
        headers = {"X-Hub-API-Key": key}

        tarball = _make_tarball("self_mod", "1.0.0")
        await client.post(
            "/api/v1/modules/publish",
            files={"file": ("self_mod-1.0.0.tar.gz", tarball, "application/gzip")},
            headers=headers,
        )

        rate = await client.post(
            "/api/v1/modules/self_mod/rate",
            json={"stars": 5},
            headers=headers,
        )
        assert rate.status_code == 403


class TestCategoryWorkflow:
    async def test_category_lifecycle(self, client, hub_app):
        """Modules with categories show up in /categories and can be filtered."""
        _, store, _ = hub_app
        await store.upsert_module(ModuleRecord(module_id="cat_web", latest_version="1.0.0", category="web"))
        await store.upsert_module(ModuleRecord(module_id="cat_db", latest_version="1.0.0", category="database"))
        await store.upsert_module(ModuleRecord(module_id="cat_web2", latest_version="1.0.0", category="web"))

        # Categories endpoint.
        cats = await client.get("/api/v1/categories")
        assert cats.status_code == 200
        cat_data = cats.json()["categories"]
        web_cat = next(c for c in cat_data if c["name"] == "web")
        assert web_cat["count"] == 2

        # Search filtered by category.
        search = await client.get("/api/v1/modules/search", params={"category": "database"})
        assert search.status_code == 200
        assert search.json()["total"] == 1
        assert search.json()["modules"][0]["module_id"] == "cat_db"


class TestSecurityWorkflow:
    async def test_publish_and_check_security(self, client, hub_app):
        """Publish a clean module and verify security endpoint."""
        _, store, _ = hub_app
        reg = await client.post(
            "/api/v1/publishers/register",
            json={"name": "SecPub", "email": "sec@x.com"},
        )
        key = reg.json()["api_key"]

        tarball = _make_tarball("sec_workflow_mod", "1.0.0")
        pub = await client.post(
            "/api/v1/modules/publish",
            files={"file": ("sec_workflow_mod-1.0.0.tar.gz", tarball, "application/gzip")},
            headers={"X-Hub-API-Key": key},
        )
        assert pub.status_code == 200

        sec = await client.get("/api/v1/modules/sec_workflow_mod/security")
        assert sec.status_code == 200
        data = sec.json()
        assert data["module_id"] == "sec_workflow_mod"
        assert data["latest_version"] == "1.0.0"
        assert data["scan_verdict"] in ("allow", "warn", "")


class TestPublisherModulesWorkflow:
    async def test_publisher_modules_list(self, client):
        """After publishing, the publisher's modules list reflects the published module."""
        reg = await client.post(
            "/api/v1/publishers/register",
            json={"name": "ModListPub", "email": "modlist@x.com"},
        )
        pub_id = reg.json()["publisher_id"]
        key = reg.json()["api_key"]

        # Initially empty.
        resp1 = await client.get(f"/api/v1/publishers/{pub_id}/modules")
        assert resp1.status_code == 200
        assert resp1.json()["total"] == 0

        # Publish a module.
        tarball = _make_tarball("modlist_mod", "1.0.0")
        await client.post(
            "/api/v1/modules/publish",
            files={"file": ("modlist_mod-1.0.0.tar.gz", tarball, "application/gzip")},
            headers={"X-Hub-API-Key": key},
        )

        # Now the list should contain 1 module.
        resp2 = await client.get(f"/api/v1/publishers/{pub_id}/modules")
        assert resp2.status_code == 200
        assert resp2.json()["total"] == 1
        assert resp2.json()["modules"][0]["module_id"] == "modlist_mod"


class TestDeprecateAndSearch:
    async def test_deprecate_then_search_lifecycle(self, client, hub_app):
        """Deprecation lifecycle: create, verify visible, deprecate, verify hidden, include flag."""
        _, store, _ = hub_app
        reg = await client.post(
            "/api/v1/publishers/register",
            json={"name": "DepPub", "email": "dep@x.com"},
        )
        key = reg.json()["api_key"]
        headers = {"X-Hub-API-Key": key}

        tarball = _make_tarball("dep_lifecycle_mod", "1.0.0")
        await client.post(
            "/api/v1/modules/publish",
            files={"file": ("dep_lifecycle_mod-1.0.0.tar.gz", tarball, "application/gzip")},
            headers=headers,
        )

        # Module is visible in search.
        s1 = await client.get("/api/v1/modules/search", params={"q": "dep_lifecycle"})
        assert s1.json()["total"] == 1

        # Deprecate.
        dep = await client.post(
            "/api/v1/modules/dep_lifecycle_mod/deprecate",
            json={"message": "End of life"},
            headers=headers,
        )
        assert dep.status_code == 200

        # Default search excludes it.
        s2 = await client.get("/api/v1/modules/search", params={"q": "dep_lifecycle"})
        assert s2.json()["total"] == 0

        # include_deprecated=true brings it back.
        s3 = await client.get(
            "/api/v1/modules/search",
            params={"q": "dep_lifecycle", "include_deprecated": "true"},
        )
        assert s3.json()["total"] == 1
        assert s3.json()["modules"][0]["deprecated"] is True
