"""Unit tests — modules/triggers/module.py (TriggerModule)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from llmos_bridge.modules.triggers import TriggerModule
from llmos_bridge.triggers.models import TriggerDefinition, TriggerState, TriggerCondition, TriggerType


def _make_register_params(**overrides):
    params = {
        "name": "test_trigger",
        "description": "A test trigger",
        "condition": {"type": "temporal", "params": {"interval_seconds": 60}},
        "plan_template": {"protocol_version": "2.0", "actions": []},
        "priority": "normal",
        "enabled": True,
    }
    params.update(overrides)
    return params


@pytest.mark.unit
class TestTriggerModuleNooDaemon:
    """All actions should return ActionResult(success=False) when daemon is None."""

    def setup_method(self):
        self.module = TriggerModule()  # no daemon set

    async def test_register_no_daemon(self) -> None:
        result = await self.module._action_register_trigger(_make_register_params())
        assert result.success is False
        assert "not available" in result.error

    async def test_activate_no_daemon(self) -> None:
        result = await self.module._action_activate_trigger({"trigger_id": "t1"})
        assert result.success is False

    async def test_deactivate_no_daemon(self) -> None:
        result = await self.module._action_deactivate_trigger({"trigger_id": "t1"})
        assert result.success is False

    async def test_delete_no_daemon(self) -> None:
        result = await self.module._action_delete_trigger({"trigger_id": "t1"})
        assert result.success is False

    async def test_list_no_daemon(self) -> None:
        result = await self.module._action_list_triggers({})
        assert result.success is False

    async def test_get_no_daemon(self) -> None:
        result = await self.module._action_get_trigger({"trigger_id": "t1"})
        assert result.success is False


@pytest.mark.unit
class TestTriggerModuleWithDaemon:
    def _make_module_with_daemon(self) -> tuple[TriggerModule, MagicMock]:
        module = TriggerModule()
        mock_daemon = MagicMock()
        module.set_daemon(mock_daemon)
        return module, mock_daemon

    async def test_register_calls_daemon(self) -> None:
        module, daemon = self._make_module_with_daemon()
        mock_trigger = TriggerDefinition(
            name="test_trigger",
            condition=TriggerCondition(TriggerType.TEMPORAL, {"interval_seconds": 60}),
            state=TriggerState.ACTIVE,
        )
        daemon.register = AsyncMock(return_value=mock_trigger)

        result = await module._action_register_trigger(_make_register_params())
        assert isinstance(result, dict)
        assert result["trigger_id"] == mock_trigger.trigger_id
        assert result["state"] == "active"
        daemon.register.assert_called_once()

    async def test_activate_calls_daemon(self) -> None:
        module, daemon = self._make_module_with_daemon()
        daemon.activate = AsyncMock()
        result = await module._action_activate_trigger({"trigger_id": "t1"})
        assert result["state"] == "active"
        daemon.activate.assert_called_once_with("t1")

    async def test_deactivate_calls_daemon(self) -> None:
        module, daemon = self._make_module_with_daemon()
        daemon.deactivate = AsyncMock()
        result = await module._action_deactivate_trigger({"trigger_id": "t1"})
        assert result["state"] == "inactive"

    async def test_delete_calls_daemon(self) -> None:
        module, daemon = self._make_module_with_daemon()
        daemon.delete = AsyncMock(return_value=True)
        result = await module._action_delete_trigger({"trigger_id": "t1"})
        assert result["deleted"] is True

    async def test_list_with_filters(self) -> None:
        module, daemon = self._make_module_with_daemon()
        t = TriggerDefinition(
            name="filter_test",
            condition=TriggerCondition(TriggerType.FILESYSTEM, {}),
            state=TriggerState.ACTIVE,
            enabled=True,
        )
        t.tags = ["prod"]
        t.created_by = "user"
        daemon.list_all = AsyncMock(return_value=[t])

        result = await module._action_list_triggers({"state": "active", "include_health": True})
        assert isinstance(result, dict)
        assert result["count"] == 1
        assert result["triggers"][0]["name"] == "filter_test"
        assert "health" in result["triggers"][0]

    async def test_list_state_filter_excludes(self) -> None:
        module, daemon = self._make_module_with_daemon()
        t = TriggerDefinition(
            name="inactive",
            condition=TriggerCondition(TriggerType.TEMPORAL, {}),
            state=TriggerState.INACTIVE,
        )
        daemon.list_all = AsyncMock(return_value=[t])
        result = await module._action_list_triggers({"state": "active"})
        assert result["count"] == 0

    async def test_get_existing_trigger(self) -> None:
        module, daemon = self._make_module_with_daemon()
        t = TriggerDefinition(
            name="detail_test",
            condition=TriggerCondition(TriggerType.TEMPORAL, {"interval_seconds": 60}),
            state=TriggerState.ACTIVE,
        )
        daemon.get = AsyncMock(return_value=t)
        result = await module._action_get_trigger({"trigger_id": t.trigger_id})
        assert result["name"] == "detail_test"
        assert "health" in result

    async def test_get_nonexistent_trigger(self) -> None:
        module, daemon = self._make_module_with_daemon()
        daemon.get = AsyncMock(return_value=None)
        result = await module._action_get_trigger({"trigger_id": "nonexistent"})
        assert result.success is False
        assert "not found" in result.error

    def test_manifest(self) -> None:
        module = TriggerModule()
        manifest = module.get_manifest()
        assert manifest.module_id == "triggers"
        action_names = [a.name for a in manifest.actions]
        assert "register_trigger" in action_names
        assert "list_triggers" in action_names
        assert "get_trigger" in action_names

    def test_set_daemon(self) -> None:
        module = TriggerModule()
        assert module._daemon is None
        mock_daemon = MagicMock()
        module.set_daemon(mock_daemon)
        assert module._daemon is mock_daemon
