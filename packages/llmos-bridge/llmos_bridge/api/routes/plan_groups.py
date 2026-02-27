"""POST /plan-groups — Submit multiple plans for parallel execution."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status

from llmos_bridge.api.dependencies import AuthDep, ExecutorDep
from llmos_bridge.api.schemas import PlanGroupResponse, SubmitPlanGroupRequest
from llmos_bridge.exceptions import IMLParseError, IMLValidationError
from llmos_bridge.logging import get_logger
from llmos_bridge.orchestration.plan_group import PlanGroupExecutor
from llmos_bridge.protocol.parser import IMLParser
from llmos_bridge.protocol.validator import IMLValidator

log = get_logger(__name__)
router = APIRouter(prefix="/plan-groups", tags=["plan-groups"])

_parser = IMLParser()
_validator = IMLValidator()


@router.post(
    "",
    status_code=status.HTTP_200_OK,
    response_model=PlanGroupResponse,
    summary="Execute multiple plans in parallel",
)
async def submit_plan_group(
    body: SubmitPlanGroupRequest,
    _auth: AuthDep,
    executor: ExecutorDep,
) -> PlanGroupResponse:
    """Parse, validate, and execute all plans concurrently.

    Returns aggregated results when all plans finish (or timeout).
    """
    # Parse & validate all plans upfront before executing any.
    from llmos_bridge.protocol.models import IMLPlan

    parsed_plans: list[IMLPlan] = []
    for idx, plan_data in enumerate(body.plans):
        try:
            plan = _parser.parse(plan_data)
            _validator.validate(plan)
            parsed_plans.append(plan)
        except (IMLParseError, IMLValidationError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Plan #{idx} validation failed: {exc}",
            ) from exc

    group_exec = PlanGroupExecutor(executor)
    result = await group_exec.execute(
        plans=parsed_plans,
        group_id=body.group_id,
        max_concurrent=body.max_concurrent,
        timeout=float(body.timeout),
    )

    # Serialize plan results to dicts for JSON response.
    serialized_results: dict[str, Any] = {}
    for plan_id, state in result.plan_results.items():
        serialized_results[plan_id] = {
            "status": state.plan_status.value if hasattr(state.plan_status, "value") else str(state.plan_status),
            "actions": len(state.actions),
        }

    return PlanGroupResponse(
        group_id=result.group_id,
        status=result.status,
        summary=result.summary,
        results=serialized_results,
        errors=result.errors,
        duration=result.duration,
    )
