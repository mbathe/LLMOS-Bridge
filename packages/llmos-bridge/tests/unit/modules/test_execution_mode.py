"""Tests for Module Spec v3 — Execution mode runtime enforcement.

Tests that the executor correctly handles different execution modes
(async, background, sync) declared on ActionSpec.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llmos_bridge.modules.base import BaseModule, Platform
from llmos_bridge.modules.manifest import ActionSpec, ModuleManifest, ParamSpec


# ---------------------------------------------------------------------------
# Test module with different execution modes
# ---------------------------------------------------------------------------


class MultiModeModule(BaseModule):
    MODULE_ID = "multi_mode"
    VERSION = "1.0.0"
    SUPPORTED_PLATFORMS = [Platform.ALL]

    async def _action_async_op(self, params: dict) -> dict:
        return {"mode": "async", "value": params.get("x", 0)}

    async def _action_background_op(self, params: dict) -> dict:
        await asyncio.sleep(0.01)
        return {"mode": "background", "value": params.get("x", 0)}

    async def _action_sync_op(self, params: dict) -> dict:
        return {"mode": "sync", "value": params.get("x", 0)}

    def get_manifest(self) -> ModuleManifest:
        return ModuleManifest(
            module_id=self.MODULE_ID,
            version=self.VERSION,
            description="Module with multiple execution modes",
            actions=[
                ActionSpec(
                    name="async_op",
                    description="Normal async operation",
                    execution_mode="async",
                ),
                ActionSpec(
                    name="background_op",
                    description="Background operation",
                    execution_mode="background",
                ),
                ActionSpec(
                    name="sync_op",
                    description="Synchronous operation",
                    execution_mode="sync",
                ),
            ],
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGetExecutionMode:
    """Test the _get_execution_mode helper on PlanExecutor."""

    def _make_executor(self, registry):
        from llmos_bridge.orchestration.executor import PlanExecutor
        from llmos_bridge.orchestration.state import PlanStateStore
        from llmos_bridge.security.audit import AuditLogger
        from llmos_bridge.security.guard import PermissionGuard

        guard = MagicMock(spec=PermissionGuard)
        store = MagicMock(spec=PlanStateStore)
        audit = MagicMock(spec=AuditLogger)
        return PlanExecutor(
            module_registry=registry,
            guard=guard,
            state_store=store,
            audit_logger=audit,
        )

    def test_default_async(self):
        from llmos_bridge.modules.registry import ModuleRegistry
        from llmos_bridge.protocol.models import IMLAction

        registry = ModuleRegistry()
        registry.register_instance(MultiModeModule())
        executor = self._make_executor(registry)

        action = MagicMock(spec=IMLAction)
        action.module = "multi_mode"
        action.action = "async_op"

        assert executor._get_execution_mode(action) == "async"

    def test_background_mode(self):
        from llmos_bridge.modules.registry import ModuleRegistry
        from llmos_bridge.protocol.models import IMLAction

        registry = ModuleRegistry()
        registry.register_instance(MultiModeModule())
        executor = self._make_executor(registry)

        action = MagicMock(spec=IMLAction)
        action.module = "multi_mode"
        action.action = "background_op"

        assert executor._get_execution_mode(action) == "background"

    def test_sync_mode(self):
        from llmos_bridge.modules.registry import ModuleRegistry
        from llmos_bridge.protocol.models import IMLAction

        registry = ModuleRegistry()
        registry.register_instance(MultiModeModule())
        executor = self._make_executor(registry)

        action = MagicMock(spec=IMLAction)
        action.module = "multi_mode"
        action.action = "sync_op"

        assert executor._get_execution_mode(action) == "sync"

    def test_unknown_action_defaults_to_async(self):
        from llmos_bridge.modules.registry import ModuleRegistry
        from llmos_bridge.protocol.models import IMLAction

        registry = ModuleRegistry()
        registry.register_instance(MultiModeModule())
        executor = self._make_executor(registry)

        action = MagicMock(spec=IMLAction)
        action.module = "multi_mode"
        action.action = "nonexistent"

        assert executor._get_execution_mode(action) == "async"

    def test_unknown_module_defaults_to_async(self):
        from llmos_bridge.modules.registry import ModuleRegistry
        from llmos_bridge.protocol.models import IMLAction

        registry = ModuleRegistry()
        executor = self._make_executor(registry)

        action = MagicMock(spec=IMLAction)
        action.module = "ghost"
        action.action = "anything"

        assert executor._get_execution_mode(action) == "async"


class TestBackgroundDispatch:
    """Test the background dispatch mechanism."""

    @pytest.mark.asyncio
    async def test_background_returns_task_id(self):
        from llmos_bridge.modules.registry import ModuleRegistry
        from llmos_bridge.orchestration.executor import PlanExecutor
        from llmos_bridge.protocol.models import IMLAction

        registry = ModuleRegistry()
        module = MultiModeModule()
        registry.register_instance(module)

        executor = PlanExecutor(
            module_registry=registry,
            guard=MagicMock(),
            state_store=MagicMock(),
            audit_logger=MagicMock(),
        )

        action = MagicMock(spec=IMLAction)
        action.module = "multi_mode"
        action.action = "background_op"
        action.target_node = None

        result = await executor._dispatch_background(action, {"x": 42})

        assert result["background"] is True
        assert "task_id" in result
        assert result["status"] == "running"

        # Wait for background task to complete.
        await asyncio.sleep(0.05)


class TestActionSpecExecutionMode:
    """Test ActionSpec execution_mode field behavior."""

    def test_default_execution_mode(self):
        spec = ActionSpec(name="test", description="test")
        assert spec.execution_mode == "async"

    def test_custom_execution_mode(self):
        spec = ActionSpec(name="test", description="test", execution_mode="background")
        assert spec.execution_mode == "background"

    def test_to_dict_excludes_default_mode(self):
        from llmos_bridge.modules.manifest import ModuleManifest

        spec = ActionSpec(name="test", description="test", execution_mode="async")
        d = ModuleManifest._action_to_dict(spec)
        assert "execution_mode" not in d

    def test_to_dict_includes_non_default_mode(self):
        from llmos_bridge.modules.manifest import ModuleManifest

        spec = ActionSpec(name="test", description="test", execution_mode="background")
        d = ModuleManifest._action_to_dict(spec)
        assert d["execution_mode"] == "background"
