"""API routes — Security administration for dashboard."""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

from llmos_bridge.api.dependencies import AuthDep, RegistryDep, SecurityManagerDep
from llmos_bridge.api.schemas import (
    IntentTestRequest,
    PatternAddRequest,
    PermissionGrantRequest,
    PermissionRevokeRequest,
)

router = APIRouter(prefix="/admin/security", tags=["admin-security"])


def _get_security_module(registry):
    """Get the SecurityModule instance if available."""
    if not registry.is_available("security"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Security module not available (enable_decorators may be off).",
        )
    return registry.get("security")


@router.get("/permissions")
async def list_permissions(
    registry: RegistryDep,
    _auth: AuthDep,
    module_id: str | None = None,
    app_id: str | None = None,
) -> dict[str, Any]:
    """List all granted permissions (global admin view across all applications).

    Query params:
      - ``module_id``: filter by module
      - ``app_id``: filter by application (omit to see all apps)
    """
    sec = _get_security_module(registry)
    pm = sec._security_manager.permission_manager if sec._security_manager else None
    if pm is None:
        return {"grants": [], "count": 0}

    if app_id:
        grants = await pm.store.get_for_app(app_id)
    elif module_id:
        grants = await pm.store.get_all()
        grants = [g for g in grants if g.module_id == module_id]
    else:
        grants = await pm.store.get_all()

    return {
        "grants": [g.to_dict() for g in grants],
        "count": len(grants),
    }


@router.get("/permissions/check")
async def check_permission(
    registry: RegistryDep,
    _auth: AuthDep,
    permission: str = "",
    module_id: str = "",
) -> dict[str, Any]:
    """Check if a specific permission is granted."""
    sec = _get_security_module(registry)
    if not permission or not module_id:
        raise HTTPException(400, "Both 'permission' and 'module_id' query params required.")
    return await sec._action_check_permission({
        "permission": permission,
        "module_id": module_id,
    })


@router.post("/permissions/grant")
async def grant_permission(
    body: PermissionGrantRequest,
    registry: RegistryDep,
    _auth: AuthDep,
) -> dict[str, Any]:
    """Grant a permission to a module."""
    sec = _get_security_module(registry)
    return await sec._action_request_permission({
        "permission": body.permission,
        "module_id": body.module_id,
        "reason": body.reason,
        "scope": body.scope,
    })


@router.delete("/permissions/revoke")
async def revoke_permission(
    body: PermissionRevokeRequest,
    registry: RegistryDep,
    _auth: AuthDep,
) -> dict[str, Any]:
    """Revoke a permission from a module."""
    sec = _get_security_module(registry)
    return await sec._action_revoke_permission({
        "permission": body.permission,
        "module_id": body.module_id,
    })


@router.get("/status")
async def get_security_status(
    registry: RegistryDep,
    _auth: AuthDep,
) -> dict[str, Any]:
    """Get security overview."""
    sec = _get_security_module(registry)
    return await sec._action_get_security_status({})


@router.get("/audit")
async def get_audit_events(
    _auth: AuthDep,
    request: Request,
    limit: int = 50,
    topic: str | None = None,
) -> dict[str, Any]:
    """Query recent audit events."""
    event_bus = getattr(request.app.state, "audit_logger", None)
    if event_bus is None:
        return {"events": [], "count": 0}

    # Get events from the event bus ring buffer.
    bus = getattr(event_bus, "_bus", None)
    if bus is None:
        return {"events": [], "count": 0}

    recent = list(getattr(bus, "_recent_events", []))
    if topic:
        recent = [e for e in recent if e.get("_topic") == topic]
    recent = recent[-limit:]
    return {"events": recent, "count": len(recent)}


# ---------------------------------------------------------------------------
# Security Layers (architecture overview)
# ---------------------------------------------------------------------------


