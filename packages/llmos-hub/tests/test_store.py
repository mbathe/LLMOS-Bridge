"""Tests for HubStore — SQLite backend for publishers, modules, versions."""

from __future__ import annotations

import pytest

from llmos_hub.models import ModuleRecord, VersionRecord
from llmos_hub.store import HubStore


@pytest.fixture()
async def store(tmp_path):
    s = HubStore(str(tmp_path / "test.db"))
    await s.init()
    yield s
    await s.close()


class TestPublisher:
    async def test_create_publisher(self, store):
        pub = await store.create_publisher("pub-1", "Alice", "hash_abc")
        assert pub.publisher_id == "pub-1"
        assert pub.name == "Alice"
        assert pub.enabled is True

    async def test_get_publisher_by_key_hash(self, store):
        await store.create_publisher("pub-2", "Bob", "hash_xyz")
        found = await store.get_publisher_by_key_hash("hash_xyz")
        assert found is not None
        assert found.name == "Bob"

    async def test_get_publisher_not_found(self, store):
        assert await store.get_publisher_by_key_hash("nonexistent") is None


class TestModules:
    async def test_upsert_and_get(self, store):
        mod = ModuleRecord(module_id="test_mod", latest_version="1.0.0", description="A test module")
        await store.upsert_module(mod)
        found = await store.get_module("test_mod")
        assert found is not None
        assert found.latest_version == "1.0.0"
        assert found.description == "A test module"

    async def test_upsert_updates_existing(self, store):
        mod1 = ModuleRecord(module_id="mod_a", latest_version="1.0.0")
        await store.upsert_module(mod1)
        mod2 = ModuleRecord(module_id="mod_a", latest_version="2.0.0", description="Updated")
        await store.upsert_module(mod2)
        found = await store.get_module("mod_a")
        assert found.latest_version == "2.0.0"
        assert found.description == "Updated"

    async def test_get_module_not_found(self, store):
        assert await store.get_module("nonexistent") is None

    async def test_search_modules(self, store):
        await store.upsert_module(ModuleRecord(module_id="web_search", latest_version="1.0.0", description="Search the web"))
        await store.upsert_module(ModuleRecord(module_id="file_sync", latest_version="1.0.0", description="Sync files"))
        results = await store.search_modules("web")
        assert len(results) == 1
        assert results[0].module_id == "web_search"

    async def test_search_empty_query(self, store):
        await store.upsert_module(ModuleRecord(module_id="mod_x", latest_version="1.0.0"))
        await store.upsert_module(ModuleRecord(module_id="mod_y", latest_version="1.0.0"))
        results = await store.search_modules("")
        assert len(results) == 2

    async def test_search_with_tags(self, store):
        await store.upsert_module(ModuleRecord(module_id="tagged", latest_version="1.0.0", tags=["web", "api"]))
        await store.upsert_module(ModuleRecord(module_id="untagged", latest_version="1.0.0", tags=[]))
        results = await store.search_modules("", tags=["web"])
        assert len(results) == 1
        assert results[0].module_id == "tagged"

    async def test_increment_downloads(self, store):
        await store.upsert_module(ModuleRecord(module_id="dl_mod", latest_version="1.0.0"))
        await store.increment_downloads("dl_mod")
        await store.increment_downloads("dl_mod")
        mod = await store.get_module("dl_mod")
        assert mod.downloads == 2

    async def test_delete_module(self, store):
        await store.upsert_module(ModuleRecord(module_id="to_delete", latest_version="1.0.0"))
        assert await store.delete_module("to_delete") is True
        assert await store.get_module("to_delete") is None

    async def test_delete_nonexistent(self, store):
        assert await store.delete_module("nope") is False


class TestVersions:
    async def test_add_and_get_versions(self, store):
        await store.add_version(VersionRecord(module_id="mod_v", version="1.0.0", package_path="mod_v/1.0.0/mod_v-1.0.0.tar.gz", checksum="abc", published_at=1000.0))
        await store.add_version(VersionRecord(module_id="mod_v", version="2.0.0", package_path="mod_v/2.0.0/mod_v-2.0.0.tar.gz", checksum="def", published_at=2000.0))
        versions = await store.get_versions("mod_v")
        assert len(versions) == 2
        assert versions[0].version == "2.0.0"  # Newest first

    async def test_get_latest_version(self, store):
        await store.add_version(VersionRecord(module_id="mod_l", version="1.0.0", package_path="p1", checksum="c1", published_at=1000.0))
        await store.add_version(VersionRecord(module_id="mod_l", version="2.0.0", package_path="p2", checksum="c2", published_at=2000.0))
        latest = await store.get_latest_version("mod_l")
        assert latest.version == "2.0.0"

    async def test_get_latest_skips_yanked(self, store):
        await store.add_version(VersionRecord(module_id="mod_y", version="1.0.0", package_path="p1", checksum="c1", published_at=1000.0))
        await store.add_version(VersionRecord(module_id="mod_y", version="2.0.0", package_path="p2", checksum="c2", published_at=2000.0))
        await store.yank_version("mod_y", "2.0.0")
        latest = await store.get_latest_version("mod_y")
        assert latest.version == "1.0.0"

    async def test_yank_version(self, store):
        await store.add_version(VersionRecord(module_id="mod_yank", version="1.0.0", package_path="p", checksum="c", published_at=1000.0))
        assert await store.yank_version("mod_yank", "1.0.0") is True
        versions = await store.get_versions("mod_yank")
        assert versions[0].yanked is True

    async def test_yank_nonexistent(self, store):
        assert await store.yank_version("nope", "1.0.0") is False
