"""Orchestration layer — Plan executor.

The PlanExecutor drives the full lifecycle of a plan:
  1. Module version compatibility check (module_requirements)
  2. Build execution graph (DAGScheduler)
  3. Security pre-flight (PermissionGuard.check_plan)
  4. For each wave of ready actions:
     a. Skip actions whose dependencies failed (cascade failure)
     b. Resolve {{result.X.Y}} templates in params
     c. Run per-action security check (PermissionGuard.check_action)
     d. Pause for user approval if required
     e. Capture perception before (optional)
     f. Dispatch to module
     g. Capture perception after and re-inject into execution_results
     h. Write to memory (optional)
     i. Update state
  5. Handle errors (abort | continue | retry | rollback | skip)
  6. Cascade SKIPPED status to all descendants of any FAILED/ABORT action
  7. Mark plan as completed or failed

Cascade failure semantics:
  When an action A fails and its ``on_error`` is ``ABORT``, all transitive
  descendants of A are immediately marked as SKIPPED.  This ensures the
  execution state always reflects reality — no action is left in PENDING
  when it can never run.

Perception re-injection:
  When an action has a PerceptionConfig, the PerceptionPipeline captures
  screenshots/OCR around the action.  The resulting report is stored under
  the reserved ``_perception`` key inside the action's execution result so
  downstream templates can reference it:
    {{result.<action_id>._perception.after_text}}
    {{result.<action_id>._perception.diff_detected}}
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from llmos_bridge.exceptions import (
    ActionExecutionError,
    ApprovalRequiredError,
    ExecutionTimeoutError,
    LLMOSError,
    PermissionNotGrantedError,
    RateLimitExceededError,
)
from llmos_bridge.logging import bind_plan_context, get_logger
from llmos_bridge.memory.store import KeyValueStore
from llmos_bridge.modules.registry import ModuleRegistry
from llmos_bridge.orchestration.approval import (
    ApprovalDecision,
    ApprovalGate,
    ApprovalRequest,
    ApprovalResponse,
)
from llmos_bridge.orchestration.dag import DAGScheduler
from llmos_bridge.orchestration.nodes import LocalNode, NodeRegistry
from llmos_bridge.orchestration.rollback import RollbackEngine
from llmos_bridge.orchestration.state import ActionState, ExecutionState, PlanStateStore
from llmos_bridge.protocol.compat import ModuleVersionChecker
from llmos_bridge.protocol.models import (
    ActionStatus,
    IMLAction,
    IMLPlan,
    OnErrorBehavior,
    PlanStatus,
)
from llmos_bridge.protocol.template import TemplateResolver
from llmos_bridge.security.audit import AuditEvent, AuditLogger
from llmos_bridge.security.guard import PermissionGuard
from llmos_bridge.security.sanitizer import OutputSanitizer

log = get_logger(__name__)

# Key under which perception data is stored inside an action's execution result.
# Templates can access it as {{result.<action_id>._perception.after_text}}.
_PERCEPTION_KEY = "_perception"

# Default max result size (512 KB) — overridden by ServerConfig.max_result_size.
_DEFAULT_MAX_RESULT_SIZE = 524_288


def _truncate_result(result: Any, max_bytes: int = _DEFAULT_MAX_RESULT_SIZE) -> Any:
    """Truncate oversized action results to prevent LLM context overflow.

    Serialises to JSON to measure size. If oversized, the result is replaced
    with a summary dict containing the truncated JSON and a warning.
    """
    import json as _json

    try:
        serialised = _json.dumps(result, default=str)
    except (TypeError, ValueError):
        serialised = str(result)

    if len(serialised.encode("utf-8", errors="replace")) <= max_bytes:
        return result

    # Truncate to max_bytes and wrap in a summary.
    truncated = serialised[:max_bytes]
    return {
        "_truncated": True,
        "_original_size": len(serialised),
        "_max_size": max_bytes,
        "data": truncated,
        "warning": f"Result truncated from {len(serialised)} to {max_bytes} bytes.",
    }


class PlanExecutor:
    """Executes an IMLPlan end-to-end.

    Usage::

        executor = PlanExecutor(
            module_registry=registry,
            guard=guard,
            state_store=state_store,
            audit_logger=audit_logger,
        )
        state = await executor.run(plan)
    """

    def __init__(
        self,
        module_registry: ModuleRegistry,
        guard: PermissionGuard,
        state_store: PlanStateStore,
        audit_logger: AuditLogger,
        sanitizer: OutputSanitizer | None = None,
        approval_gate: ApprovalGate | None = None,
        perception_pipeline: Any | None = None,  # perception.pipeline.PerceptionPipeline
        kv_store: KeyValueStore | None = None,
        node_registry: NodeRegistry | None = None,
        resource_manager: Any | None = None,  # ResourceManager (optional)
        fallback_chains: dict[str, list[str]] | None = None,
        max_result_size: int = _DEFAULT_MAX_RESULT_SIZE,
        intent_verifier: Any | None = None,
        scanner_pipeline: Any | None = None,  # SecurityPipeline (optional)
    ) -> None:
        self._registry = module_registry
        self._nodes = node_registry or NodeRegistry(LocalNode(module_registry))
        self._guard = guard
        self._store = state_store
        self._audit = audit_logger
        self._sanitizer = sanitizer or OutputSanitizer()
        self._approval_gate = approval_gate
        self._perception = perception_pipeline
        self._kv_store = kv_store
        self._resource_manager = resource_manager
        self._fallback_chains = fallback_chains or {}
        self._max_result_size = max_result_size
        self._intent_verifier = intent_verifier
        self._scanner_pipeline = scanner_pipeline
        self._rollback = RollbackEngine(module_registry=module_registry)
        # plan_id → asyncio.Task for background execution tracking
        self._running_tasks: dict[str, asyncio.Task[ExecutionState]] = {}

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    async def submit_plan(self, plan: IMLPlan) -> str:
        """Fire-and-forget plan submission.

        Starts ``run(plan)`` as a background asyncio task and returns
        immediately with the ``plan_id``.  Used by TriggerDaemon so that
        the daemon can keep watching for new fires while plans execute.

        Args:
            plan: The IML plan to execute.

        Returns:
            plan_id — the same value as ``plan.plan_id``.
        """
        task: asyncio.Task[ExecutionState] = asyncio.create_task(
            self.run(plan), name=f"plan_{plan.plan_id}"
        )
        self._running_tasks[plan.plan_id] = task
        task.add_done_callback(lambda t: self._running_tasks.pop(plan.plan_id, None))
        log.info("plan_submitted_background", plan_id=plan.plan_id)
        return plan.plan_id

    async def cancel_plan(self, plan_id: str) -> bool:
        """Cancel a running plan submitted via ``submit_plan()``.

        Args:
            plan_id: ID of the plan to cancel.

        Returns:
            True if the task was found and cancelled; False if not running.
        """
        task = self._running_tasks.get(plan_id)
        if task and not task.done():
            task.cancel()
            log.info("plan_cancelled", plan_id=plan_id)
            return True
        return False

    async def run(self, plan: IMLPlan) -> ExecutionState:
        """Execute *plan* and return the final :class:`ExecutionState`."""
        bind_plan_context(plan_id=plan.plan_id, session_id=plan.session_id)

        state = ExecutionState.from_plan(plan)
        await self._store.create(state)
        await self._audit.log(AuditEvent.PLAN_STARTED, plan_id=plan.plan_id)

        execution_results: dict[str, Any] = {}

        # ---- Step 1: module version compatibility check ----
        if plan.module_requirements:
            available = {
                m: self._registry.get_manifest(m).version
                for m in self._registry.list_available()
            }
            checker = ModuleVersionChecker(available_versions=available)
            try:
                checker.assert_compatible(plan.module_requirements)
            except LLMOSError as exc:
                log.error("plan_compat_failed", error=str(exc))
                state.plan_status = PlanStatus.FAILED
                await self._store.update_plan_status(plan.plan_id, PlanStatus.FAILED)
                await self._audit.log(
                    AuditEvent.PLAN_FAILED, plan_id=plan.plan_id, error=str(exc)
                )
                return state

        # ---- Step 1.3: Scanner pipeline (fast heuristic + ML screening) ----
        if self._scanner_pipeline is not None and self._scanner_pipeline.enabled:
            pipeline_result = await self._scanner_pipeline.scan_input(plan)
            if not pipeline_result.allowed:
                error_msg = (
                    f"Input scan rejected: verdict={pipeline_result.aggregate_verdict.value}, "
                    f"risk={pipeline_result.max_risk_score}"
                )
                log.error(
                    "scanner_pipeline_rejected",
                    plan_id=plan.plan_id,
                    verdict=pipeline_result.aggregate_verdict.value,
                    risk_score=pipeline_result.max_risk_score,
                    short_circuited=pipeline_result.short_circuited,
                )
                state.plan_status = PlanStatus.FAILED
                await self._store.update_plan_status(plan.plan_id, PlanStatus.FAILED)
                await self._audit.log(
                    AuditEvent.PLAN_FAILED, plan_id=plan.plan_id, error=error_msg
                )
                return state

        # ---- Step 1.5: Fire intent verification concurrently ----
        # The LLM call is the slowest step (~500-2000ms).  We launch it as
        # a background task and overlap it with the security pre-flight and
        # DAG construction below.  We only await the result before the first
        # action dispatch.
        import asyncio as _aio

        _verification_task: _aio.Task[Any] | None = None
        if self._intent_verifier is not None and self._intent_verifier.enabled:
            _verification_task = _aio.create_task(
                self._intent_verifier.verify_plan(plan)
            )

        # ---- Step 2: security pre-flight (runs concurrently with verification) ----
        try:
            self._guard.check_plan(plan)
        except LLMOSError as exc:
            log.error("plan_preflight_failed", error=str(exc))
            if _verification_task is not None:
                _verification_task.cancel()
            state.plan_status = PlanStatus.FAILED
            await self._store.update_plan_status(plan.plan_id, PlanStatus.FAILED)
            await self._audit.log(AuditEvent.PLAN_FAILED, plan_id=plan.plan_id, error=str(exc))
            return state

        # ---- Step 3: DAG construction (concurrent with verification) ----
        scheduler = DAGScheduler(plan)

        # ---- Step 3.5: Await intent verification before dispatching actions ----
        if _verification_task is not None:
            try:
                from llmos_bridge.security.intent_verifier import VerificationVerdict

                verification = await _verification_task
                if verification.verdict == VerificationVerdict.REJECT:
                    error_msg = f"Intent verification rejected: {verification.reasoning}"
                    log.error(
                        "intent_verification_rejected",
                        plan_id=plan.plan_id,
                        risk_level=verification.risk_level,
                        threats=[t.threat_type.value for t in verification.threats],
                    )
                    state.plan_status = PlanStatus.FAILED
                    await self._store.update_plan_status(plan.plan_id, PlanStatus.FAILED)
                    await self._audit.log(
                        AuditEvent.INTENT_REJECTED,
                        plan_id=plan.plan_id,
                        error=error_msg,
                        risk_level=verification.risk_level,
                    )
                    return state
                elif verification.verdict == VerificationVerdict.CLARIFY:
                    log.warning(
                        "intent_verification_clarify",
                        plan_id=plan.plan_id,
                        clarification=verification.clarification_needed,
                    )
                    if self._intent_verifier._strict:
                        state.plan_status = PlanStatus.FAILED
                        await self._store.update_plan_status(plan.plan_id, PlanStatus.FAILED)
                        return state
                elif verification.verdict == VerificationVerdict.WARN:
                    log.warning(
                        "intent_verification_warn",
                        plan_id=plan.plan_id,
                        reasoning=verification.reasoning,
                    )
            except Exception as exc:
                log.error("intent_verification_error", error=str(exc))
                if self._intent_verifier._strict:
                    state.plan_status = PlanStatus.FAILED
                    await self._store.update_plan_status(plan.plan_id, PlanStatus.FAILED)
                    return state

        await self._store.update_plan_status(plan.plan_id, PlanStatus.RUNNING)
        state.plan_status = PlanStatus.RUNNING

        # Track action IDs that must be skipped due to cascade failure.
        cascade_skipped: set[str] = set()

        for wave in scheduler.waves():
            # Mark actions in this wave that were already cascade-skipped.
            for aid in wave.action_ids:
                if aid in cascade_skipped:
                    await self._skip_action(
                        plan.plan_id, aid, state, "Skipped: dependency failed."
                    )

            runnable = [
                aid for aid in wave.action_ids if aid not in cascade_skipped
            ]

            tasks = [
                self._run_action(
                    plan=plan,
                    action=plan.get_action(aid),  # type: ignore[arg-type]
                    state=state,
                    execution_results=execution_results,
                    cascade_skipped=cascade_skipped,
                )
                for aid in runnable
                if plan.get_action(aid) is not None
            ]
            await asyncio.gather(*tasks, return_exceptions=True)

            # After each wave, detect ABORT failures and cascade-skip descendants.
            abort_triggered = False
            for action in plan.actions:
                action_state = state.get_action(action.id)
                if (
                    action_state.status == ActionStatus.FAILED
                    and action.on_error == OnErrorBehavior.ABORT
                ):
                    new_skips = scheduler.descendants(action.id) - cascade_skipped
                    cascade_skipped.update(new_skips)
                    if new_skips:
                        log.warning(
                            "cascade_skip",
                            failed_action=action.id,
                            skipped_count=len(new_skips),
                        )
                    abort_triggered = True

            if abort_triggered:
                # Immediately persist SKIPPED for all future pending actions.
                for aid in cascade_skipped:
                    action_state = state.get_action(aid)
                    if action_state.status == ActionStatus.PENDING:
                        await self._skip_action(
                            plan.plan_id,
                            aid,
                            state,
                            "Skipped: upstream action failed with abort.",
                        )
                log.warning("plan_aborted_on_failure", plan_id=plan.plan_id)
                break

        final_status = (
            PlanStatus.COMPLETED if state.all_completed() else PlanStatus.FAILED
        )
        state.plan_status = final_status
        await self._store.update_plan_status(plan.plan_id, final_status)

        event = (
            AuditEvent.PLAN_COMPLETED
            if final_status == PlanStatus.COMPLETED
            else AuditEvent.PLAN_FAILED
        )
        await self._audit.log(event, plan_id=plan.plan_id)
        log.info("plan_finished", plan_id=plan.plan_id, status=final_status.value)

        return state

    # ------------------------------------------------------------------
    # Action execution
    # ------------------------------------------------------------------

    async def _run_action(
        self,
        plan: IMLPlan,
        action: IMLAction,
        state: ExecutionState,
        execution_results: dict[str, Any],
        cascade_skipped: set[str],
    ) -> None:
        bind_plan_context(plan_id=plan.plan_id, action_id=action.id)
        action_state = state.get_action(action.id)

        # Guard against race condition where a sibling in the same parallel wave
        # caused a cascade skip while we were already scheduled.
        if action.id in cascade_skipped:
            await self._skip_action(plan.plan_id, action.id, state, "Skipped: dependency failed.")
            return

        # Load memory keys declared in action.memory.read_keys.
        memory_context: dict[str, Any] = {}
        if self._kv_store and action.memory and action.memory.read_keys:
            try:
                memory_context = await self._kv_store.get_many(action.memory.read_keys)
            except Exception as mem_exc:
                log.warning(
                    "memory_read_failed",
                    action_id=action.id,
                    keys=action.memory.read_keys,
                    error=str(mem_exc),
                )

        # Resolve template expressions in params (including {{memory.key}} lookups).
        try:
            resolver = TemplateResolver(
                execution_results=execution_results,
                memory_store=memory_context,
                allow_env=True,
            )
            resolved_params = resolver.resolve(action.params)
        except LLMOSError as exc:
            await self._fail_action(
                plan.plan_id, action.id, action_state, str(exc),
                module_id=action.module, action_name=action.action,
            )
            return

        # Permission check at dispatch time.
        try:
            self._guard.check_action(action, plan_id=plan.plan_id)
            # Re-check sandbox with resolved params — templates like
            # {{result.a1.path}} were skipped during pre-flight and now
            # contain actual paths that must be validated.
            self._guard.check_sandbox_params(
                action.module, action.action, resolved_params
            )
        except ApprovalRequiredError:
            approval_result = await self._handle_approval(
                plan.plan_id, action, action_state, resolved_params, state
            )
            if approval_result is None:
                # Action was rejected, skipped, or failed — already handled.
                return
            # Approval granted (possibly with modified params).
            resolved_params = approval_result
        except LLMOSError as exc:
            await self._fail_action(
                plan.plan_id, action.id, action_state, str(exc),
                module_id=action.module, action_name=action.action,
            )
            return

        attempt = 0
        max_attempts = 1
        retry_config = action.retry

        if action.on_error == OnErrorBehavior.RETRY and retry_config:
            max_attempts = retry_config.max_attempts

        while attempt < max_attempts:
            attempt += 1
            action_state.attempt = attempt
            action_state.status = ActionStatus.RUNNING
            action_state.started_at = time.time()

            await self._store.update_action(
                plan.plan_id, action.id, ActionStatus.RUNNING, attempt=attempt
            )
            await self._audit.log(
                AuditEvent.ACTION_STARTED,
                plan_id=plan.plan_id,
                action_id=action.id,
                attempt=attempt,
            )
            log.info(
                "action_started",
                action=f"{action.module}.{action.action}",
                attempt=attempt,
            )

            # Capture screen before execution (optional).
            perception_before: Any | None = None
            if self._perception and action.perception and action.perception.capture_before:
                perception_before = await self._perception.capture_before(
                    action.id, action.perception
                )

            try:
                raw_result, fallback_used = await asyncio.wait_for(
                    self._dispatch_with_resource_limit(action, resolved_params),
                    timeout=action.timeout,
                )
                if fallback_used:
                    action_state.fallback_module = fallback_used
            except asyncio.TimeoutError:
                err = ExecutionTimeoutError(action.id, action.timeout)
                if attempt < max_attempts:
                    delay = retry_config.delay_for_attempt(attempt) if retry_config else 1.0
                    log.warning("action_retry", delay=delay, attempt=attempt)
                    await asyncio.sleep(delay)
                    continue
                await self._fail_action(
                    plan.plan_id, action.id, action_state, str(err),
                    module_id=action.module, action_name=action.action,
                )
                return
            except PermissionNotGrantedError as exc:
                msg = (
                    f"Permission '{exc.permission}' not granted for module "
                    f"'{exc.module_id}'. Use security.request_permission to grant it."
                )
                await self._fail_action(
                    plan.plan_id, action.id, action_state, msg,
                    module_id=action.module, action_name=action.action,
                )
                return
            except RateLimitExceededError as exc:
                msg = (
                    f"Rate limit exceeded for '{exc.action_key}': "
                    f"max {exc.limit} per {exc.window}. Wait before retrying."
                )
                await self._fail_action(
                    plan.plan_id, action.id, action_state, msg,
                    module_id=action.module, action_name=action.action,
                )
                return
            except Exception as exc:
                if attempt < max_attempts:
                    delay = retry_config.delay_for_attempt(attempt) if retry_config else 1.0
                    log.warning(
                        "action_retry", delay=delay, attempt=attempt, error=str(exc)
                    )
                    await asyncio.sleep(delay)
                    continue
                await self._fail_action(
                    plan.plan_id, action.id, action_state, str(exc),
                    module_id=action.module, action_name=action.action,
                )
                if action.on_error == OnErrorBehavior.ROLLBACK and action.rollback:
                    await self._rollback.execute(plan, action, execution_results)
                return

            # Success — sanitise and record result.
            clean_result = self._sanitizer.sanitize(
                raw_result, module=action.module, action=action.action
            )

            # Perception capture after execution + re-injection into results.
            # Downstream templates can use:
            #   {{result.<action_id>._perception.after_text}}
            #   {{result.<action_id>._perception.diff_detected}}
            if self._perception and action.perception:
                try:
                    perception_report = await self._perception.run_after(
                        action_id=action.id,
                        config=action.perception,
                        before=perception_before,
                    )
                    perception_dict = (
                        perception_report.to_dict()
                        if hasattr(perception_report, "to_dict")
                        else perception_report
                    )
                    if isinstance(clean_result, dict):
                        clean_result[_PERCEPTION_KEY] = perception_dict
                    else:
                        clean_result = {"value": clean_result, _PERCEPTION_KEY: perception_dict}
                except Exception as perc_exc:
                    log.warning(
                        "perception_after_failed",
                        action_id=action.id,
                        error=str(perc_exc),
                    )

            # Truncate oversized results to prevent LLM context overflow.
            clean_result = _truncate_result(clean_result, self._max_result_size)

            execution_results[action.id] = clean_result
            action_state.status = ActionStatus.COMPLETED
            action_state.result = clean_result
            action_state.finished_at = time.time()

            # Persist result to KV memory if a write_key is configured.
            if self._kv_store and action.memory and action.memory.write_key:
                try:
                    await self._kv_store.set(
                        action.memory.write_key,
                        clean_result,
                        session_id=plan.session_id,
                    )
                    log.debug(
                        "memory_written",
                        action_id=action.id,
                        key=action.memory.write_key,
                    )
                except Exception as mem_exc:
                    log.warning(
                        "memory_write_failed",
                        action_id=action.id,
                        key=action.memory.write_key,
                        error=str(mem_exc),
                    )

            await self._store.update_action(
                plan.plan_id, action.id, ActionStatus.COMPLETED, result=clean_result
            )
            await self._audit.log(
                AuditEvent.ACTION_COMPLETED, plan_id=plan.plan_id, action_id=action.id
            )
            log.info("action_completed", action=f"{action.module}.{action.action}")
            return

    async def _dispatch_with_resource_limit(
        self, action: IMLAction, resolved_params: dict[str, Any]
    ) -> tuple[Any, str | None]:
        """Dispatch action, optionally throttled by ResourceManager.

        Returns:
            Tuple of (result, fallback_module_used_or_None).
        """
        if self._resource_manager:
            async with self._resource_manager.acquire(action.module):
                return await self._dispatch_with_fallback(action, resolved_params)
        return await self._dispatch_with_fallback(action, resolved_params)

    async def _dispatch_with_fallback(
        self, action: IMLAction, resolved_params: dict[str, Any]
    ) -> tuple[Any, str | None]:
        """Dispatch action with graceful degradation via fallback modules.

        If the primary module fails and a fallback chain is configured, the
        executor tries each fallback module in order before giving up.

        Returns:
            Tuple of (result, fallback_module_used_or_None).
        """
        try:
            result = await self._dispatch(action, resolved_params)
            return result, None
        except Exception as primary_exc:
            fallback_chain = self._fallback_chains.get(action.module, [])
            if not fallback_chain:
                raise

            for fallback_module in fallback_chain:
                try:
                    node = self._nodes.resolve(action.target_node)
                    result = await node.execute_action(
                        fallback_module, action.action, resolved_params
                    )
                    log.warning(
                        "fallback_used",
                        primary_module=action.module,
                        fallback_module=fallback_module,
                        action=action.action,
                        action_id=action.id,
                    )
                    return result, fallback_module
                except Exception:
                    continue

            # All fallbacks failed — raise the original error.
            raise primary_exc

    async def _dispatch(self, action: IMLAction, resolved_params: dict[str, Any]) -> Any:
        node = self._nodes.resolve(action.target_node)
        return await node.execute_action(action.module, action.action, resolved_params)

    def _suggest_alternatives(
        self, module_id: str, action_name: str, error: str
    ) -> list[str]:
        """Generate alternative suggestions when an action fails.

        Looks at fallback chains and available modules to propose concrete
        alternatives the LLM can try next (Negotiation Protocol).
        """
        alternatives: list[str] = []

        # Suggest fallback modules that have the same action.
        for fb_module in self._fallback_chains.get(module_id, []):
            try:
                mod = self._registry.get(fb_module)
                manifest = mod.get_manifest()
                action_names = [a.name for a in manifest.actions]
                if action_name in action_names:
                    alternatives.append(
                        f"Try '{fb_module}.{action_name}' as an alternative"
                    )
            except Exception:
                pass

        # Suggest retry with different params for common errors.
        err_lower = error.lower()
        if "not found" in err_lower or "no such file" in err_lower:
            alternatives.append("Verify the file path exists before retrying")
        if "permission" in err_lower or "denied" in err_lower:
            alternatives.append("Check file permissions or use a different path")
        if "timeout" in err_lower:
            alternatives.append("Retry with a longer timeout or smaller payload")

        return alternatives

    async def _fail_action(
        self,
        plan_id: str,
        action_id: str,
        action_state: ActionState,
        error: str,
        module_id: str = "",
        action_name: str = "",
    ) -> None:
        # Negotiation Protocol: store structured alternatives on the action state.
        alternatives = self._suggest_alternatives(module_id, action_name, error)
        action_state.alternatives = alternatives

        action_state.status = ActionStatus.FAILED
        action_state.error = error
        action_state.finished_at = time.time()
        await self._store.update_action(plan_id, action_id, ActionStatus.FAILED, error=error)
        await self._audit.log(
            AuditEvent.ACTION_FAILED, plan_id=plan_id, action_id=action_id, error=error
        )
        log.error("action_failed", action_id=action_id, error=error)

    async def _skip_action(
        self, plan_id: str, action_id: str, state: ExecutionState, reason: str
    ) -> None:
        """Mark *action_id* as SKIPPED if it has not already reached a terminal state."""
        action_state = state.get_action(action_id)
        if action_state.status in (
            ActionStatus.PENDING, ActionStatus.WAITING, ActionStatus.AWAITING_APPROVAL
        ):
            action_state.status = ActionStatus.SKIPPED
            action_state.error = reason
            action_state.finished_at = time.time()
            await self._store.update_action(
                plan_id, action_id, ActionStatus.SKIPPED, error=reason
            )
            log.info("action_skipped", action_id=action_id, reason=reason)

    async def _handle_approval(
        self,
        plan_id: str,
        action: IMLAction,
        action_state: ActionState,
        resolved_params: dict[str, Any],
        state: ExecutionState | None = None,
    ) -> dict[str, Any] | None:
        """Wait for user approval and return resolved params (or None on reject/skip).

        Returns:
            The (possibly modified) resolved params on approval, or None if the
            action was rejected, skipped, or the gate is unavailable.
        """
        if self._approval_gate is None:
            # No gate configured — fail the action since we can't wait.
            await self._fail_action(
                plan_id, action.id, action_state,
                "Action requires approval but no approval gate is configured.",
            )
            return None

        # Check auto-approve list (from prior APPROVE_ALWAYS decisions).
        if self._approval_gate.is_auto_approved(action.module, action.action):
            action_state.approval_metadata = {
                "decision": "approve_always",
                "reason": "Auto-approved from prior APPROVE_ALWAYS decision",
                "timestamp": time.time(),
            }
            await self._audit.log(
                AuditEvent.APPROVAL_GRANTED,
                plan_id=plan_id,
                action_id=action.id,
                decision="approve_always",
            )
            log.info("approval_auto_approved", action_id=action.id)
            return resolved_params

        # Build approval request.
        approval_config = action.approval
        risk_level = approval_config.risk_level if approval_config else "medium"
        description = (
            approval_config.message
            if approval_config and approval_config.message
            else f"Execute {action.module}.{action.action}"
        )
        clarification = (
            approval_config.clarification_options
            if approval_config and approval_config.clarification_options
            else []
        )
        request = ApprovalRequest(
            plan_id=plan_id,
            action_id=action.id,
            module=action.module,
            action_name=action.action,
            params=resolved_params,
            risk_level=risk_level,
            description=description,
            requires_approval_reason=(
                "action_flag" if action.requires_approval else "config_rule"
            ),
            clarification_options=clarification,
        )

        # Update state and notify.
        action_state.status = ActionStatus.AWAITING_APPROVAL
        await self._store.update_action(plan_id, action.id, ActionStatus.AWAITING_APPROVAL)
        await self._audit.log(
            AuditEvent.APPROVAL_REQUESTED,
            plan_id=plan_id,
            action_id=action.id,
            module=action.module,
            action=action.action,
            risk_level=risk_level,
        )
        log.info(
            "approval_requested",
            action_id=action.id,
            module=action.module,
            action=action.action,
        )

        # Determine timeout.
        timeout = (
            float(approval_config.timeout_seconds)
            if approval_config and approval_config.timeout_seconds is not None
            else None
        )
        timeout_behavior = (
            approval_config.timeout_behavior
            if approval_config
            else None
        )

        # Wait for the decision.
        response = await self._approval_gate.request_approval(
            request, timeout=timeout, timeout_behavior=timeout_behavior,
        )

        # Process the decision.
        action_state.approval_metadata = response.to_dict()

        if response.decision in (ApprovalDecision.APPROVE, ApprovalDecision.APPROVE_ALWAYS):
            await self._audit.log(
                AuditEvent.APPROVAL_GRANTED,
                plan_id=plan_id,
                action_id=action.id,
                decision=response.decision.value,
                approved_by=response.approved_by,
            )
            log.info(
                "approval_granted",
                action_id=action.id,
                decision=response.decision.value,
            )
            return resolved_params

        if response.decision == ApprovalDecision.MODIFY:
            if response.modified_params:
                resolved_params = response.modified_params
            await self._audit.log(
                AuditEvent.APPROVAL_GRANTED,
                plan_id=plan_id,
                action_id=action.id,
                decision="modify",
                approved_by=response.approved_by,
            )
            log.info("approval_granted_with_modifications", action_id=action.id)
            return resolved_params

        if response.decision == ApprovalDecision.SKIP:
            # Directly update the action state since _skip_action needs ExecutionState.
            action_state.status = ActionStatus.SKIPPED
            action_state.error = f"Skipped by approval decision: {response.reason or 'user requested skip'}"
            action_state.finished_at = time.time()
            await self._store.update_action(
                plan_id, action.id, ActionStatus.SKIPPED,
                error=action_state.error,
            )
            log.info("action_skipped", action_id=action.id, reason=action_state.error)
            await self._audit.log(
                AuditEvent.APPROVAL_REJECTED,
                plan_id=plan_id,
                action_id=action.id,
                decision="skip",
                reason=response.reason,
            )
            return None

        # REJECT (or any unknown decision).
        await self._fail_action(
            plan_id, action.id, action_state,
            f"Approval rejected: {response.reason or 'user rejected action'}",
        )
        await self._audit.log(
            AuditEvent.APPROVAL_REJECTED,
            plan_id=plan_id,
            action_id=action.id,
            decision=response.decision.value,
            reason=response.reason,
        )
        return None
