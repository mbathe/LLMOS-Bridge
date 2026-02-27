"""API routes — Intent Verifier introspection and management.

Provides endpoints to:
  - Check verifier status and configuration
  - Manually verify a plan (dry-run)
  - List, register, and remove threat categories
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from llmos_bridge.api.dependencies import IntentVerifierDep

router = APIRouter(prefix="/intent-verifier", tags=["intent-verifier"])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class VerifyPlanRequest(BaseModel):
    """Request body for manual plan verification."""

    plan: dict[str, Any] = Field(description="Full IML plan JSON to verify.")


class RegisterCategoryRequest(BaseModel):
    """Request body for registering a custom threat category."""

    id: str = Field(description="Unique identifier for the category.")
    name: str = Field(description="Human-readable name.")
    description: str = Field(description="Detection guidance text for the system prompt.")
    threat_type: str = Field(default="custom", description="Threat type for classification.")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/status")
async def get_status(intent_verifier: IntentVerifierDep) -> dict[str, Any]:
    """Get the current intent verifier status and configuration."""
    if intent_verifier is None:
        return {
            "enabled": False,
            "reason": "IntentVerifier not configured.",
        }
    return intent_verifier.status()


@router.post("/verify")
async def verify_plan(
    body: VerifyPlanRequest,
    intent_verifier: IntentVerifierDep,
) -> dict[str, Any]:
    """Manually verify an IML plan without executing it (dry-run).

    Useful for previewing what the security analysis would flag before
    submitting the plan for execution.
    """
    if intent_verifier is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="IntentVerifier not configured.",
        )

    from llmos_bridge.protocol.models import IMLPlan

    try:
        plan = IMLPlan(**body.plan)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid IML plan: {exc}",
        ) from exc

    result = await intent_verifier.verify_plan(plan)
    return result.model_dump()


@router.get("/categories")
async def list_categories(intent_verifier: IntentVerifierDep) -> list[dict[str, Any]]:
    """List all threat categories (built-in + custom)."""
    if intent_verifier is None:
        return []

    registry = intent_verifier.category_registry
    if registry is None:
        return []

    return registry.to_dict_list()


@router.post("/categories", status_code=status.HTTP_201_CREATED)
async def register_category(
    body: RegisterCategoryRequest,
    intent_verifier: IntentVerifierDep,
) -> dict[str, Any]:
    """Register a custom threat category at runtime."""
    if intent_verifier is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="IntentVerifier not configured.",
        )

    registry = intent_verifier.category_registry
    if registry is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ThreatCategoryRegistry not configured.",
        )

    from llmos_bridge.security.intent_verifier import ThreatType
    from llmos_bridge.security.threat_categories import ThreatCategory

    # Map threat_type string to enum (fallback to CUSTOM)
    try:
        threat_type = ThreatType(body.threat_type)
    except ValueError:
        threat_type = ThreatType.CUSTOM

    category = ThreatCategory(
        id=body.id,
        name=body.name,
        description=body.description,
        threat_type=threat_type,
        enabled=True,
        builtin=False,
    )
    registry.register(category)

    return category.to_dict()


@router.delete("/categories/{category_id}")
async def remove_category(
    category_id: str,
    intent_verifier: IntentVerifierDep,
) -> dict[str, Any]:
    """Remove a threat category (custom categories only)."""
    if intent_verifier is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="IntentVerifier not configured.",
        )

    registry = intent_verifier.category_registry
    if registry is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ThreatCategoryRegistry not configured.",
        )

    category = registry.get(category_id)
    if category is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Category '{category_id}' not found.",
        )

    if category.builtin:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot remove built-in category '{category_id}'. Use disable instead.",
        )

    registry.unregister(category_id)
    return {"removed": category_id}
