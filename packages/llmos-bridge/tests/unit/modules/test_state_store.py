"""Tests for Module Spec v3 — Module state persistence.

Tests the ModuleStateStore (SQLite) and the save/restore flow
integrated into ModuleLifecycleManager.
"""

from __future__ import annotations

from typing import Any

import pytest

from llmos_bridge.modules.state_store import ModuleStateStore


# ---------------------------------------------------------------------------
# ModuleStateStore CRUD tests
# ---------------------------------------------------------------------------


class TestModuleStateStore:
    @pytest.fixture()
    async def store(self, tmp_path):
        s = ModuleStateStore(tmp_path / "module_state_test.db")
        await s.init()
        yield s
        await s.close()

    @pytest.mark.asyncio
    async def test_save_and_load(self, store):
        await store.save("test_mod", {"key": "value", "count": 42})
        loaded = await store.load("test_mod")
        assert loaded == {"key": "value", "count": 42}

    @pytest.mark.asyncio
    async def test_load_nonexistent(self, store):
        result = await store.load("ghost")
        assert result is None

    @pytest.mark.asyncio
    async def test_save_upsert(self, store):
        await store.save("mod_a", {"version": 1})
        await store.save("mod_a", {"version": 2})
        loaded = await store.load("mod_a")
        assert loaded == {"version": 2}

    @pytest.mark.asyncio
    async def test_delete(self, store):
        await store.save("mod_b", {"data": True})
        await store.delete("mod_b")
        assert await store.load("mod_b") is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, store):
        # Should not raise.
        await store.delete("nonexistent")

    @pytest.mark.asyncio
    async def test_list_all(self, store):
        await store.save("alpha", {"a": 1})
        await store.save("beta", {"b": 2})
        await store.save("gamma", {"c": 3})
        ids = await store.list_all()
        assert sorted(ids) == ["alpha", "beta", "gamma"]

    @pytest.mark.asyncio
    async def test_list_all_empty(self, store):
        ids = await store.list_all()
        assert ids == []

    @pytest.mark.asyncio
    async def test_save_complex_state(self, store):
        state = {
            "sessions": {"s1": {"user": "alice"}},
            "loaded_models": ["gpt-4", "llama"],
            "connections": 3,
            "nested": {"a": {"b": {"c": True}}},
        }
        await store.save("complex", state)
        loaded = await store.load("complex")
        assert loaded == state

    @pytest.mark.asyncio
    async def test_save_empty_state(self, store):
        await store.save("empty", {})
        # Empty dict is "falsy" but should still round-trip.
        loaded = await store.load("empty")
        assert loaded == {}


# ---------------------------------------------------------------------------
# Lifecycle integration: save/restore flow
# ---------------------------------------------------------------------------


