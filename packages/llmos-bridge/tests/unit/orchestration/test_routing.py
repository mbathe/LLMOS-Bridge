"""Unit tests — Smart routing engine (Phase 4).

Tests cover:
- CapabilityRouter: find_capable_nodes for local+remote, empty results
- NodeSelector: local_first, round_robin, least_loaded, affinity
- NodeQuarantine: threshold, duration expiry, record_success resets
- ActiveActionCounter: increment, decrement, get, snapshot
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, PropertyMock

import pytest

from llmos_bridge.orchestration.routing import (
    ActiveActionCounter,
    CapabilityRouter,
    NodeQuarantine,
    NodeSelector,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_local_node(available_modules: list[str]) -> MagicMock:
    """Create a mock LocalNode."""
    from llmos_bridge.orchestration.nodes import LocalNode

    node = MagicMock(spec=LocalNode)
    type(node).node_id = PropertyMock(return_value="local")
    node.is_available.return_value = True
    node._registry = MagicMock()
    node._registry.is_available = lambda m: m in available_modules
    # Make isinstance checks work.
    node.__class__ = LocalNode
    return node


def _make_remote_node(
    node_id: str, capabilities: list[str], available: bool = True
) -> MagicMock:
    """Create a mock RemoteNode."""
    node = MagicMock()
    type(node).node_id = PropertyMock(return_value=node_id)
    node.is_available.return_value = available
    node._capabilities = capabilities
    return node


def _make_node_registry(
    local_modules: list[str],
    remotes: list[tuple[str, list[str], bool]] | None = None,
) -> MagicMock:
    """Create a mock NodeRegistry with a local node and optional remotes."""
    local = _make_local_node(local_modules)
    nodes: dict[str, MagicMock] = {"local": local}
    if remotes:
        for nid, caps, avail in remotes:
            nodes[nid] = _make_remote_node(nid, caps, avail)

    registry = MagicMock()
    registry.list_nodes.return_value = list(nodes.keys())
    registry.get_node = lambda nid: nodes.get(nid)
    return registry


# ---------------------------------------------------------------------------
# CapabilityRouter
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCapabilityRouter:
    def test_finds_local_node(self) -> None:
        registry = _make_node_registry(["filesystem", "os_exec"])
        router = CapabilityRouter(registry)

        result = router.find_capable_nodes("filesystem")
        assert len(result) == 1
        assert result[0].node_id == "local"

    def test_finds_remote_node(self) -> None:
        registry = _make_node_registry(
            ["filesystem"],
            remotes=[("gpu-node", ["vision", "computer_control"], True)],
        )
        router = CapabilityRouter(registry)

        result = router.find_capable_nodes("vision")
        assert len(result) == 1
        assert result[0].node_id == "gpu-node"

    def test_finds_both_local_and_remote(self) -> None:
        registry = _make_node_registry(
            ["filesystem"],
            remotes=[("node-2", ["filesystem", "os_exec"], True)],
        )
        router = CapabilityRouter(registry)

        result = router.find_capable_nodes("filesystem")
        assert len(result) == 2

    def test_excludes_unavailable_nodes(self) -> None:
        registry = _make_node_registry(
            ["filesystem"],
            remotes=[("node-2", ["filesystem"], False)],
        )
        router = CapabilityRouter(registry)

        result = router.find_capable_nodes("filesystem")
        assert len(result) == 1
        assert result[0].node_id == "local"

    def test_returns_empty_when_no_capable_node(self) -> None:
        registry = _make_node_registry(["filesystem"])
        router = CapabilityRouter(registry)

        result = router.find_capable_nodes("nonexistent_module")
        assert result == []

    def test_multiple_remote_nodes(self) -> None:
        registry = _make_node_registry(
            [],
            remotes=[
                ("gpu-1", ["vision"], True),
                ("gpu-2", ["vision"], True),
                ("cpu-1", ["filesystem"], True),
            ],
        )
        router = CapabilityRouter(registry)

        result = router.find_capable_nodes("vision")
        assert len(result) == 2
        node_ids = {n.node_id for n in result}
        assert node_ids == {"gpu-1", "gpu-2"}


# ---------------------------------------------------------------------------
# NodeSelector
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNodeSelector:
    def test_local_first_prefers_local(self) -> None:
        local = _make_local_node(["fs"])
        remote = _make_remote_node("remote-1", ["fs"])
        selector = NodeSelector("local_first")

        result = selector.select([remote, local], "fs")
        assert result.node_id == "local"

    def test_local_first_falls_back_to_remote(self) -> None:
        remote = _make_remote_node("remote-1", ["fs"])
        selector = NodeSelector("local_first")

        result = selector.select([remote], "fs")
        assert result.node_id == "remote-1"

    def test_round_robin_cycles(self) -> None:
        n1 = _make_remote_node("n1", ["fs"])
        n2 = _make_remote_node("n2", ["fs"])
        n3 = _make_remote_node("n3", ["fs"])
        selector = NodeSelector("round_robin")

        results = [selector.select([n1, n2, n3], "fs").node_id for _ in range(6)]
        assert results == ["n1", "n2", "n3", "n1", "n2", "n3"]

    def test_round_robin_per_module(self) -> None:
        n1 = _make_remote_node("n1", ["fs", "db"])
        n2 = _make_remote_node("n2", ["fs", "db"])
        selector = NodeSelector("round_robin")

        # Advance "fs" round-robin
        selector.select([n1, n2], "fs")
        selector.select([n1, n2], "fs")

        # "db" should start from n1 (independent counter)
        result = selector.select([n1, n2], "db")
        assert result.node_id == "n1"

    def test_least_loaded_picks_lowest(self) -> None:
        n1 = _make_remote_node("n1", ["fs"])
        n2 = _make_remote_node("n2", ["fs"])
        tracker = ActiveActionCounter()
        tracker.increment("n1")
        tracker.increment("n1")
        tracker.increment("n2")

        selector = NodeSelector("least_loaded")
        result = selector.select([n1, n2], "fs", tracker)
        assert result.node_id == "n2"

    def test_least_loaded_no_tracker_returns_first(self) -> None:
        n1 = _make_remote_node("n1", ["fs"])
        n2 = _make_remote_node("n2", ["fs"])
        selector = NodeSelector("least_loaded")

        result = selector.select([n1, n2], "fs", None)
        assert result.node_id == "n1"

    def test_affinity_uses_preferred_node(self) -> None:
        n1 = _make_remote_node("gpu-node", ["vision"])
        n2 = _make_remote_node("cpu-node", ["vision"])
        selector = NodeSelector("affinity", module_affinity={"vision": "gpu-node"})

        result = selector.select([n2, n1], "vision")
        assert result.node_id == "gpu-node"

    def test_affinity_falls_back_to_local_first(self) -> None:
        local = _make_local_node(["vision"])
        remote = _make_remote_node("cpu-node", ["vision"])
        selector = NodeSelector("affinity", module_affinity={"vision": "nonexistent"})

        result = selector.select([remote, local], "vision")
        assert result.node_id == "local"

    def test_select_empty_candidates_returns_none(self) -> None:
        selector = NodeSelector("local_first")
        assert selector.select([], "fs") is None

    def test_unknown_strategy_returns_first(self) -> None:
        n1 = _make_remote_node("n1", ["fs"])
        selector = NodeSelector("unknown_strategy")
        result = selector.select([n1], "fs")
        assert result.node_id == "n1"

    def test_strategy_property(self) -> None:
        selector = NodeSelector("round_robin")
        assert selector.strategy == "round_robin"


# ---------------------------------------------------------------------------
# NodeQuarantine
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNodeQuarantine:
    def test_not_quarantined_by_default(self) -> None:
        q = NodeQuarantine(threshold=3, duration=60.0)
        assert q.is_quarantined("node-1") is False

    def test_quarantine_after_threshold(self) -> None:
        q = NodeQuarantine(threshold=3, duration=60.0)
        q.record_failure("node-1")
        q.record_failure("node-1")
        assert q.is_quarantined("node-1") is False
        q.record_failure("node-1")
        assert q.is_quarantined("node-1") is True

    def test_quarantine_expiry(self) -> None:
        q = NodeQuarantine(threshold=1, duration=0.01)
        q.record_failure("node-1")
        assert q.is_quarantined("node-1") is True
        time.sleep(0.02)
        assert q.is_quarantined("node-1") is False

    def test_record_success_resets(self) -> None:
        q = NodeQuarantine(threshold=3, duration=60.0)
        q.record_failure("node-1")
        q.record_failure("node-1")
        q.record_success("node-1")
        # Counter reset — need 3 more to quarantine.
        q.record_failure("node-1")
        q.record_failure("node-1")
        assert q.is_quarantined("node-1") is False

    def test_quarantined_nodes_list(self) -> None:
        q = NodeQuarantine(threshold=1, duration=60.0)
        q.record_failure("node-1")
        q.record_failure("node-2")
        assert set(q.quarantined_nodes()) == {"node-1", "node-2"}

    def test_quarantined_nodes_cleans_expired(self) -> None:
        q = NodeQuarantine(threshold=1, duration=0.01)
        q.record_failure("node-1")
        time.sleep(0.02)
        assert q.quarantined_nodes() == []

    def test_failure_count(self) -> None:
        q = NodeQuarantine(threshold=5, duration=60.0)
        q.record_failure("node-1")
        q.record_failure("node-1")
        assert q.failure_count("node-1") == 2
        assert q.failure_count("node-2") == 0

    def test_properties(self) -> None:
        q = NodeQuarantine(threshold=5, duration=120.0)
        assert q.threshold == 5
        assert q.duration == 120.0


# ---------------------------------------------------------------------------
# ActiveActionCounter
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestActiveActionCounter:
    def test_initial_count_is_zero(self) -> None:
        c = ActiveActionCounter()
        assert c.get("node-1") == 0

    def test_increment(self) -> None:
        c = ActiveActionCounter()
        c.increment("node-1")
        c.increment("node-1")
        assert c.get("node-1") == 2

    def test_decrement(self) -> None:
        c = ActiveActionCounter()
        c.increment("node-1")
        c.increment("node-1")
        c.decrement("node-1")
        assert c.get("node-1") == 1

    def test_decrement_floor_at_zero(self) -> None:
        c = ActiveActionCounter()
        c.decrement("node-1")
        assert c.get("node-1") == 0

    def test_snapshot(self) -> None:
        c = ActiveActionCounter()
        c.increment("node-1")
        c.increment("node-2")
        c.increment("node-2")
        snap = c.snapshot()
        assert snap == {"node-1": 1, "node-2": 2}

    def test_snapshot_is_copy(self) -> None:
        c = ActiveActionCounter()
        c.increment("node-1")
        snap = c.snapshot()
        snap["node-1"] = 999
        assert c.get("node-1") == 1
