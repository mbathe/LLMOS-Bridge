"""Smart routing engine — capability-based node selection.

Components
----------
- **CapabilityRouter**: filters nodes that can execute a given module.
- **NodeSelector**: picks the best node using a configurable strategy.
- **NodeQuarantine**: tracks consecutive failures and temporarily excludes nodes.
- **ActiveActionCounter**: tracks in-flight actions per node for load balancing.

These are instantiated only when ``node.mode != "standalone"`` and a
``RoutingConfig`` is provided.  Standalone deployments never import this module.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from llmos_bridge.logging import get_logger

if TYPE_CHECKING:
    from llmos_bridge.orchestration.nodes import BaseNode, LocalNode, NodeRegistry

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# CapabilityRouter — filter nodes by module availability
# ---------------------------------------------------------------------------


class CapabilityRouter:
    """Filter nodes capable of executing a given module.

    Uses ``RemoteNode._capabilities`` (populated by heartbeat) and
    ``LocalNode._registry.is_available()`` to determine which nodes
    support a requested module.
    """

    def __init__(self, node_registry: "NodeRegistry") -> None:
        self._registry = node_registry

    def find_capable_nodes(self, module_id: str) -> list["BaseNode"]:
        """Return all available nodes that support *module_id*."""
        from llmos_bridge.orchestration.nodes import LocalNode

        capable: list["BaseNode"] = []
        for nid in self._registry.list_nodes():
            node = self._registry.get_node(nid)
            if node is None or not node.is_available():
                continue
            if isinstance(node, LocalNode):
                if node._registry.is_available(module_id):
                    capable.append(node)
            else:
                # RemoteNode — check _capabilities list populated by heartbeat.
                if module_id in getattr(node, "_capabilities", []):
                    capable.append(node)
        return capable


# ---------------------------------------------------------------------------
# NodeSelector — strategy-based node selection
# ---------------------------------------------------------------------------


class NodeSelector:
    """Select the best node from a list of candidates.

    Strategies:
    - ``local_first``: prefer local node, fallback to first remote.
    - ``round_robin``: distribute evenly across nodes per module.
    - ``least_loaded``: pick the node with fewest active actions.
    - ``affinity``: use a module→node preference map, fallback to local_first.
    """

    def __init__(
        self,
        strategy: str = "local_first",
        module_affinity: dict[str, str] | None = None,
    ) -> None:
        self._strategy = strategy
        self._affinity = module_affinity or {}
        self._rr_index: dict[str, int] = {}

    @property
    def strategy(self) -> str:
        return self._strategy

    def select(
        self,
        candidates: list["BaseNode"],
        module_id: str,
        load_tracker: "ActiveActionCounter | None" = None,
    ) -> "BaseNode | None":
        """Pick the best node from *candidates* for *module_id*."""
        if not candidates:
            return None
        if self._strategy == "local_first":
            return self._select_local_first(candidates)
        elif self._strategy == "round_robin":
            return self._select_round_robin(candidates, module_id)
        elif self._strategy == "least_loaded":
            return self._select_least_loaded(candidates, load_tracker)
        elif self._strategy == "affinity":
            return self._select_affinity(candidates, module_id)
        return candidates[0]

    def _select_local_first(self, candidates: list["BaseNode"]) -> "BaseNode":
        from llmos_bridge.orchestration.nodes import LocalNode

        for c in candidates:
            if isinstance(c, LocalNode):
                return c
        return candidates[0]

    def _select_round_robin(
        self, candidates: list["BaseNode"], module_id: str
    ) -> "BaseNode":
        idx = self._rr_index.get(module_id, 0) % len(candidates)
        self._rr_index[module_id] = idx + 1
        return candidates[idx]

    def _select_least_loaded(
        self,
        candidates: list["BaseNode"],
        load_tracker: "ActiveActionCounter | None",
    ) -> "BaseNode":
        if load_tracker is None:
            return candidates[0]
        return min(candidates, key=lambda n: load_tracker.get(n.node_id))

    def _select_affinity(
        self, candidates: list["BaseNode"], module_id: str
    ) -> "BaseNode":
        preferred = self._affinity.get(module_id)
        if preferred:
            for c in candidates:
                if c.node_id == preferred:
                    return c
        # Fallback to local_first.
        return self._select_local_first(candidates)


# ---------------------------------------------------------------------------
# NodeQuarantine — track failures and exclude unreliable nodes
# ---------------------------------------------------------------------------


class NodeQuarantine:
    """Track consecutive failures and quarantine unreliable nodes.

    A node is quarantined after *threshold* consecutive failures and
    excluded from routing for *duration* seconds.  A successful action
    resets the failure counter immediately.
    """

    def __init__(self, threshold: int = 3, duration: float = 60.0) -> None:
        self._threshold = threshold
        self._duration = duration
        self._failure_counts: dict[str, int] = {}
        self._quarantine_until: dict[str, float] = {}

    @property
    def threshold(self) -> int:
        return self._threshold

    @property
    def duration(self) -> float:
        return self._duration

    def is_quarantined(self, node_id: str) -> bool:
        """Return True if *node_id* is currently quarantined."""
        until = self._quarantine_until.get(node_id)
        if until is None:
            return False
        if time.time() >= until:
            # Quarantine expired — auto-release.
            del self._quarantine_until[node_id]
            self._failure_counts.pop(node_id, None)
            return False
        return True

    def record_failure(self, node_id: str) -> None:
        """Record a failure for *node_id*.  Quarantine if threshold reached."""
        self._failure_counts[node_id] = self._failure_counts.get(node_id, 0) + 1
        if self._failure_counts[node_id] >= self._threshold:
            self._quarantine_until[node_id] = time.time() + self._duration
            log.warning(
                "node_quarantined",
                node_id=node_id,
                duration=self._duration,
                failures=self._failure_counts[node_id],
            )

    def record_success(self, node_id: str) -> None:
        """Reset failure counter for *node_id*."""
        self._failure_counts.pop(node_id, None)
        self._quarantine_until.pop(node_id, None)

    def quarantined_nodes(self) -> list[str]:
        """Return list of currently quarantined node IDs."""
        now = time.time()
        result: list[str] = []
        expired: list[str] = []
        for nid, until in self._quarantine_until.items():
            if now >= until:
                expired.append(nid)
            else:
                result.append(nid)
        # Cleanup expired entries.
        for nid in expired:
            del self._quarantine_until[nid]
            self._failure_counts.pop(nid, None)
        return result

    def failure_count(self, node_id: str) -> int:
        """Return the current failure count for *node_id*."""
        return self._failure_counts.get(node_id, 0)


# ---------------------------------------------------------------------------
# ActiveActionCounter — track in-flight actions per node
# ---------------------------------------------------------------------------


class ActiveActionCounter:
    """Track how many actions each node is currently executing.

    Used by the ``least_loaded`` strategy to prefer less-busy nodes.
    """

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}

    def increment(self, node_id: str) -> None:
        """Record that an action has started on *node_id*."""
        self._counts[node_id] = self._counts.get(node_id, 0) + 1

    def decrement(self, node_id: str) -> None:
        """Record that an action has finished on *node_id*."""
        count = self._counts.get(node_id, 0)
        self._counts[node_id] = max(0, count - 1)

    def get(self, node_id: str) -> int:
        """Return the number of active actions on *node_id*."""
        return self._counts.get(node_id, 0)

    def snapshot(self) -> dict[str, int]:
        """Return a copy of all active action counts."""
        return dict(self._counts)
