"""Distributed node abstraction — BaseNode, LocalNode, NodeRegistry.

Design goals
------------
LLMOS Bridge supports single-machine (standalone) and multi-node (orchestrator)
modes.  ``NodeRegistry`` routes action dispatch to either a ``LocalNode``
(delegates to ``ModuleRegistry``) or a ``RemoteNode`` (HTTP to a remote daemon).

Standalone guarantee
--------------------
``NodeRegistry.resolve(None)`` ALWAYS returns ``LocalNode``.
``LocalNode`` contains zero network code.
If ``settings.node.mode == "standalone"``, no discovery service is started
and no remote nodes are ever registered.  The distributed layer is a strict
no-op for single-machine deployments.

Interface contract
------------------
Any ``BaseNode`` implementation MUST implement ``execute_action()``.  The
executor is the only consumer of this interface, and it calls only that method.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from llmos_bridge.logging import get_logger

if TYPE_CHECKING:
    from llmos_bridge.modules.registry import ModuleRegistry
    from llmos_bridge.orchestration.routing import (
        ActiveActionCounter,
        CapabilityRouter,
        NodeQuarantine,
        NodeSelector,
    )

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# BaseNode — interface contract
# ---------------------------------------------------------------------------


class BaseNode(ABC):
    """Abstract interface for a node that can execute IML actions.

    Implementations: ``LocalNode`` (in-process), ``RemoteNode`` (HTTP).
    Both are interchangeable from the executor's perspective.
    """

    @property
    @abstractmethod
    def node_id(self) -> str:
        """Unique identifier for this node (e.g. ``'local'``, ``'node_lyon_2'``)."""

    @abstractmethod
    async def execute_action(
        self,
        module_id: str,
        action_name: str,
        params: dict[str, Any],
    ) -> Any:
        """Execute a single IML action on this node.

        Args:
            module_id:   The module ID (e.g. ``'filesystem'``).
            action_name: The action name (e.g. ``'read_file'``).
            params:      Already-resolved parameters dict.

        Returns:
            Any JSON-serialisable result from the module.

        Raises:
            ActionNotFoundError: module or action not found on this node.
            ActionExecutionError: the action raised an unexpected exception.
        """

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if this node is reachable and ready to accept actions.

        ``LocalNode`` always returns True.
        ``RemoteNode`` checks the last heartbeat timestamp.
        """


# ---------------------------------------------------------------------------
# LocalNode — in-process implementation
# ---------------------------------------------------------------------------


class LocalNode(BaseNode):
    """Executes actions on the local machine via the ModuleRegistry.

    This is the only implementation used in standalone mode.  It wraps
    the existing ``ModuleRegistry.get(module_id).execute(action, params)``
    call so the executor never needs to know whether it is talking to a
    local or a remote node.
    """

    def __init__(self, registry: "ModuleRegistry") -> None:
        self._registry = registry

    @property
    def node_id(self) -> str:
        return "local"

    async def execute_action(
        self,
        module_id: str,
        action_name: str,
        params: dict[str, Any],
    ) -> Any:
        module = self._registry.get(module_id)
        return await module.execute(action_name, params)

    def is_available(self) -> bool:
        return True

    def __repr__(self) -> str:
        return "LocalNode(node_id='local')"


# ---------------------------------------------------------------------------
# NodeRegistry — routing table
# ---------------------------------------------------------------------------


class NodeRegistry:
    """Maps node IDs to BaseNode instances.

    Usage::

        registry = NodeRegistry(LocalNode(module_registry))

        # Standalone — always resolves to local
        node = registry.resolve(None)
        node = registry.resolve("local")

        # Multi-node — resolves to a remote node
        node = registry.resolve("node_lyon_2")
        result = await node.execute_action("filesystem", "read_file", params)
    """

    def __init__(self, local_node: LocalNode) -> None:
        self._nodes: dict[str, BaseNode] = {local_node.node_id: local_node}
        self._local = local_node

    @property
    def local_node(self) -> LocalNode:
        """Return the local node."""
        return self._local

    def resolve(self, target: str | None) -> BaseNode:
        """Return the BaseNode for *target*.

        ``None`` and ``"local"`` always resolve to the local node.
        Any other string must match a registered node_id.

        Raises:
            KeyError: if *target* is not ``None`` and not registered.
        """
        if target is None:
            return self._local
        node = self._nodes.get(target)
        if node is None:
            raise KeyError(
                f"Unknown node: {target!r}. "
                f"Registered nodes: {list(self._nodes)}"
            )
        return node

    def get_node(self, node_id: str) -> BaseNode | None:
        """Look up a node by ID without fallback.  Returns None if not found."""
        return self._nodes.get(node_id)

    def register(self, node: BaseNode) -> None:
        """Register a node.  Overwrites any existing entry with the same ID."""
        self._nodes[node.node_id] = node
        log.info("node_registered", node_id=node.node_id)

    def unregister(self, node_id: str) -> bool:
        """Remove a node from the registry.

        The local node cannot be unregistered.
        Returns True if a node was actually removed.
        """
        if node_id == self._local.node_id:
            log.warning("node_unregister_local_ignored")
            return False
        removed = self._nodes.pop(node_id, None)
        if removed is not None:
            log.info("node_unregistered", node_id=node_id)
            return True
        return False

    def list_nodes(self) -> list[str]:
        """Return all registered node IDs."""
        return list(self._nodes)

    def get_remote_nodes(self) -> list[BaseNode]:
        """Return all non-local nodes."""
        return [n for nid, n in self._nodes.items() if nid != self._local.node_id]

    def resolve_for_action(
        self,
        target: str | None,
        module_id: str,
        router: "CapabilityRouter | None" = None,
        selector: "NodeSelector | None" = None,
        quarantine: "NodeQuarantine | None" = None,
        load_tracker: "ActiveActionCounter | None" = None,
    ) -> BaseNode:
        """Smart resolve: explicit target → direct lookup; None → auto-route.

        Routing order:
        1. Explicit *target* — direct lookup (backward compatible).
        2. No *router* — fallback to local (standalone behaviour).
        3. Smart routing: find capable nodes → exclude quarantined → select best.
        4. No capable node found → fallback to local.
        """
        # 1. Explicit target — direct lookup (backward compat).
        if target is not None:
            return self.resolve(target)

        # 2. No router — standalone, always local.
        if router is None:
            return self._local

        # 3. Smart routing.
        candidates = router.find_capable_nodes(module_id)
        if quarantine:
            candidates = [
                c for c in candidates if not quarantine.is_quarantined(c.node_id)
            ]
        if not candidates:
            return self._local  # Fallback to local even if no capability.

        node = (
            selector.select(candidates, module_id, load_tracker)
            if selector
            else candidates[0]
        )
        return node or self._local

    def __len__(self) -> int:
        return len(self._nodes)

    def __repr__(self) -> str:
        return f"NodeRegistry(nodes={self.list_nodes()})"
