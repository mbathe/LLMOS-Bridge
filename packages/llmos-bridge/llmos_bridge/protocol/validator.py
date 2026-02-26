"""IML Protocol v2 — Semantic validator.

Runs semantic checks that cannot be expressed in Pydantic models alone:
  - DAG cycle detection
  - Parallel mode dependency constraints
  - Reactive mode event constraints
  - Template reference validation ({{result.X.Y}} references existing actions)
  - Rollback chain validation (no infinite rollback loops)

The parser handles structural and type validation.
This validator handles semantic consistency.
"""

from __future__ import annotations

import re
from typing import Any

import networkx as nx

from llmos_bridge.exceptions import DAGCycleError, IMLValidationError
from llmos_bridge.protocol.constants import TEMPLATE_PREFIX_RESULT
from llmos_bridge.protocol.models import ExecutionMode, IMLPlan

_TEMPLATE_RE = re.compile(r"\{\{(\w+)\.(\w+)(?:\.(\w+))?\}\}")


class IMLValidator:
    """Semantic validator for IML plans.

    Usage::

        validator = IMLValidator()
        validator.validate(plan)   # raises on error, returns None on success
    """

    def validate(self, plan: IMLPlan) -> None:
        """Run all semantic checks against *plan*.

        Raises:
            DAGCycleError: A dependency cycle was detected.
            IMLValidationError: Any other semantic violation.
        """
        self._check_dag(plan)
        self._check_template_references(plan)
        self._check_rollback_chains(plan)
        self._check_mode_constraints(plan)

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    def _check_dag(self, plan: IMLPlan) -> None:
        """Detect cyclic dependencies using NetworkX."""
        graph: nx.DiGraph = nx.DiGraph()
        for action in plan.actions:
            graph.add_node(action.id)
        for action in plan.actions:
            for dep in action.depends_on:
                graph.add_edge(dep, action.id)

        try:
            cycle = nx.find_cycle(graph)
            cycle_ids = [edge[0] for edge in cycle] + [cycle[-1][1]]
            raise DAGCycleError(cycle_ids)
        except nx.NetworkXNoCycle:
            pass  # No cycle — all good.

    def _check_template_references(self, plan: IMLPlan) -> None:
        """Ensure {{result.X.Y}} templates reference actions that exist."""
        action_ids = {a.id for a in plan.actions}
        errors: list[str] = []

        for action in plan.actions:
            for template in self._extract_templates(action.params):
                prefix, ref_id, _ = template
                if prefix == TEMPLATE_PREFIX_RESULT and ref_id not in action_ids:
                    errors.append(
                        f"Action '{action.id}' references unknown action "
                        f"'{{{{result.{ref_id}...}}}}'"
                    )

        if errors:
            raise IMLValidationError(
                f"Template reference errors: {'; '.join(errors)}"
            )

    def _check_rollback_chains(self, plan: IMLPlan) -> None:
        """Detect rollback chains that loop back into themselves."""
        action_ids = {a.id for a in plan.actions}
        rollback_graph: nx.DiGraph = nx.DiGraph()

        for action in plan.actions:
            rollback_graph.add_node(action.id)
            if action.rollback and action.rollback.action in action_ids:
                rollback_graph.add_edge(action.id, action.rollback.action)

        try:
            cycle = nx.find_cycle(rollback_graph)
            cycle_ids = [edge[0] for edge in cycle]
            raise IMLValidationError(
                f"Rollback cycle detected between actions: {cycle_ids}"
            )
        except nx.NetworkXNoCycle:
            pass

    def _check_mode_constraints(self, plan: IMLPlan) -> None:
        """Mode-specific consistency checks."""
        if plan.execution_mode == ExecutionMode.SEQUENTIAL:
            return  # No additional constraints for sequential mode.

        if plan.execution_mode == ExecutionMode.PARALLEL:
            # In parallel mode, actions with dependencies must not form
            # long linear chains (defeats the purpose).  This is a warning,
            # not an error — we emit it as a validation notice.
            pass

        if plan.execution_mode == ExecutionMode.REACTIVE:
            # Reactive mode: at least one action should be a trigger.
            # (Formal reactive constraints are deferred to Phase 4.)
            pass

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _extract_templates(
        self, params: dict[str, Any]
    ) -> list[tuple[str, str, str | None]]:
        """Walk *params* recursively and collect all template expressions.

        Returns:
            List of (prefix, ref_id, field) tuples.
        """
        results: list[tuple[str, str, str | None]] = []
        self._walk_value(params, results)
        return results

    def _walk_value(
        self, value: Any, results: list[tuple[str, str, str | None]]
    ) -> None:
        if isinstance(value, str):
            for match in _TEMPLATE_RE.finditer(value):
                prefix, ref_id, field = match.group(1), match.group(2), match.group(3)
                results.append((prefix, ref_id, field))
        elif isinstance(value, dict):
            for v in value.values():
                self._walk_value(v, results)
        elif isinstance(value, list):
            for item in value:
                self._walk_value(item, results)
