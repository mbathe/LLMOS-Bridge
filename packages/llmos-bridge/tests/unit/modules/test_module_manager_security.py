"""Tests — ModuleManagerModule security decorator metadata coverage.

Validates that all 22 actions in ModuleManagerModule carry the correct
security decorator metadata (permissions, risk levels, audit levels,
rate limits, data classifications).
"""
from __future__ import annotations

from typing import Any

import pytest

from llmos_bridge.modules.module_manager.module import ModuleManagerModule
from llmos_bridge.security.decorators import collect_security_metadata


@pytest.mark.unit
class TestModuleManagerSecurity:
    """Security decorator metadata on ModuleManagerModule actions."""

    def setup_method(self) -> None:
        self.module = ModuleManagerModule()
        # No external dependencies needed for pure metadata introspection.
        self.module._check_dependencies = lambda: None

    # -- helper --------------------------------------------------------------

    def _meta(self, action_name: str) -> dict[str, Any]:
        fn = getattr(self.module, f"_action_{action_name}")
        return collect_security_metadata(fn)

    # ------------------------------------------------------------------ #
    # Group 1: Read-only actions (11) — MODULE_READ, no risk metadata     #
    # ------------------------------------------------------------------ #

    _READ_ONLY_ACTIONS = [
        "list_modules",
        "get_module_info",
        "get_module_health",
        "get_module_metrics",
        "get_module_state",
        "list_services",
        "get_system_status",
        "describe_module",
        "search_hub",
        "list_installed",
        "verify_module",
    ]

    @pytest.mark.parametrize("action_name", _READ_ONLY_ACTIONS)
    def test_read_only_has_module_read_permission(self, action_name: str) -> None:
        meta = self._meta(action_name)
        assert "module.read" in meta["permissions"], (
            f"{action_name} should require module.read"
        )

    @pytest.mark.parametrize("action_name", _READ_ONLY_ACTIONS)
    def test_read_only_has_no_risk_level(self, action_name: str) -> None:
        meta = self._meta(action_name)
        assert "risk_level" not in meta, (
            f"{action_name} should not have risk_level (no @sensitive_action)"
        )

    # ------------------------------------------------------------------ #
    # Group 2: Lifecycle MEDIUM risk (6 actions)                          #
    # ------------------------------------------------------------------ #

    _LIFECYCLE_MEDIUM_ACTIONS = [
        "enable_module",
        "disable_module",
        "pause_module",
        "resume_module",
        "enable_action",
        "disable_action",
    ]

    @pytest.mark.parametrize("action_name", _LIFECYCLE_MEDIUM_ACTIONS)
    def test_lifecycle_medium_has_module_manage_permission(self, action_name: str) -> None:
        meta = self._meta(action_name)
        assert "module.manage" in meta["permissions"], (
            f"{action_name} should require module.manage"
        )

    @pytest.mark.parametrize("action_name", _LIFECYCLE_MEDIUM_ACTIONS)
    def test_lifecycle_medium_risk_level(self, action_name: str) -> None:
        meta = self._meta(action_name)
        assert meta["risk_level"] == "medium", (
            f"{action_name} should be medium risk"
        )

    @pytest.mark.parametrize("action_name", _LIFECYCLE_MEDIUM_ACTIONS)
    def test_lifecycle_medium_audit_level(self, action_name: str) -> None:
        meta = self._meta(action_name)
        assert meta["audit_level"] == "standard", (
            f"{action_name} should have standard audit"
        )

    # ------------------------------------------------------------------ #
    # Group 3: Config update (1 action)                                   #
    # ------------------------------------------------------------------ #

    def test_update_module_config_permission(self) -> None:
        meta = self._meta("update_module_config")
        assert "module.manage" in meta["permissions"]

    def test_update_module_config_risk_level(self) -> None:
        meta = self._meta("update_module_config")
        assert meta["risk_level"] == "medium"

    def test_update_module_config_audit_level(self) -> None:
        meta = self._meta("update_module_config")
        assert meta["audit_level"] == "detailed"

    def test_update_module_config_data_classification(self) -> None:
        meta = self._meta("update_module_config")
        assert meta["data_classification"] == "internal"

    # ------------------------------------------------------------------ #
    # Group 4: Restart (1 action)                                         #
    # ------------------------------------------------------------------ #

    def test_restart_module_permission(self) -> None:
        meta = self._meta("restart_module")
        assert "module.manage" in meta["permissions"]

    def test_restart_module_risk_level(self) -> None:
        meta = self._meta("restart_module")
        assert meta["risk_level"] == "high"

    def test_restart_module_audit_level(self) -> None:
        meta = self._meta("restart_module")
        assert meta["audit_level"] == "standard"

    def test_restart_module_rate_limit(self) -> None:
        meta = self._meta("restart_module")
        assert meta["rate_limit"]["calls_per_minute"] == 10

    # ------------------------------------------------------------------ #
    # Group 5: Hub install/uninstall/upgrade (3 actions)                  #
    # ------------------------------------------------------------------ #

    _HUB_ACTIONS = [
        "install_module",
        "uninstall_module",
        "upgrade_module",
    ]

    @pytest.mark.parametrize("action_name", _HUB_ACTIONS)
    def test_hub_has_module_install_permission(self, action_name: str) -> None:
        meta = self._meta(action_name)
        assert "module.install" in meta["permissions"], (
            f"{action_name} should require module.install"
        )

    @pytest.mark.parametrize("action_name", _HUB_ACTIONS)
    def test_hub_risk_level(self, action_name: str) -> None:
        meta = self._meta(action_name)
        assert meta["risk_level"] == "high", (
            f"{action_name} should be high risk"
        )

    @pytest.mark.parametrize("action_name", _HUB_ACTIONS)
    def test_hub_audit_level(self, action_name: str) -> None:
        meta = self._meta(action_name)
        assert meta["audit_level"] == "detailed", (
            f"{action_name} should have detailed audit"
        )

    @pytest.mark.parametrize("action_name", _HUB_ACTIONS)
    def test_hub_rate_limit(self, action_name: str) -> None:
        meta = self._meta(action_name)
        assert meta["rate_limit"]["calls_per_minute"] == 5, (
            f"{action_name} should be rate-limited to 5/min"
        )

    # ------------------------------------------------------------------ #
    # Group 6: Completeness — every _action_* has security metadata       #
    # ------------------------------------------------------------------ #

    def test_all_actions_have_security_metadata(self) -> None:
        """Every _action_* method must carry at least one security decorator."""
        missing: list[str] = []
        for attr_name in dir(self.module):
            if not attr_name.startswith("_action_"):
                continue
            method = getattr(self.module, attr_name)
            meta = collect_security_metadata(method)
            if not meta:
                missing.append(attr_name)
        assert missing == [], (
            f"Actions without security metadata: {missing}"
        )
