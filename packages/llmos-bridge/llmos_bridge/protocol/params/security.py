"""Typed parameter models for the ``security`` module."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ListPermissionsParams(BaseModel):
    module_id: str | None = Field(None, description="Filter by module ID")


class CheckPermissionParams(BaseModel):
    permission: str = Field(..., description="Permission identifier (e.g. 'filesystem.write')")
    module_id: str = Field(..., description="Module to check permission for")


class RequestPermissionParams(BaseModel):
    permission: str = Field(..., description="Permission identifier to request")
    module_id: str = Field(..., description="Module requesting the permission")
    reason: str = Field("", description="Why this permission is needed")
    scope: str = Field("session", description="Grant scope: 'session' or 'permanent'")


class RevokePermissionParams(BaseModel):
    permission: str = Field(..., description="Permission identifier to revoke")
    module_id: str = Field(..., description="Module to revoke permission from")


class GetSecurityStatusParams(BaseModel):
    pass


class ListAuditEventsParams(BaseModel):
    limit: int = Field(50, description="Maximum events to return", ge=1, le=1000)


PARAMS_MAP: dict[str, type[BaseModel]] = {
    "list_permissions": ListPermissionsParams,
    "check_permission": CheckPermissionParams,
    "request_permission": RequestPermissionParams,
    "revoke_permission": RevokePermissionParams,
    "get_security_status": GetSecurityStatusParams,
    "list_audit_events": ListAuditEventsParams,
}
