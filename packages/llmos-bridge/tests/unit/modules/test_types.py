"""Tests for modules.types — ModuleType, ModuleState, SYSTEM_MODULE_IDS."""

from __future__ import annotations

import pytest

from llmos_bridge.modules.types import (
    ModuleState,
    ModuleType,
    SYSTEM_MODULE_IDS,
    VALID_TRANSITIONS,
)


@pytest.mark.unit
class TestModuleType:
    def test_system_value(self):
        assert ModuleType.SYSTEM == "system"

    def test_user_value(self):
        assert ModuleType.USER == "user"

    def test_is_string_enum(self):
        assert isinstance(ModuleType.SYSTEM, str)
        assert isinstance(ModuleType.USER, str)

    def test_all_members(self):
        assert set(ModuleType) == {ModuleType.SYSTEM, ModuleType.USER}


@pytest.mark.unit
class TestModuleState:
    def test_loaded(self):
        assert ModuleState.LOADED == "loaded"

    def test_starting(self):
        assert ModuleState.STARTING == "starting"

    def test_active(self):
        assert ModuleState.ACTIVE == "active"

    def test_paused(self):
        assert ModuleState.PAUSED == "paused"

    def test_stopping(self):
        assert ModuleState.STOPPING == "stopping"

    def test_disabled(self):
        assert ModuleState.DISABLED == "disabled"

    def test_error(self):
        assert ModuleState.ERROR == "error"

    def test_all_states(self):
        expected = {"loaded", "starting", "active", "paused", "stopping", "disabled", "error"}
        assert {s.value for s in ModuleState} == expected

    def test_is_string_enum(self):
        for state in ModuleState:
            assert isinstance(state, str)


@pytest.mark.unit
class TestSystemModuleIds:
    def test_contains_filesystem(self):
        assert "filesystem" in SYSTEM_MODULE_IDS

    def test_contains_os_exec(self):
        assert "os_exec" in SYSTEM_MODULE_IDS

    def test_contains_security(self):
        assert "security" in SYSTEM_MODULE_IDS

    def test_contains_module_manager(self):
        assert "module_manager" in SYSTEM_MODULE_IDS

    def test_is_frozenset(self):
        assert isinstance(SYSTEM_MODULE_IDS, frozenset)

    def test_immutable(self):
        with pytest.raises(AttributeError):
            SYSTEM_MODULE_IDS.add("new_module")  # type: ignore[attr-defined]


@pytest.mark.unit
class TestValidTransitions:
    def test_loaded_can_start(self):
        assert ModuleState.STARTING in VALID_TRANSITIONS[ModuleState.LOADED]

    def test_loaded_can_error(self):
        assert ModuleState.ERROR in VALID_TRANSITIONS[ModuleState.LOADED]

    def test_starting_can_activate(self):
        assert ModuleState.ACTIVE in VALID_TRANSITIONS[ModuleState.STARTING]

    def test_active_can_pause(self):
        assert ModuleState.PAUSED in VALID_TRANSITIONS[ModuleState.ACTIVE]

    def test_active_can_stop(self):
        assert ModuleState.STOPPING in VALID_TRANSITIONS[ModuleState.ACTIVE]

    def test_paused_can_resume(self):
        assert ModuleState.STARTING in VALID_TRANSITIONS[ModuleState.PAUSED]

    def test_paused_can_stop(self):
        assert ModuleState.STOPPING in VALID_TRANSITIONS[ModuleState.PAUSED]

    def test_disabled_can_restart(self):
        assert ModuleState.STARTING in VALID_TRANSITIONS[ModuleState.DISABLED]

    def test_error_can_restart(self):
        assert ModuleState.STARTING in VALID_TRANSITIONS[ModuleState.ERROR]

    def test_stopping_goes_to_disabled(self):
        assert ModuleState.DISABLED in VALID_TRANSITIONS[ModuleState.STOPPING]

    def test_all_states_have_entries(self):
        for state in ModuleState:
            assert state in VALID_TRANSITIONS
