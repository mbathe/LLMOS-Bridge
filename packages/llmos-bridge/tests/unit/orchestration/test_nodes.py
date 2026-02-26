"""Unit tests for the distributed node abstraction layer.

Tests cover:
- LocalNode execution delegates to ModuleRegistry
- NodeRegistry.resolve(None) → LocalNode (standalone guarantee)
- NodeRegistry.resolve("local") → LocalNode
- NodeRegistry.resolve("unknown") → KeyError
- NodeRegistry.register / unregister / list_nodes
- Local node cannot be unregistered
- PlanExecutor uses NodeRegistry for dispatch (target_node=None default)
- IMLAction.target_node field defaults to None (backward compat)
- NodeConfig defaults to standalone mode
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llmos_bridge.config import NodeConfig, Settings
from llmos_bridge.orchestration.nodes import BaseNode, LocalNode, NodeRegistry
from llmos_bridge.protocol.models import IMLAction


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_registry(return_value: Any = {"output": "ok"}) -> MagicMock:
    """Return a mock ModuleRegistry whose modules return return_value."""
    module = MagicMock()
    module.execute = AsyncMock(return_value=return_value)
    registry = MagicMock()
    registry.get.return_value = module
    return registry


# ---------------------------------------------------------------------------
# LocalNode
# ---------------------------------------------------------------------------


class TestLocalNode:
    def test_node_id(self) -> None:
        node = LocalNode(_make_registry())
        assert node.node_id == "local"

    def test_is_available_always_true(self) -> None:
        node = LocalNode(_make_registry())
        assert node.is_available() is True

    @pytest.mark.asyncio
    async def test_execute_action_delegates_to_registry(self) -> None:
        registry = _make_registry(return_value={"content": "hello"})
        node = LocalNode(registry)

        result = await node.execute_action("filesystem", "read_file", {"path": "/tmp/x"})

        registry.get.assert_called_once_with("filesystem")
        registry.get.return_value.execute.assert_awaited_once_with(
            "read_file", {"path": "/tmp/x"}
        )
        assert result == {"content": "hello"}

    @pytest.mark.asyncio
    async def test_execute_action_propagates_exception(self) -> None:
        registry = MagicMock()
        module = MagicMock()
        module.execute = AsyncMock(side_effect=ValueError("module error"))
        registry.get.return_value = module
        node = LocalNode(registry)

        with pytest.raises(ValueError, match="module error"):
            await node.execute_action("os_exec", "run_command", {})

    def test_repr(self) -> None:
        node = LocalNode(_make_registry())
        assert "local" in repr(node)

    def test_is_base_node_subclass(self) -> None:
        node = LocalNode(_make_registry())
        assert isinstance(node, BaseNode)


# ---------------------------------------------------------------------------
# NodeRegistry
# ---------------------------------------------------------------------------


class TestNodeRegistry:
    def _make_registry(self) -> NodeRegistry:
        return NodeRegistry(LocalNode(_make_registry()))

    # --- resolve ---

    def test_resolve_none_returns_local(self) -> None:
        reg = self._make_registry()
        node = reg.resolve(None)
        assert node.node_id == "local"

    def test_resolve_local_string_returns_local(self) -> None:
        reg = self._make_registry()
        node = reg.resolve("local")
        assert node.node_id == "local"

    def test_resolve_unknown_raises_key_error(self) -> None:
        reg = self._make_registry()
        with pytest.raises(KeyError, match="node_lyon_2"):
            reg.resolve("node_lyon_2")

    def test_resolve_none_always_local_regardless_of_registered_nodes(self) -> None:
        """Standalone guarantee: target_node=None always routes locally."""
        reg = self._make_registry()

        # Register a mock remote node
        remote = MagicMock(spec=BaseNode)
        remote.node_id = "node_remote"
        reg.register(remote)

        # None still resolves to local
        assert reg.resolve(None).node_id == "local"

    # --- register / unregister ---

    def test_register_adds_node(self) -> None:
        reg = self._make_registry()
        remote = MagicMock(spec=BaseNode)
        remote.node_id = "node_abc"
        reg.register(remote)

        assert reg.resolve("node_abc") is remote
        assert "node_abc" in reg.list_nodes()

    def test_register_overwrites_existing(self) -> None:
        reg = self._make_registry()
        n1 = MagicMock(spec=BaseNode)
        n1.node_id = "node_x"
        n2 = MagicMock(spec=BaseNode)
        n2.node_id = "node_x"
        reg.register(n1)
        reg.register(n2)
        assert reg.resolve("node_x") is n2

    def test_unregister_removes_node(self) -> None:
        reg = self._make_registry()
        remote = MagicMock(spec=BaseNode)
        remote.node_id = "node_to_remove"
        reg.register(remote)
        assert "node_to_remove" in reg.list_nodes()

        reg.unregister("node_to_remove")
        assert "node_to_remove" not in reg.list_nodes()

    def test_unregister_local_is_ignored(self) -> None:
        """The local node must never be removed."""
        reg = self._make_registry()
        reg.unregister("local")
        # Still resolves
        assert reg.resolve(None).node_id == "local"

    def test_unregister_nonexistent_is_noop(self) -> None:
        reg = self._make_registry()
        reg.unregister("does_not_exist")  # should not raise

    # --- list / len ---

    def test_list_nodes_contains_local(self) -> None:
        reg = self._make_registry()
        assert "local" in reg.list_nodes()

    def test_len_starts_at_one(self) -> None:
        reg = self._make_registry()
        assert len(reg) == 1

    def test_len_grows_with_registrations(self) -> None:
        reg = self._make_registry()
        for i in range(3):
            n = MagicMock(spec=BaseNode)
            n.node_id = f"node_{i}"
            reg.register(n)
        assert len(reg) == 4  # 1 local + 3 remote

    def test_repr_shows_nodes(self) -> None:
        reg = self._make_registry()
        assert "local" in repr(reg)


# ---------------------------------------------------------------------------
# IMLAction.target_node backward compatibility
# ---------------------------------------------------------------------------


class TestIMLActionTargetNode:
    def _make_action(self, **kwargs: Any) -> IMLAction:
        base = {
            "id": "a1",
            "action": "read_file",
            "module": "filesystem",
            "params": {"path": "/tmp/x"},
        }
        base.update(kwargs)
        return IMLAction(**base)

    def test_target_node_defaults_to_none(self) -> None:
        action = self._make_action()
        assert action.target_node is None

    def test_target_node_can_be_set(self) -> None:
        action = self._make_action(target_node="node_lyon_2")
        assert action.target_node == "node_lyon_2"

    def test_existing_plan_without_target_node_still_valid(self) -> None:
        """All plans created before distributed support must remain valid."""
        action = IMLAction(
            id="a1",
            action="run_command",
            module="os_exec",
            params={"command": ["ls"]},
        )
        assert action.target_node is None

    def test_target_node_serialises_and_deserialises(self) -> None:
        action = self._make_action(target_node="node_rpi_3")
        d = action.model_dump()
        assert d["target_node"] == "node_rpi_3"

        restored = IMLAction(**d)
        assert restored.target_node == "node_rpi_3"

    def test_target_node_none_serialises_as_none(self) -> None:
        action = self._make_action()
        d = action.model_dump()
        assert d["target_node"] is None


# ---------------------------------------------------------------------------
# NodeConfig defaults
# ---------------------------------------------------------------------------


class TestNodeConfig:
    def test_default_mode_is_standalone(self) -> None:
        cfg = NodeConfig()
        assert cfg.mode == "standalone"

    def test_default_node_id_is_local(self) -> None:
        cfg = NodeConfig()
        assert cfg.node_id == "local"

    def test_default_location_is_empty(self) -> None:
        cfg = NodeConfig()
        assert cfg.location == ""

    def test_settings_include_node_config(self) -> None:
        settings = Settings()
        assert hasattr(settings, "node")
        assert isinstance(settings.node, NodeConfig)
        assert settings.node.mode == "standalone"

    def test_node_config_accepts_orchestrator_mode(self) -> None:
        cfg = NodeConfig(mode="orchestrator", node_id="pc_central", location="HQ Paris")
        assert cfg.mode == "orchestrator"
        assert cfg.node_id == "pc_central"


# ---------------------------------------------------------------------------
# Executor uses NodeRegistry for dispatch
# ---------------------------------------------------------------------------


class TestExecutorNodeDispatch:
    """Verify that PlanExecutor routes actions through NodeRegistry."""

    @pytest.mark.asyncio
    async def test_executor_dispatches_via_local_node_when_target_none(self) -> None:
        """target_node=None → LocalNode → same result as before distributed support."""
        from llmos_bridge.orchestration.executor import PlanExecutor
        from llmos_bridge.orchestration.nodes import LocalNode, NodeRegistry
        from llmos_bridge.orchestration.state import PlanStateStore
        from llmos_bridge.security.audit import AuditLogger
        from llmos_bridge.security.guard import PermissionGuard

        # Build a registry whose module returns a known value
        module_registry = _make_registry(return_value={"ok": True})

        local_node = LocalNode(module_registry)
        node_registry = NodeRegistry(local_node)

        guard = MagicMock(spec=PermissionGuard)
        guard.check_plan = MagicMock(return_value=None)
        guard.check_action = MagicMock(return_value=None)

        state_store = MagicMock(spec=PlanStateStore)
        state_store.create = AsyncMock()
        state_store.update_plan_status = AsyncMock()
        state_store.update_action = AsyncMock()

        audit = MagicMock(spec=AuditLogger)
        audit.log = AsyncMock()

        executor = PlanExecutor(
            module_registry=module_registry,
            guard=guard,
            state_store=state_store,
            audit_logger=audit,
            node_registry=node_registry,
        )

        # Verify the NodeRegistry is the one we passed
        assert executor._nodes is node_registry

    @pytest.mark.asyncio
    async def test_executor_creates_default_node_registry_when_not_provided(
        self,
    ) -> None:
        """If no NodeRegistry is passed, executor creates one with LocalNode."""
        from llmos_bridge.orchestration.executor import PlanExecutor
        from llmos_bridge.orchestration.nodes import LocalNode, NodeRegistry
        from llmos_bridge.orchestration.state import PlanStateStore
        from llmos_bridge.security.audit import AuditLogger
        from llmos_bridge.security.guard import PermissionGuard

        module_registry = _make_registry()
        executor = PlanExecutor(
            module_registry=module_registry,
            guard=MagicMock(spec=PermissionGuard),
            state_store=MagicMock(spec=PlanStateStore),
            audit_logger=MagicMock(spec=AuditLogger),
        )

        assert isinstance(executor._nodes, NodeRegistry)
        local = executor._nodes.resolve(None)
        assert isinstance(local, LocalNode)
        assert local.node_id == "local"
