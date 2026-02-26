"""Orchestration layer — Rollback engine.

When an action fails with ``on_error: rollback``, the rollback engine
executes the compensating action defined in ``action.rollback``.

The rollback chain is depth-limited to prevent infinite loops.
Rollback actions themselves never trigger further rollbacks.
"""

from __future__ import annotations

from typing import Any

from llmos_bridge.logging import get_logger
from llmos_bridge.modules.registry import ModuleRegistry
from llmos_bridge.protocol.models import IMLAction, IMLPlan
from llmos_bridge.protocol.template import TemplateResolver

log = get_logger(__name__)

_MAX_ROLLBACK_DEPTH = 5


class RollbackEngine:
    """Executes rollback actions for failed plan actions.

    Usage::

        engine = RollbackEngine(module_registry=registry)
        await engine.execute(plan, failed_action, execution_results)
    """

    def __init__(self, module_registry: ModuleRegistry) -> None:
        self._registry = module_registry

    async def execute(
        self,
        plan: IMLPlan,
        failed_action: IMLAction,
        execution_results: dict[str, Any],
        depth: int = 0,
    ) -> None:
        """Execute the rollback action for *failed_action*.

        Args:
            plan:               The plan containing the failed action.
            failed_action:      The action that failed and requested rollback.
            execution_results:  Results of previously completed actions.
            depth:              Current rollback recursion depth.
        """
        if depth >= _MAX_ROLLBACK_DEPTH:
            log.error(
                "rollback_depth_exceeded",
                action_id=failed_action.id,
                max_depth=_MAX_ROLLBACK_DEPTH,
            )
            return

        if not failed_action.rollback:
            return

        rollback_action = plan.get_action(failed_action.rollback.action)
        if rollback_action is None:
            log.error(
                "rollback_action_not_found",
                action_id=failed_action.id,
                rollback_target=failed_action.rollback.action,
            )
            return

        # Override params with rollback-specific params (may reference results).
        params = dict(rollback_action.params)
        params.update(failed_action.rollback.params)

        resolver = TemplateResolver(execution_results=execution_results)
        try:
            resolved_params = resolver.resolve(params)
        except Exception as exc:
            log.error("rollback_template_resolution_failed", error=str(exc))
            return

        log.info(
            "rollback_executing",
            failed_action=failed_action.id,
            rollback_action=rollback_action.id,
        )

        try:
            module = self._registry.get(rollback_action.module)
            await module.execute(rollback_action.action, resolved_params)
            log.info("rollback_completed", rollback_action=rollback_action.id)
        except Exception as exc:
            log.error("rollback_failed", rollback_action=rollback_action.id, error=str(exc))
