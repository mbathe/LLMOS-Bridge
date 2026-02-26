"""Orchestration layer — DAG Scheduler.

Builds a NetworkX DiGraph from an IMLPlan and provides ordered execution
waves for both sequential and parallel execution modes.

A "wave" is a batch of actions whose dependencies are all satisfied and
that can be dispatched concurrently.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator

import networkx as nx

from llmos_bridge.exceptions import DAGCycleError
from llmos_bridge.protocol.models import ExecutionMode, IMLPlan


@dataclass
class ExecutionWave:
    """A batch of action IDs that can run concurrently."""

    wave_index: int
    action_ids: list[str]
    is_final: bool = False


class DAGScheduler:
    """Builds and queries the execution graph for a plan.

    Usage::

        scheduler = DAGScheduler(plan)
        for wave in scheduler.waves():
            # dispatch all actions in `wave.action_ids` concurrently
    """

    def __init__(self, plan: IMLPlan) -> None:
        self._plan = plan
        self._graph = self._build_graph(plan)

    @staticmethod
    def _build_graph(plan: IMLPlan) -> nx.DiGraph:
        graph: nx.DiGraph = nx.DiGraph()
        for action in plan.actions:
            graph.add_node(action.id)
        for action in plan.actions:
            for dep in action.depends_on:
                graph.add_edge(dep, action.id)

        if not nx.is_directed_acyclic_graph(graph):
            try:
                cycle = nx.find_cycle(graph)
                cycle_ids = [edge[0] for edge in cycle] + [cycle[-1][1]]
            except nx.NetworkXNoCycle:
                cycle_ids = []
            raise DAGCycleError(cycle_ids)

        return graph

    def waves(self) -> Iterator[ExecutionWave]:
        """Yield :class:`ExecutionWave` instances in topological order.

        In SEQUENTIAL mode each wave contains exactly one action.
        In PARALLEL mode each wave contains all actions whose dependencies
        have been satisfied.
        """
        if self._plan.execution_mode == ExecutionMode.SEQUENTIAL:
            yield from self._sequential_waves()
        else:
            yield from self._parallel_waves()

    def _sequential_waves(self) -> Iterator[ExecutionWave]:
        order = list(nx.topological_sort(self._graph))
        for i, action_id in enumerate(order):
            yield ExecutionWave(
                wave_index=i,
                action_ids=[action_id],
                is_final=(i == len(order) - 1),
            )

    def _parallel_waves(self) -> Iterator[ExecutionWave]:
        """Kahn's algorithm — emit all zero-in-degree nodes as a wave."""
        graph = self._graph.copy()
        wave_index = 0

        while graph.nodes:
            ready = [n for n in graph.nodes if graph.in_degree(n) == 0]
            if not ready:
                # Should not happen if DAG validation passed, but guard anyway.
                raise DAGCycleError(list(graph.nodes))

            remaining_after = len(graph.nodes) - len(ready)
            yield ExecutionWave(
                wave_index=wave_index,
                action_ids=sorted(ready),
                is_final=(remaining_after == 0),
            )
            graph.remove_nodes_from(ready)
            wave_index += 1

    def topological_order(self) -> list[str]:
        """Return all action IDs in a valid topological order."""
        return list(nx.topological_sort(self._graph))

    def successors(self, action_id: str) -> list[str]:
        """Return action IDs that depend directly on *action_id*."""
        return list(self._graph.successors(action_id))

    def predecessors(self, action_id: str) -> list[str]:
        """Return action IDs that *action_id* directly depends on."""
        return list(self._graph.predecessors(action_id))

    def ancestors(self, action_id: str) -> set[str]:
        """Return all transitive predecessors of *action_id*."""
        return nx.ancestors(self._graph, action_id)

    def descendants(self, action_id: str) -> set[str]:
        """Return all transitive successors of *action_id*."""
        return nx.descendants(self._graph, action_id)

    def is_independent(self, a: str, b: str) -> bool:
        """Return True if actions *a* and *b* have no dependency relationship."""
        return a not in self.ancestors(b) and b not in self.ancestors(a)