class TestLifecycleStateRestore:
    @pytest.mark.asyncio
    async def test_state_saved_on_stop(self, tmp_path):
        from llmos_bridge.events.bus import NullEventBus
        from llmos_bridge.modules.base import BaseModule, Platform
        from llmos_bridge.modules.lifecycle import ModuleLifecycleManager
        from llmos_bridge.modules.manifest import ModuleManifest
        from llmos_bridge.modules.registry import ModuleRegistry

        class StatefulModule(BaseModule):
            MODULE_ID = "stateful"
            VERSION = "1.0.0"
            SUPPORTED_PLATFORMS = [Platform.ALL]

            def __init__(self):
                super().__init__()
                self._counter = 0

            def state_snapshot(self) -> dict[str, Any]:
                return {"counter": self._counter}

            async def restore_state(self, state: dict[str, Any]) -> None:
                self._counter = state.get("counter", 0)

            def get_manifest(self) -> ModuleManifest:
                return ModuleManifest(
                    module_id=self.MODULE_ID,
                    version=self.VERSION,
                    description="Stateful module for testing",
                )

        store = ModuleStateStore(tmp_path / "state.db")
        await store.init()
        bus = NullEventBus()
        registry = ModuleRegistry()

        module = StatefulModule()
        module._counter = 42
        registry.register_instance(module)

        lifecycle = ModuleLifecycleManager(registry, bus, state_store=store)
        await lifecycle.start_module("stateful")
        await lifecycle.stop_module("stateful")

        # Verify state was saved.
        saved = await store.load("stateful")
        assert saved == {"counter": 42}

        await store.close()

    @pytest.mark.asyncio
    async def test_state_restored_on_start(self, tmp_path):
        from llmos_bridge.events.bus import NullEventBus
        from llmos_bridge.modules.base import BaseModule, Platform
        from llmos_bridge.modules.lifecycle import ModuleLifecycleManager
        from llmos_bridge.modules.manifest import ModuleManifest
        from llmos_bridge.modules.registry import ModuleRegistry

        class StatefulModule(BaseModule):
            MODULE_ID = "restorable"
            VERSION = "1.0.0"
            SUPPORTED_PLATFORMS = [Platform.ALL]

            def __init__(self):
                super().__init__()
                self._counter = 0
                self._restored = False

            def state_snapshot(self) -> dict[str, Any]:
                return {"counter": self._counter}

            async def restore_state(self, state: dict[str, Any]) -> None:
                self._counter = state.get("counter", 0)
                self._restored = True

            def get_manifest(self) -> ModuleManifest:
                return ModuleManifest(
                    module_id=self.MODULE_ID,
                    version=self.VERSION,
                    description="Restorable module",
                )

        store = ModuleStateStore(tmp_path / "state.db")
        await store.init()

        # Pre-seed state.
        await store.save("restorable", {"counter": 99})

        bus = NullEventBus()
        registry = ModuleRegistry()
        module = StatefulModule()
        registry.register_instance(module)

        lifecycle = ModuleLifecycleManager(registry, bus, state_store=store)
        await lifecycle.start_module("restorable")

        assert module._counter == 99
        assert module._restored is True

        await store.close()

    @pytest.mark.asyncio
    async def test_no_restore_without_saved_state(self, tmp_path):
        from llmos_bridge.events.bus import NullEventBus
        from llmos_bridge.modules.base import BaseModule, Platform
        from llmos_bridge.modules.lifecycle import ModuleLifecycleManager
        from llmos_bridge.modules.manifest import ModuleManifest
        from llmos_bridge.modules.registry import ModuleRegistry

        class SimpleModule(BaseModule):
            MODULE_ID = "simple"
            VERSION = "1.0.0"
            SUPPORTED_PLATFORMS = [Platform.ALL]
            restored = False

            async def restore_state(self, state: dict[str, Any]) -> None:
                self.restored = True

            def get_manifest(self) -> ModuleManifest:
                return ModuleManifest(
                    module_id=self.MODULE_ID,
                    version=self.VERSION,
                    description="Simple module",
                )

        store = ModuleStateStore(tmp_path / "state.db")
        await store.init()

        bus = NullEventBus()
        registry = ModuleRegistry()
        module = SimpleModule()
        registry.register_instance(module)

        lifecycle = ModuleLifecycleManager(registry, bus, state_store=store)
        await lifecycle.start_module("simple")

        assert module.restored is False

        await store.close()

    @pytest.mark.asyncio
    async def test_no_save_for_empty_snapshot(self, tmp_path):
        from llmos_bridge.events.bus import NullEventBus
        from llmos_bridge.modules.base import BaseModule, Platform
        from llmos_bridge.modules.lifecycle import ModuleLifecycleManager
        from llmos_bridge.modules.manifest import ModuleManifest
        from llmos_bridge.modules.registry import ModuleRegistry

        class NoStateModule(BaseModule):
            MODULE_ID = "no_state"
            VERSION = "1.0.0"
            SUPPORTED_PLATFORMS = [Platform.ALL]

            def get_manifest(self) -> ModuleManifest:
                return ModuleManifest(
                    module_id=self.MODULE_ID,
                    version=self.VERSION,
                    description="No state module",
                )

        store = ModuleStateStore(tmp_path / "state.db")
        await store.init()

        bus = NullEventBus()
        registry = ModuleRegistry()
        module = NoStateModule()
        registry.register_instance(module)

        lifecycle = ModuleLifecycleManager(registry, bus, state_store=store)
        await lifecycle.start_module("no_state")
        await lifecycle.stop_module("no_state")

        # Empty snapshot should not be saved.
        saved = await store.load("no_state")
        assert saved is None

        await store.close()
