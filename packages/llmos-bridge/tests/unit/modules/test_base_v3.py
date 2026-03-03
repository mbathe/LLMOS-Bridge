"""Tests for Module Spec v3 — BaseModule interface enrichment.

Covers:
  - ResourceEstimate and ModulePolicy dataclasses
  - New lifecycle hooks: restore_state, on_install, on_update, on_resource_pressure
  - Resource awareness: estimate_cost
  - Policy and introspection: policy_rules, describe
  - Dynamic action registration: register_action, unregister_action, _get_handler
"""

from __future__ import annotations

import pytest

from llmos_bridge.exceptions import ActionNotFoundError
from llmos_bridge.modules.base import (
    BaseModule,
    ModulePolicy,
    ResourceEstimate,
)
from llmos_bridge.modules.manifest import ActionSpec, ModuleManifest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

class StubModule(BaseModule):
    MODULE_ID = "stub"
    VERSION = "1.0.0"

    def get_manifest(self) -> ModuleManifest:
        return ModuleManifest(
            module_id=self.MODULE_ID,
            version=self.VERSION,
            description="Stub module for testing",
        )

    async def _action_hello(self, params: dict) -> dict:
        return {"greeting": f"Hello, {params.get('name', 'world')}!"}


class OverriddenModule(BaseModule):
    """Module that overrides all v3 methods."""

    MODULE_ID = "overridden"
    VERSION = "2.0.0"

    def __init__(self) -> None:
        super().__init__()
        self._restored_state: dict | None = None
        self._installed = False
        self._updated_from: str | None = None
        self._pressure_level: str | None = None

    def get_manifest(self) -> ModuleManifest:
        return ModuleManifest(
            module_id=self.MODULE_ID,
            version=self.VERSION,
            description="Overridden module",
        )

    async def restore_state(self, state: dict) -> None:
        self._restored_state = state

    async def on_install(self) -> None:
        self._installed = True

    async def on_update(self, old_version: str) -> None:
        self._updated_from = old_version

    async def on_resource_pressure(self, level: str) -> None:
        self._pressure_level = level

    async def estimate_cost(self, action: str, params: dict) -> ResourceEstimate:
        return ResourceEstimate(
            estimated_duration_seconds=5.0,
            estimated_memory_mb=512.0,
            estimated_cpu_percent=80.0,
            estimated_io_operations=10,
            confidence=0.9,
        )

    def policy_rules(self) -> ModulePolicy:
        return ModulePolicy(
            max_parallel_calls=3,
            cooldown_seconds=1.0,
            allow_remote_invocation=False,
            execution_timeout=30.0,
            max_memory_mb=1024,
            retry_on_failure=True,
        )

    def describe(self) -> dict:
        return {
            "module_id": self.MODULE_ID,
            "active_connections": 5,
            "loaded_model": "gpt-4",
        }


# ---------------------------------------------------------------------------
# ResourceEstimate
# ---------------------------------------------------------------------------

class TestResourceEstimate:
    def test_defaults(self):
        r = ResourceEstimate()
        assert r.estimated_duration_seconds == 0.0
        assert r.estimated_memory_mb == 0.0
        assert r.estimated_cpu_percent == 0.0
        assert r.estimated_io_operations == 0
        assert r.confidence == 0.5

    def test_custom_values(self):
        r = ResourceEstimate(
            estimated_duration_seconds=10.0,
            estimated_memory_mb=256.0,
            estimated_cpu_percent=50.0,
            estimated_io_operations=100,
            confidence=0.8,
        )
        assert r.estimated_duration_seconds == 10.0
        assert r.estimated_memory_mb == 256.0
        assert r.estimated_cpu_percent == 50.0
        assert r.estimated_io_operations == 100
        assert r.confidence == 0.8


# ---------------------------------------------------------------------------
# ModulePolicy
# ---------------------------------------------------------------------------

