"""POST /plans, GET /plans, GET /plans/{id}, DELETE /plans/{id}"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, status

from llmos_bridge.api.dependencies import (
    ApprovalGateDep,
    AuthDep,
    ConfigDep,
    ExecutorDep,
    RecorderDep,
    StateStoreDep,
)
from llmos_bridge.api.schemas import (
    ActionResponse,
    ApprovalDecisionResponse,
    ApprovalRequestResponse,
    ApprovePlanActionRequest,
    PlanListResponse,
    PlanResponse,
    SubmitPlanRequest,
    SubmitPlanResponse,
)
from llmos_bridge.exceptions import IMLParseError, IMLValidationError, LLMOSError
from llmos_bridge.logging import get_logger
from llmos_bridge.orchestration.approval import ApprovalDecision, ApprovalResponse
from llmos_bridge.orchestration.state import PlanStateStore
from llmos_bridge.protocol.models import ActionStatus, PlanStatus
from llmos_bridge.protocol.parser import IMLParser
from llmos_bridge.protocol.validator import IMLValidator

log = get_logger(__name__)
router = APIRouter(prefix="/plans", tags=["plans"])

_parser = IMLParser()
_validator = IMLValidator()

# In-memory map of plan_id -> asyncio.Task for cancellation support.
_running_tasks: dict[str, asyncio.Task[Any]] = {}


async def _record_plan(recorder: Any, recording_id: str, plan_data: dict[str, Any], exec_state: Any) -> None:
    """Silently append a completed plan to an active recording. Never raises."""
    try:
        await recorder.add_plan(
            recording_id,
            plan_data,
            exec_state.plan_status.value,
            len(exec_state.actions),
        )
    except Exception as exc:
        log.warning("recording_add_plan_failed", recording_id=recording_id, error=str(exc))


@router.post(
    "",
    response_model=SubmitPlanResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit an IML plan for execution",
)
async def submit_plan(
    body: SubmitPlanRequest,
    background_tasks: BackgroundTasks,
    _auth: AuthDep,
    executor: ExecutorDep,
    store: StateStoreDep,
    config: ConfigDep,
    recorder: RecorderDep,
) -> SubmitPlanResponse:
    try:
        plan = _parser.parse(body.plan)
        _validator.validate(plan)
    except IMLParseError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except IMLValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"message": exc.message, "errors": exc.errors},
        )

    # Snapshot active recording ID at submission time (before any await).
    active_recording_id = recorder.active_recording_id if recorder is not None else None

    if body.async_execution:
        async def _run_async() -> None:
            exec_state = await executor.run(plan)
            if recorder is not None and active_recording_id:
                await _record_plan(recorder, active_recording_id, body.plan, exec_state)

        task = asyncio.create_task(_run_async())
        _running_tasks[plan.plan_id] = task
        task.add_done_callback(lambda t: _running_tasks.pop(plan.plan_id, None))
        return SubmitPlanResponse(
            plan_id=plan.plan_id,
            status=PlanStatus.PENDING,
            message="Plan accepted. Use GET /plans/{plan_id} to poll status.",
        )
    else:
        timeout = config.server.sync_plan_timeout
        try:
            exec_state = await asyncio.wait_for(executor.run(plan), timeout=float(timeout))
            if recorder is not None and active_recording_id:
                await _record_plan(recorder, active_recording_id, body.plan, exec_state)
            # Build action responses for sync callers (SDK tools need results).
            action_responses = [
                ActionResponse(
                    action_id=a.action_id,
                    module=a.fallback_module or a.module,
                    action=a.action,
                    status=a.status,
                    started_at=a.started_at,
                    finished_at=a.finished_at,
                    result=a.result,
                    error=a.error,
                    alternatives=a.alternatives,
                    attempt=a.attempt,
                    approval_metadata=a.approval_metadata,
                )
                for a in exec_state.actions.values()
            ]
            return SubmitPlanResponse(
                plan_id=plan.plan_id,
                status=exec_state.plan_status,
                message=f"Plan finished with status: {exec_state.plan_status.value}",
                actions=action_responses,
                rejection_details=exec_state.rejection_details,
            )
        except asyncio.TimeoutError:
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail=f"Synchronous execution timed out after {timeout}s. Use async_execution=true.",
            )
        except LLMOSError as exc:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.get("", response_model=PlanListResponse, summary="List plans")
async def list_plans(
    _auth: AuthDep,
    store: StateStoreDep,
    plan_status: PlanStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=500),
    page: int = Query(default=1, ge=1),
) -> PlanListResponse:
    plans = await store.list_plans(status=plan_status, limit=limit)
    return PlanListResponse(
        plans=plans,
        total=len(plans),
        page=page,
        per_page=limit,
    )


@router.get("/{plan_id}", response_model=PlanResponse, summary="Get plan status")
async def get_plan(
    plan_id: str,
    _auth: AuthDep,
    store: StateStoreDep,
) -> PlanResponse:
    state = await store.get(plan_id)
    if state is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Plan '{plan_id}' not found.",
        )

    actions = [
        ActionResponse(
            action_id=a.action_id,
            module=a.fallback_module or a.module,
            action=a.action,
            status=a.status,
            started_at=a.started_at,
            finished_at=a.finished_at,
            result=a.result,
            error=a.error,
            alternatives=a.alternatives,
            attempt=a.attempt,
            approval_metadata=a.approval_metadata,
        )
        for a in state.actions.values()
    ]

    return PlanResponse(
        plan_id=state.plan_id,
        status=state.plan_status,
        created_at=state.created_at,
        updated_at=state.updated_at,
        actions=actions,
        rejection_details=state.rejection_details,
    )


@router.delete(
    "/{plan_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Cancel a running plan",
    response_model=None,
)
async def cancel_plan(
    plan_id: str,
    _auth: AuthDep,
    store: StateStoreDep,
) -> None:
    task = _running_tasks.get(plan_id)
    if task and not task.done():
        task.cancel()
        log.info("plan_cancelled_via_api", plan_id=plan_id)

    state = await store.get(plan_id)
    if state is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Plan '{plan_id}' not found.",
        )
    await store.update_plan_status(plan_id, PlanStatus.CANCELLED)


@router.post(
    "/{plan_id}/actions/{action_id}/approve",
    response_model=ApprovalDecisionResponse,
    summary="Approve or reject an action awaiting approval",
)
async def approve_action(
    plan_id: str,
    action_id: str,
    body: ApprovePlanActionRequest,
    _auth: AuthDep,
    gate: ApprovalGateDep,
) -> ApprovalDecisionResponse:
    # Resolve the effective decision (support legacy 'approved' field).
    if body.approved is not None and body.decision == "approve":
        # Legacy mode — translate bool to decision string.
        decision_str = "approve" if body.approved else "reject"
    else:
        decision_str = body.decision

    try:
        decision = ApprovalDecision(decision_str)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid decision '{decision_str}'. Must be one of: "
            f"{', '.join(d.value for d in ApprovalDecision)}.",
        )

    response = ApprovalResponse(
        decision=decision,
        modified_params=body.modified_params,
        reason=body.reason,
        approved_by=body.approved_by,
    )

    applied = gate.submit_decision(plan_id, action_id, response)
    if not applied:
        raise HTTPException(
            status_code=409,
            detail=f"Action '{action_id}' in plan '{plan_id}' is not pending approval.",
        )

    log.info(
        "approval_decision_submitted",
        plan_id=plan_id,
        action_id=action_id,
        decision=decision_str,
    )
    return ApprovalDecisionResponse(
        plan_id=plan_id,
        action_id=action_id,
        decision=decision_str,
        applied=True,
    )


@router.get(
    "/{plan_id}/pending-approvals",
    response_model=list[ApprovalRequestResponse],
    summary="List pending approval requests for a plan",
)
async def list_pending_approvals(
    plan_id: str,
    _auth: AuthDep,
    gate: ApprovalGateDep,
) -> list[ApprovalRequestResponse]:
    pending = gate.get_pending(plan_id=plan_id)
    return [
        ApprovalRequestResponse(
            plan_id=req.plan_id,
            action_id=req.action_id,
            module=req.module,
            action=req.action_name,
            params=req.params,
            risk_level=req.risk_level,
            description=req.description,
            requires_approval_reason=req.requires_approval_reason,
            clarification_options=req.clarification_options,
            requested_at=req.requested_at,
        )
        for req in pending
    ]
