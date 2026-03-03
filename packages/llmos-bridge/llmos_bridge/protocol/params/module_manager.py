"""Typed parameter models for the ``module_manager`` module."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ListModulesParams(BaseModel):
    module_type: str | None = Field(None, description="Filter by type: 'system' or 'user'")
    state: str | None = Field(None, description="Filter by lifecycle state (e.g. 'active', 'paused')")
    include_health: bool = Field(False, description="Include health check results")


class GetModuleInfoParams(BaseModel):
    module_id: str = Field(..., description="ID of the module to inspect")
    include_health: bool = Field(False, description="Include health check results")
    include_metrics: bool = Field(False, description="Include operational metrics")


class EnableModuleParams(BaseModel):
    module_id: str = Field(..., description="ID of the module to enable/start")


class DisableModuleParams(BaseModel):
    module_id: str = Field(..., description="ID of the module to disable/stop")
    reason: str = Field("", description="Reason for disabling")


class PauseModuleParams(BaseModel):
    module_id: str = Field(..., description="ID of the module to pause")


class ResumeModuleParams(BaseModel):
    module_id: str = Field(..., description="ID of the module to resume")


class RestartModuleParams(BaseModel):
    module_id: str = Field(..., description="ID of the module to restart")
    force: bool = Field(False, description="Force restart even if in error state")


class EnableActionParams(BaseModel):
    module_id: str = Field(..., description="Module containing the action")
    action: str = Field(..., description="Action name to re-enable")


class DisableActionParams(BaseModel):
    module_id: str = Field(..., description="Module containing the action")
    action: str = Field(..., description="Action name to disable")
    reason: str = Field("", description="Reason for disabling")


class GetModuleHealthParams(BaseModel):
    module_id: str = Field(..., description="ID of the module to health-check")


class GetModuleMetricsParams(BaseModel):
    module_id: str = Field(..., description="ID of the module")


class GetModuleStateParams(BaseModel):
    module_id: str = Field(..., description="ID of the module")


class ListServicesParams(BaseModel):
    pass


class GetSystemStatusParams(BaseModel):
    include_health: bool = Field(False, description="Include per-module health checks")


class UpdateModuleConfigParams(BaseModel):
    module_id: str = Field(..., description="ID of the module to configure")
    config: dict[str, Any] = Field(..., description="Configuration dict to apply")


# --- v3: Hub / Package Manager actions ---


class InstallModuleParams(BaseModel):
    source: str = Field("hub", description="'hub' or 'local'")
    module_id: str = Field("", description="Module ID (for hub install)")
    path: str = Field("", description="Local path (for local install)")
    version: str = Field("latest", description="Version constraint")


class UninstallModuleParams(BaseModel):
    module_id: str = Field(..., description="Module to uninstall")


class UpgradeModuleParams(BaseModel):
    module_id: str = Field(..., description="Module to upgrade")
    path: str = Field(..., description="Path to new version package directory")


class SearchHubParams(BaseModel):
    query: str = Field(..., description="Search query")
    limit: int = Field(20, description="Max results to return")


class ListInstalledParams(BaseModel):
    enabled_only: bool = Field(False, description="Only show enabled modules")


class VerifyModuleParams(BaseModel):
    module_id: str = Field(..., description="Module to verify integrity of")


class DescribeModuleParams(BaseModel):
    module_id: str = Field(..., description="Module to get dynamic description for")


PARAMS_MAP: dict[str, type[BaseModel]] = {
    "list_modules": ListModulesParams,
    "get_module_info": GetModuleInfoParams,
    "enable_module": EnableModuleParams,
    "disable_module": DisableModuleParams,
    "pause_module": PauseModuleParams,
    "resume_module": ResumeModuleParams,
    "restart_module": RestartModuleParams,
    "enable_action": EnableActionParams,
    "disable_action": DisableActionParams,
    "get_module_health": GetModuleHealthParams,
    "get_module_metrics": GetModuleMetricsParams,
    "get_module_state": GetModuleStateParams,
    "list_services": ListServicesParams,
    "get_system_status": GetSystemStatusParams,
    "update_module_config": UpdateModuleConfigParams,
    # v3 Hub actions
    "install_module": InstallModuleParams,
    "uninstall_module": UninstallModuleParams,
    "upgrade_module": UpgradeModuleParams,
    "search_hub": SearchHubParams,
    "list_installed": ListInstalledParams,
    "verify_module": VerifyModuleParams,
    "describe_module": DescribeModuleParams,
}