class TestModulePolicy:
    def test_defaults(self):
        p = ModulePolicy()
        assert p.max_parallel_calls == 0
        assert p.cooldown_seconds == 0.0
        assert p.allow_remote_invocation is True
        assert p.execution_timeout == 0.0
        assert p.max_memory_mb == 0
        assert p.retry_on_failure is False

    def test_custom_values(self):
        p = ModulePolicy(
            max_parallel_calls=5,
            cooldown_seconds=2.0,
            allow_remote_invocation=False,
            execution_timeout=60.0,
            max_memory_mb=2048,
            retry_on_failure=True,
        )
        assert p.max_parallel_calls == 5
        assert p.cooldown_seconds == 2.0
        assert p.allow_remote_invocation is False
        assert p.execution_timeout == 60.0
        assert p.max_memory_mb == 2048
        assert p.retry_on_failure is True


# ---------------------------------------------------------------------------
# Default no-op lifecycle hooks (v3)
# ---------------------------------------------------------------------------

class TestDefaultHooks:
    @pytest.fixture()
    def mod(self) -> StubModule:
        return StubModule()

    @pytest.mark.asyncio
    async def test_restore_state_noop(self, mod):
        result = await mod.restore_state({"key": "value"})
        assert result is None

    @pytest.mark.asyncio
    async def test_on_install_noop(self, mod):
        result = await mod.on_install()
        assert result is None

    @pytest.mark.asyncio
    async def test_on_update_noop(self, mod):
        result = await mod.on_update("0.9.0")
        assert result is None

    @pytest.mark.asyncio
    async def test_on_resource_pressure_noop(self, mod):
        result = await mod.on_resource_pressure("warning")
        assert result is None

    @pytest.mark.asyncio
    async def test_estimate_cost_default(self, mod):
        estimate = await mod.estimate_cost("hello", {"name": "test"})
        assert isinstance(estimate, ResourceEstimate)
        assert estimate.confidence == 0.5
        assert estimate.estimated_duration_seconds == 0.0

    def test_policy_rules_default(self, mod):
        policy = mod.policy_rules()
        assert isinstance(policy, ModulePolicy)
        assert policy.max_parallel_calls == 0
        assert policy.cooldown_seconds == 0.0

    def test_describe_default_returns_manifest_dict(self, mod):
        desc = mod.describe()
        assert desc["module_id"] == "stub"
        assert desc["version"] == "1.0.0"
        assert "actions" in desc


# ---------------------------------------------------------------------------
# Overridden v3 hooks
# ---------------------------------------------------------------------------

class TestOverriddenHooks:
    @pytest.fixture()
    def mod(self) -> OverriddenModule:
        return OverriddenModule()

    @pytest.mark.asyncio
    async def test_restore_state(self, mod):
        await mod.restore_state({"sessions": [1, 2]})
        assert mod._restored_state == {"sessions": [1, 2]}

    @pytest.mark.asyncio
    async def test_on_install(self, mod):
        assert not mod._installed
        await mod.on_install()
        assert mod._installed

    @pytest.mark.asyncio
    async def test_on_update(self, mod):
        await mod.on_update("1.5.0")
        assert mod._updated_from == "1.5.0"

    @pytest.mark.asyncio
    async def test_on_resource_pressure(self, mod):
        await mod.on_resource_pressure("critical")
        assert mod._pressure_level == "critical"

    @pytest.mark.asyncio
    async def test_estimate_cost_override(self, mod):
        estimate = await mod.estimate_cost("compute", {})
        assert estimate.estimated_duration_seconds == 5.0
        assert estimate.estimated_memory_mb == 512.0
        assert estimate.confidence == 0.9

    def test_policy_rules_override(self, mod):
        policy = mod.policy_rules()
        assert policy.max_parallel_calls == 3
        assert policy.cooldown_seconds == 1.0
        assert policy.allow_remote_invocation is False
        assert policy.retry_on_failure is True

    def test_describe_override(self, mod):
        desc = mod.describe()
        assert desc["module_id"] == "overridden"
        assert desc["active_connections"] == 5
        assert desc["loaded_model"] == "gpt-4"