@router.get("/layers")
async def get_security_layers(
    request: Request,
    _auth: AuthDep,
) -> dict[str, Any]:
    """Full security architecture: all layers with status and config summary."""
    settings = request.app.state.settings
    pipeline = getattr(request.app.state, "scanner_pipeline", None)
    verifier = getattr(request.app.state, "intent_verifier", None)
    sec_mgr = getattr(request.app.state, "security_manager", None)

    sec_cfg = settings.security_advanced
    iv_cfg = settings.intent_verifier

    # Layer 1-2: Scanner Pipeline
    scanner_layer: dict[str, Any] = {
        "id": "scanner_pipeline",
        "name": "Scanner Pipeline",
        "order": 1,
        "enabled": pipeline is not None and getattr(pipeline, "enabled", False),
        "description": "Fast heuristic + ML-based scanning (<1ms)",
        "config": {
            "fail_fast": settings.scanner_pipeline.fail_fast,
            "reject_threshold": settings.scanner_pipeline.reject_threshold,
            "warn_threshold": settings.scanner_pipeline.warn_threshold,
        },
        "stats": {},
    }
    if pipeline is not None:
        try:
            scanners = pipeline.registry.list_all()
            enabled_scanners = pipeline.registry.list_enabled()
            heuristic = pipeline.registry.get("heuristic")
            scanner_layer["stats"] = {
                "scanners_active": len(enabled_scanners),
                "scanners_total": len(scanners),
                "patterns_enabled": (
                    len([p for p in heuristic.patterns if p.enabled]) if heuristic else 0
                ),
                "patterns_total": len(heuristic.patterns) if heuristic else 0,
            }
        except Exception:
            scanner_layer["stats"] = {"error": "failed to read scanner stats"}

    # Layer 3: Intent Verifier (LLM)
    verifier_layer: dict[str, Any] = {
        "id": "intent_verifier",
        "name": "Intent Verifier (LLM)",
        "order": 2,
        "enabled": verifier is not None and getattr(verifier, "enabled", False),
        "description": "LLM-based threat analysis",
        "config": {
            "provider": iv_cfg.provider,
            "model": iv_cfg.model,
            "strict": iv_cfg.strict,
            "cache_size": iv_cfg.cache_size,
        },
        "stats": {},
    }
    if verifier is not None:
        try:
            verifier_status = verifier.status()
            verifier_layer["stats"] = {
                "cache_entries": verifier_status.get("cache_entries", 0),
                "threat_categories": len(verifier_status.get("threat_categories", [])),
            }
        except Exception:
            verifier_layer["stats"] = {"error": "failed to read verifier stats"}

    # Layer 4: Permission System
    perm_layer: dict[str, Any] = {
        "id": "permission_system",
        "name": "Permission System",
        "order": 3,
        "enabled": sec_cfg.enable_decorators,
        "description": "OS-level permission grants + rate limiting + audit",
        "config": {
            "profile": settings.security.permission_profile,
            "auto_grant_low_risk": sec_cfg.auto_grant_low_risk,
            "rate_limiting": sec_cfg.enable_rate_limiting,
        },
        "stats": {},
    }
    if sec_mgr is not None:
        try:
            all_grants = await sec_mgr.permission_manager.store.list_all()
            perm_layer["stats"]["permissions_count"] = len(all_grants)
        except Exception:
            perm_layer["stats"]["permissions_count"] = 0

    # Layer 5: Output Sanitizer
    sanitizer_layer: dict[str, Any] = {
        "id": "output_sanitizer",
        "name": "Output Sanitizer",
        "order": 4,
        "enabled": True,
        "description": "Prevents prompt injection in module outputs",
        "config": {
            "max_output_length": 50000,
            "max_depth": 10,
        },
    }

    return {
        "layers": [scanner_layer, verifier_layer, perm_layer, sanitizer_layer],
        "profile": settings.security.permission_profile,
        "decorators_enabled": sec_cfg.enable_decorators,
    }


# ---------------------------------------------------------------------------
# Intent Verifier endpoints
# ---------------------------------------------------------------------------


@router.get("/intent-verifier/status")
async def get_intent_verifier_status(
    request: Request,
    _auth: AuthDep,
) -> dict[str, Any]:
    """Detailed intent verifier status."""
    verifier = getattr(request.app.state, "intent_verifier", None)
    if verifier is None:
        return {"enabled": False, "provider": "null", "message": "Not configured"}
    result = verifier.status()
    # Inject provider from config (not stored on IntentVerifier itself).
    result["provider"] = request.app.state.settings.intent_verifier.provider
    return result


