"""Distributed node abstraction — BaseNode, LocalNode, NodeRegistry.

Design goals
------------
Phase 1 (today): LLMOS Bridge runs on a single machine.  ``NodeRegistry``
holds only a ``LocalNode`` that delegates directly to the ``ModuleRegistry``.
All existing behaviour is preserved — ``target_node=None`` is resolved to
the local node, which is identical to the pre-distributed code path.

Phase 4 (future): ``RemoteNode`` implements ``BaseNode`` over HTTP/gRPC to
a remote LLMOS instance.  ``NodeRegistry.register_remote()`` adds it.
The executor never changes — it always calls ``node.execute_action()``.

Standalone guarantee
--------------------
``NodeRegistry.resolve(None)`` ALWAYS returns ``LocalNode``.
``LocalNode`` contains zero network code.
If ``settings.node.mode == "standalone"``, no discovery service is started
and no remote nodes are ever registered.  The distributed layer is a strict
no-op for single-machine deployments.

Interface contract
------------------
Any future ``RemoteNode`` implementation MUST implement the same
``BaseNode`` interface — specifically ``execute_action()``.  The executor
is the only consumer of this interface, and it calls only that method.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from llmos_bridge.logging import get_logger

if TYPE_CHECKING:
    from llmos_bridge.modules.registry import ModuleRegistry

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# BaseNode — interface contract
# ---------------------------------------------------------------------------


class BaseNode(ABC):
    """Abstract interface for a node that can execute IML actions.

    Phase 1 implementation: ``LocalNode`` — delegates to ``ModuleRegistry``.
    Phase 4 implementation: ``RemoteNode`` — sends actions over HTTP/gRPC
    to a remote LLMOS Bridge instance.

    Both implementations are interchangeable from the executor's perspective.
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
# LocalNode — Phase 1 implementation
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

    Phase 1: contains only the single ``LocalNode``.
    Phase 4: ``register_remote()`` populates additional ``RemoteNode``
    instances discovered via mDNS or explicit config.

    Thread/async safety: registrations happen at startup before any
    concurrent requests arrive, so no lock is needed for Phase 1.
    Phase 4 should add an asyncio.Lock around mutations if nodes can
    join/leave at runtime.

    Usage::

        registry = NodeRegistry(LocalNode(module_registry))

        # Standalone — always resolves to local
        node = registry.resolve(None)
        node = registry.resolve("local")

        # Phase 4 — resolves to a remote node
        node = registry.resolve("node_lyon_2")
        result = await node.execute_action("filesystem", "read_file", params)
    """

    def __init__(self, local_node: LocalNode) -> None:
        self._nodes: dict[str, BaseNode] = {local_node.node_id: local_node}
        self._local = local_node

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

    def register(self, node: BaseNode) -> None:
        """Register a node.  Overwrites any existing entry with the same ID.

        Called at startup for the local node, and at Phase 4 runtime for
        remote nodes discovered via mDNS or explicit config.
        """
        self._nodes[node.node_id] = node
        log.info("node_registered", node_id=node.node_id)

    def unregister(self, node_id: str) -> None:
        """Remove a node from the registry (e.g. when it goes offline).

        The local node cannot be unregistered.
        """
        if node_id == self._local.node_id:
            log.warning("node_unregister_local_ignored")
            return
        removed = self._nodes.pop(node_id, None)
        if removed is not None:
            log.info("node_unregistered", node_id=node_id)

    def list_nodes(self) -> list[str]:
        """Return all registered node IDs."""
        return list(self._nodes)

    def __len__(self) -> int:
        return len(self._nodes)

    def __repr__(self) -> str:
        return f"NodeRegistry(nodes={self.list_nodes()})"
