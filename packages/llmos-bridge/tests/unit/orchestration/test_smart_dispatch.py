"""Unit tests — Smart dispatch / resolve_for_action (Phase 4).

Tests cover:
- resolve_for_action: explicit target, auto-route, quarantine exclusion, fallback
- Node-level fallback: retry on alternate nodes, max_retries
- Load tracking: increment/decrement around dispatch
- Standalone mode: no router = always local
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from llmos_bridge.exceptions import NodeUnreachableError
from llmos_bridge.orchestration.nodes import BaseNode, LocalNode, NodeRegistry
from llmos_bridge.orchestration.routing import (
    ActiveActionCounter,
    CapabilityRouter,
    NodeQuarantine,
    NodeSelector,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_local_node(modules: list[str] | None = None) -> MagicMock:
    """Create a mock LocalNode."""
    node = MagicMock(spec=LocalNode)
    type(node).node_id = PropertyMock(return_value="local")
    node.is_available.return_value = True
    mock_registry = MagicMock()
    mock_registry.is_available = lambda m: m in (modules or ["filesystem"])
    node._registry = mock_registry
    node.__class__ = LocalNode
    return node


def _make_remote_node(
    node_id: str, caps: list[str], available: bool = True
) -> MagicMock:
    """Create a mock RemoteNode."""
    node = MagicMock()
    type(node).node_id = PropertyMock(return_value=node_id)
    node.is_available.return_value = available
    node._capabilities = caps
    node.execute_action = AsyncMock(return_value={"ok": True})
    return node


def _make_registry(
    local_modules: list[str],
    remotes: dict[str, tuple[list[str], bool]] | None = None,
) -> NodeRegistry:
    """Build a real NodeRegistry with mock nodes."""
    local = _make_local_node(local_modules)
    registry = NodeRegistry(local)  # type: ignore[arg-type]
    if remotes:
        for nid, (caps, avail) in remotes.items():
            registry.register(_make_remote_node(nid, caps, avail))
    return registry


# ---------------------------------------------------------------------------
# resolve_for_action
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestResolveForAction:
    def test_explicit_target_direct_lookup(self) -> None:
        registry = _make_registry(["fs"], {"node-2": (["fs"], True)})
        node = registry.resolve_for_action("node-2", "fs")
        assert node.node_id == "node-2"

    def test_explicit_target_unknown_raises(self) -> None:
        registry = _make_registry(["fs"])
        with pytest.raises(KeyError):
            registry.resolve_for_action("nonexistent", "fs")

    def test_no_router_returns_local(self) -> None:
        """Standalone mode — no router means always local."""
        registry = _make_registry(["fs"])
        node = registry.resolve_for_action(None, "fs")
        assert node.node_id == "local"

    def test_auto_route_local_first(self) -> None:
        registry = _make_registry(
            ["filesystem"],
            {"node-2": (["filesystem"], True)},
        )
        router = CapabilityRouter(registry)
        selector = NodeSelector("local_first")

        node = registry.resolve_for_action(
            None, "filesystem", router=router, selector=selector,
        )
        assert node.node_id == "local"

    def test_auto_route_to_remote(self) -> None:
        """Module only available on remote — should route there."""
        registry = _make_registry(
            ["filesystem"],
            {"gpu-node": (["vision"], True)},
        )
        router = CapabilityRouter(registry)
        selector = NodeSelector("local_first")

        node = registry.resolve_for_action(
            None, "vision", router=router, selector=selector,
        )
        assert node.node_id == "gpu-node"

    def test_auto_route_excludes_quarantined(self) -> None:
        registry = _make_registry(
            [],
            {
                "node-1": (["vision"], True),
                "node-2": (["vision"], True),
            },
        )
        router = CapabilityRouter(registry)
        selector = NodeSelector("local_first")
        quarantine = NodeQuarantine(threshold=1, duration=60.0)
        quarantine.record_failure("node-1")

        node = registry.resolve_for_action(
            None, "vision",
            router=router, selector=selector, quarantine=quarantine,
        )
        assert node.node_id == "node-2"

    def test_auto_route_no_capable_fallback_local(self) -> None:
        """No capable node found — fallback to local."""
        registry = _make_registry(["filesystem"])
        router = CapabilityRouter(registry)
        selector = NodeSelector("local_first")

        node = registry.resolve_for_action(
            None, "nonexistent_module", router=router, selector=selector,
        )
        assert node.node_id == "local"

    def test_auto_route_all_quarantined_fallback_local(self) -> None:
        registry = _make_registry(
            [],
            {"node-1": (["vision"], True)},
        )
        router = CapabilityRouter(registry)
        selector = NodeSelector("local_first")
        quarantine = NodeQuarantine(threshold=1, duration=60.0)
        quarantine.record_failure("node-1")

        node = registry.resolve_for_action(
            None, "vision",
            router=router, selector=selector, quarantine=quarantine,
        )
        assert node.node_id == "local"

    def test_auto_route_round_robin(self) -> None:
        registry = _make_registry(
            [],
            {
                "n1": (["fs"], True),
                "n2": (["fs"], True),
            },
        )
        router = CapabilityRouter(registry)
        selector = NodeSelector("round_robin")

        ids = []
        for _ in range(4):
            node = registry.resolve_for_action(
                None, "fs", router=router, selector=selector,
            )
            ids.append(node.node_id)
        # Should cycle through the two nodes.
        assert ids[0] != ids[1]  # Different nodes
        assert ids[0] == ids[2]  # Cycle repeats

    def test_auto_route_least_loaded(self) -> None:
        registry = _make_registry(
            [],
            {
                "n1": (["fs"], True),
                "n2": (["fs"], True),
            },
        )
        router = CapabilityRouter(registry)
        selector = NodeSelector("least_loaded")
        tracker = ActiveActionCounter()
        tracker.increment("n1")
        tracker.increment("n1")

        node = registry.resolve_for_action(
            None, "fs",
            router=router, selector=selector, load_tracker=tracker,
        )
        assert node.node_id == "n2"

    def test_auto_route_with_selector_none(self) -> None:
        """No selector → first candidate."""
        registry = _make_registry(
            ["filesystem"],
            {"n2": (["filesystem"], True)},
        )
        router = CapabilityRouter(registry)

        node = registry.resolve_for_action(
            None, "filesystem", router=router,
        )
        assert node is not None


# ---------------------------------------------------------------------------
# Executor node fallback (integration-like via _dispatch_with_node_fallback)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNodeFallbackDispatch:
    @pytest.mark.asyncio
    async def test_dispatch_with_node_fallback_succeeds(self) -> None:
        """After primary node fails, fallback to an alternate node."""
        from llmos_bridge.orchestration.executor import PlanExecutor
        from llmos_bridge.protocol.models import IMLAction

        # Build mock nodes.
        local = _make_local_node([])
        remote1 = _make_remote_node("n1", ["vision"])
        remote1.execute_action = AsyncMock(
            side_effect=NodeUnreachableError("n1", "down"),
        )
        remote2 = _make_remote_node("n2", ["vision"])
        remote2.execute_action = AsyncMock(return_value={"result": "ok"})

        registry = NodeRegistry(local)  # type: ignore[arg-type]
        registry.register(remote1)
        registry.register(remote2)

        # Build routing components.
        from llmos_bridge.config import RoutingConfig

        cfg = RoutingConfig(strategy="round_robin", max_node_retries=2)

        # Build minimal executor (only needs routing components).
        executor = PlanExecutor.__new__(PlanExecutor)
        executor._nodes = registry
        executor._routing_config = cfg
        executor._fallback_chains = {}

        from llmos_bridge.orchestration.routing import (
            ActiveActionCounter,
            CapabilityRouter,
            NodeQuarantine,
            NodeSelector,
        )

        executor._router = CapabilityRouter(registry)
        executor._selector = NodeSelector(cfg.strategy)
        executor._quarantine = NodeQuarantine(cfg.quarantine_threshold, cfg.quarantine_duration)
        executor._load_tracker = ActiveActionCounter()

        action = MagicMock(spec=IMLAction)
        action.module = "vision"
        action.action = "capture"
        action.target_node = None

        result = await executor._dispatch_with_node_fallback(
            action, {"foo": "bar"}, exclude=["n1"],
        )
        assert result == {"result": "ok"}
        remote2.execute_action.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_dispatch_with_node_fallback_all_fail(self) -> None:
        """All fallback nodes fail → raises NodeUnreachableError."""
        from llmos_bridge.orchestration.executor import PlanExecutor
        from llmos_bridge.protocol.models import IMLAction

        local = _make_local_node([])
        remote1 = _make_remote_node("n1", ["vision"])
        remote1.execute_action = AsyncMock(
            side_effect=NodeUnreachableError("n1", "down"),
        )

        registry = NodeRegistry(local)  # type: ignore[arg-type]
        registry.register(remote1)

        from llmos_bridge.config import RoutingConfig

        cfg = RoutingConfig(strategy="round_robin", max_node_retries=2)

        executor = PlanExecutor.__new__(PlanExecutor)
        executor._nodes = registry
        executor._routing_config = cfg
        executor._fallback_chains = {}

        from llmos_bridge.orchestration.routing import (
            ActiveActionCounter,
            CapabilityRouter,
            NodeQuarantine,
            NodeSelector,
        )

        executor._router = CapabilityRouter(registry)
        executor._selector = NodeSelector(cfg.strategy)
        executor._quarantine = NodeQuarantine(cfg.quarantine_threshold, cfg.quarantine_duration)
        executor._load_tracker = ActiveActionCounter()

        action = MagicMock(spec=IMLAction)
        action.module = "vision"
        action.action = "capture"
        action.target_node = None

        with pytest.raises(NodeUnreachableError):
            await executor._dispatch_with_node_fallback(
                action, {}, exclude=["nonexistent"],
            )

    @pytest.mark.asyncio
    async def test_dispatch_with_node_fallback_no_router(self) -> None:
        """No router → raises immediately."""
        from llmos_bridge.orchestration.executor import PlanExecutor
        from llmos_bridge.protocol.models import IMLAction

        executor = PlanExecutor.__new__(PlanExecutor)
        executor._router = None
        executor._routing_config = None
        executor._quarantine = None
        executor._load_tracker = None

        action = MagicMock(spec=IMLAction)
        action.module = "fs"
        action.action = "read_file"

        with pytest.raises(NodeUnreachableError):
            await executor._dispatch_with_node_fallback(action, {}, exclude=["n1"])


# ---------------------------------------------------------------------------
# Load tracking integration
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLoadTracking:
    def test_resolve_node_uses_routing(self) -> None:
        """_resolve_node should delegate to resolve_for_action."""
        from llmos_bridge.orchestration.executor import PlanExecutor
        from llmos_bridge.protocol.models import IMLAction

        mock_nodes = MagicMock()
        mock_nodes.resolve_for_action.return_value = MagicMock(node_id="gpu-node")

        executor = PlanExecutor.__new__(PlanExecutor)
        executor._nodes = mock_nodes
        executor._router = MagicMock()
        executor._selector = MagicMock()
        executor._quarantine = MagicMock()
        executor._load_tracker = MagicMock()

        action = MagicMock(spec=IMLAction)
        action.target_node = None
        action.module = "vision"

        node = executor._resolve_node(action)
        assert node.node_id == "gpu-node"
        mock_nodes.resolve_for_action.assert_called_once()

    def test_resolve_node_explicit_target(self) -> None:
        """Explicit target_node → passed through to resolve_for_action."""
        from llmos_bridge.orchestration.executor import PlanExecutor
        from llmos_bridge.protocol.models import IMLAction

        mock_nodes = MagicMock()
        mock_nodes.resolve_for_action.return_value = MagicMock(node_id="n2")

        executor = PlanExecutor.__new__(PlanExecutor)
        executor._nodes = mock_nodes
        executor._router = None
        executor._selector = None
        executor._quarantine = None
        executor._load_tracker = None

        action = MagicMock(spec=IMLAction)
        action.target_node = "n2"
        action.module = "fs"

        node = executor._resolve_node(action)
        mock_nodes.resolve_for_action.assert_called_once_with(
            target="n2",
            module_id="fs",
            router=None,
            selector=None,
            quarantine=None,
            load_tracker=None,
        )