@router.post("/intent-verifier/test")
async def test_intent_verifier(
    body: IntentTestRequest,
    request: Request,
    _auth: AuthDep,
) -> dict[str, Any]:
    """Test a text against the intent verifier (dry run)."""
    verifier = getattr(request.app.state, "intent_verifier", None)
    if verifier is None:
        return {"error": "Intent verifier not configured"}

    from llmos_bridge.protocol.models import IMLAction, IMLPlan

    # Build a synthetic single-action plan from the text.
    plan = IMLPlan(
        plan_id="test-verification",
        description=body.text[:200],
        actions=[
            IMLAction(
                id="test-1",
                module="os_exec",
                action="run_command",
                params={"command": ["echo", body.text]},
            )
        ],
    )
    result = await verifier.verify_plan(plan)
    return result.model_dump()


@router.post("/intent-verifier/cache/clear")
async def clear_intent_verifier_cache(
    request: Request,
    _auth: AuthDep,
) -> dict[str, Any]:
    """Clear the intent verifier LRU cache."""
    verifier = getattr(request.app.state, "intent_verifier", None)
    if verifier is None:
        raise HTTPException(status_code=404, detail="Intent verifier not configured")
    verifier.clear_cache()
    return {"cleared": True}


# ---------------------------------------------------------------------------
# Pattern management (heuristic scanner)
# ---------------------------------------------------------------------------


def _get_heuristic_scanner(request: Request):
    """Resolve the HeuristicScanner from the pipeline registry."""
    pipeline = getattr(request.app.state, "scanner_pipeline", None)
    if pipeline is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Scanner pipeline not available.",
        )
    heuristic = pipeline.registry.get("heuristic")
    if heuristic is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Heuristic scanner not registered.",
        )
    return heuristic


@router.get("/scanners/patterns")
async def list_patterns(
    request: Request,
    _auth: AuthDep,
) -> dict[str, Any]:
    """List all heuristic pattern rules."""
    heuristic = _get_heuristic_scanner(request)
    patterns = []
    categories: set[str] = set()
    for p in heuristic.patterns:
        patterns.append({
            "id": p.id,
            "category": p.category,
            "severity": p.severity,
            "description": p.description,
            "enabled": p.enabled,
        })
        categories.add(p.category)
    return {"patterns": patterns, "categories": sorted(categories)}


@router.post("/scanners/patterns/{pattern_id}/enable")
async def enable_pattern(
    pattern_id: str,
    request: Request,
    _auth: AuthDep,
) -> dict[str, Any]:
    """Enable a specific heuristic pattern."""
    heuristic = _get_heuristic_scanner(request)
    found = heuristic.enable_pattern(pattern_id)
    if not found:
        raise HTTPException(status_code=404, detail=f"Pattern '{pattern_id}' not found")
    return {"id": pattern_id, "enabled": True}


@router.post("/scanners/patterns/{pattern_id}/disable")
async def disable_pattern(
    pattern_id: str,
    request: Request,
    _auth: AuthDep,
) -> dict[str, Any]:
    """Disable a specific heuristic pattern."""
    heuristic = _get_heuristic_scanner(request)
    found = heuristic.disable_pattern(pattern_id)
    if not found:
        raise HTTPException(status_code=404, detail=f"Pattern '{pattern_id}' not found")
    return {"id": pattern_id, "enabled": False}


@router.post("/scanners/patterns")
async def add_pattern(
    body: PatternAddRequest,
    request: Request,
    _auth: AuthDep,
) -> dict[str, Any]:
    """Add a custom heuristic pattern at runtime."""
    heuristic = _get_heuristic_scanner(request)

    # Validate regex.
    try:
        compiled = re.compile(body.pattern, re.IGNORECASE)
    except re.error as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid regex: {exc}",
        )

    # Check for duplicate ID.
    for existing in heuristic.patterns:
        if existing.id == body.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Pattern '{body.id}' already exists.",
            )

    from llmos_bridge.security.scanners.heuristic import PatternRule

    rule = PatternRule(
        id=body.id,
        category=body.category,
        pattern=compiled,
        severity=body.severity,
        description=body.description,
    )
    heuristic.add_pattern(rule)
    return {"id": body.id, "added": True}