# ---------------------------------------------------------------------------
# Dynamic action registration
# ---------------------------------------------------------------------------

class TestDynamicActions:
    @pytest.fixture()
    def mod(self) -> StubModule:
        return StubModule()

    def test_initial_dynamic_actions_empty(self, mod):
        assert mod._dynamic_actions == {}
        assert mod._dynamic_specs == {}

    def test_register_action_without_spec(self, mod):
        async def my_handler(params):
            return {"result": "dynamic"}

        mod.register_action("dynamic_action", my_handler)
        assert "dynamic_action" in mod._dynamic_actions
        assert "dynamic_action" not in mod._dynamic_specs

    def test_register_action_with_spec(self, mod):
        async def my_handler(params):
            return {"result": "dynamic"}

        spec = ActionSpec(name="dynamic_action", description="A dynamic action")
        mod.register_action("dynamic_action", my_handler, spec=spec)
        assert "dynamic_action" in mod._dynamic_actions
        assert "dynamic_action" in mod._dynamic_specs
        assert mod._dynamic_specs["dynamic_action"].description == "A dynamic action"

    def test_unregister_action(self, mod):
        async def my_handler(params):
            pass

        spec = ActionSpec(name="temp", description="Temporary")
        mod.register_action("temp", my_handler, spec=spec)
        assert "temp" in mod._dynamic_actions
        assert "temp" in mod._dynamic_specs

        mod.unregister_action("temp")
        assert "temp" not in mod._dynamic_actions
        assert "temp" not in mod._dynamic_specs

    def test_unregister_nonexistent_action(self, mod):
        # Should not raise
        mod.unregister_action("nonexistent")

    @pytest.mark.asyncio
    async def test_dynamic_action_dispatched(self, mod):
        async def my_handler(params):
            return {"dynamic": True, "input": params.get("x")}

        mod.register_action("compute", my_handler)
        result = await mod.execute("compute", {"x": 42})
        assert result == {"dynamic": True, "input": 42}

    @pytest.mark.asyncio
    async def test_dynamic_action_overrides_static(self, mod):
        """Dynamic actions take precedence over _action_ methods."""

        async def override_handler(params):
            return {"overridden": True}

        mod.register_action("hello", override_handler)
        result = await mod.execute("hello", {"name": "test"})
        assert result == {"overridden": True}

    @pytest.mark.asyncio
    async def test_static_action_after_unregister(self, mod):
        """After unregistering a dynamic action, the static one is used."""

        async def override_handler(params):
            return {"overridden": True}

        mod.register_action("hello", override_handler)
        mod.unregister_action("hello")
        result = await mod.execute("hello", {"name": "world"})
        assert result == {"greeting": "Hello, world!"}

    def test_get_handler_dynamic_priority(self, mod):
        async def handler(params):
            pass

        mod.register_action("hello", handler)
        assert mod._get_handler("hello") is handler

    def test_get_handler_falls_back_to_static(self, mod):
        handler = mod._get_handler("hello")
        assert handler is not None
        assert callable(handler)

    def test_get_handler_not_found(self, mod):
        with pytest.raises(ActionNotFoundError):
            mod._get_handler("nonexistent_action")

    @pytest.mark.asyncio
    async def test_multiple_dynamic_actions(self, mod):
        async def handler_a(params):
            return "a"

        async def handler_b(params):
            return "b"

        mod.register_action("action_a", handler_a)
        mod.register_action("action_b", handler_b)

        assert await mod.execute("action_a", {}) == "a"
        assert await mod.execute("action_b", {}) == "b"

    @pytest.mark.asyncio
    async def test_replace_dynamic_action(self, mod):
        async def handler_v1(params):
            return "v1"

        async def handler_v2(params):
            return "v2"

        mod.register_action("evolve", handler_v1)
        assert await mod.execute("evolve", {}) == "v1"

        mod.register_action("evolve", handler_v2)
        assert await mod.execute("evolve", {}) == "v2"
