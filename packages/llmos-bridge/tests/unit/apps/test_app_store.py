"""Tests for AppStore — SQLite persistence for LLMOS applications."""

import pytest

from llmos_bridge.apps.app_store import AppStore, AppRecord, AppStatus


@pytest.fixture
async def store(tmp_path):
    s = AppStore(tmp_path / "apps.db")
    await s.init()
    yield s
    await s.close()


async def _register_test_app(store, app_id="app1", name="test-app", version="1.0"):
    return await store.register(
        app_id=app_id,
        name=name,
        version=version,
        file_path="/tmp/test.app.yaml",
        description="A test app",
        author="tester",
        tags=["test", "demo"],
    )


class TestRegister:
    @pytest.mark.asyncio
    async def test_register_new(self, store):
        record = await _register_test_app(store)
        assert record.id == "app1"
        assert record.name == "test-app"
        assert record.version == "1.0"
        assert record.status == AppStatus.registered
        assert record.description == "A test app"
        assert record.author == "tester"
        assert record.tags == ["test", "demo"]

    @pytest.mark.asyncio
    async def test_register_upsert(self, store):
        await _register_test_app(store, name="old-name")
        record = await _register_test_app(store, name="new-name")
        assert record.name == "new-name"

    @pytest.mark.asyncio
    async def test_register_multiple(self, store):
        await _register_test_app(store, app_id="a1", name="app-one")
        await _register_test_app(store, app_id="a2", name="app-two")
        apps = await store.list_apps()
        assert len(apps) == 2


class TestGet:
    @pytest.mark.asyncio
    async def test_get_by_id(self, store):
        await _register_test_app(store)
        record = await store.get("app1")
        assert record is not None
        assert record.name == "test-app"

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, store):
        record = await store.get("nonexistent")
        assert record is None

    @pytest.mark.asyncio
    async def test_get_by_name(self, store):
        await _register_test_app(store)
        record = await store.get_by_name("test-app")
        assert record is not None
        assert record.id == "app1"

    @pytest.mark.asyncio
    async def test_get_by_name_nonexistent(self, store):
        record = await store.get_by_name("nope")
        assert record is None


class TestListApps:
    @pytest.mark.asyncio
    async def test_list_all(self, store):
        await _register_test_app(store, app_id="a1", name="one")
        await _register_test_app(store, app_id="a2", name="two")
        apps = await store.list_apps()
        assert len(apps) == 2

    @pytest.mark.asyncio
    async def test_list_empty(self, store):
        apps = await store.list_apps()
        assert apps == []

    @pytest.mark.asyncio
    async def test_list_by_status(self, store):
        await _register_test_app(store, app_id="a1")
        await _register_test_app(store, app_id="a2")
        await store.update_status("a1", AppStatus.running)
        apps = await store.list_apps(status=AppStatus.running)
        assert len(apps) == 1
        assert apps[0].id == "a1"

    @pytest.mark.asyncio
    async def test_list_by_tag(self, store):
        await store.register(
            app_id="a1", name="one", version="1.0",
            file_path="/tmp/a.yaml", tags=["web"],
        )
        await store.register(
            app_id="a2", name="two", version="1.0",
            file_path="/tmp/b.yaml", tags=["cli"],
        )
        apps = await store.list_apps(tag="web")
        assert len(apps) == 1
        assert apps[0].id == "a1"


class TestUpdateStatus:
    @pytest.mark.asyncio
    async def test_update_status(self, store):
        await _register_test_app(store)
        result = await store.update_status("app1", AppStatus.running)
        assert result is True
        record = await store.get("app1")
        assert record.status == AppStatus.running

    @pytest.mark.asyncio
    async def test_update_status_with_error(self, store):
        await _register_test_app(store)
        await store.update_status("app1", AppStatus.error, "Something broke")
        record = await store.get("app1")
        assert record.status == AppStatus.error
        assert record.error_message == "Something broke"

    @pytest.mark.asyncio
    async def test_update_nonexistent(self, store):
        result = await store.update_status("nonexistent", AppStatus.running)
        assert result is False


class TestRecordRun:
    @pytest.mark.asyncio
    async def test_record_run(self, store):
        await _register_test_app(store)
        await store.record_run("app1")
        record = await store.get("app1")
        assert record.run_count == 1
        assert record.last_run_at > 0

    @pytest.mark.asyncio
    async def test_multiple_runs(self, store):
        await _register_test_app(store)
        await store.record_run("app1")
        await store.record_run("app1")
        await store.record_run("app1")
        record = await store.get("app1")
        assert record.run_count == 3


class TestDelete:
    @pytest.mark.asyncio
    async def test_delete(self, store):
        await _register_test_app(store)
        result = await store.delete("app1")
        assert result is True
        assert await store.get("app1") is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, store):
        result = await store.delete("nonexistent")
        assert result is False


class TestToDict:
    @pytest.mark.asyncio
    async def test_to_dict(self, store):
        await _register_test_app(store)
        record = await store.get("app1")
        d = record.to_dict()
        assert d["id"] == "app1"
        assert d["name"] == "test-app"
        assert d["status"] == "registered"
        assert d["tags"] == ["test", "demo"]
        assert "created_at" in d
        assert "updated_at" in d
