"""Unit tests — RollbackEngine."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from llmos_bridge.modules.filesystem import FilesystemModule
from llmos_bridge.modules.registry import ModuleRegistry
from llmos_bridge.orchestration.rollback import RollbackEngine, _MAX_ROLLBACK_DEPTH
from llmos_bridge.protocol.models import IMLAction, IMLPlan, RollbackConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_plan(actions: list[dict]) -> IMLPlan:
    return IMLPlan(
        plan_id="rollback-test",
        description="Rollback test plan",
        actions=[IMLAction(**a) for a in actions],
    )


@pytest.fixture
def registry() -> ModuleRegistry:
    reg = ModuleRegistry()
    reg.register(FilesystemModule)
    return reg


@pytest.fixture
def engine(registry: ModuleRegistry) -> RollbackEngine:
    return RollbackEngine(module_registry=registry)


# ---------------------------------------------------------------------------
# RollbackEngine.execute — no rollback config
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRollbackEngineNoConfig:
    async def test_no_rollback_config_exits_silently(
        self, engine: RollbackEngine
    ) -> None:
        plan = make_plan([
            {"id": "write1", "module": "filesystem", "action": "write_file",
             "params": {"path": "/tmp/test.txt", "content": "hello"}},
        ])
        failed_action = plan.get_action("write1")
        # No rollback config set — should return without error
        await engine.execute(plan, failed_action, {})

    async def test_rollback_not_found_in_plan_logs_error(
        self, engine: RollbackEngine
    ) -> None:
        # Build a plan without a rollback target, then manually attach a
        # RollbackConfig pointing at a nonexistent action ID.
        # (IMLPlan validates forward refs at construction time, so we do this
        # post-construction to exercise the engine's "action not found" branch.)
        plan = make_plan([
            {"id": "write1", "module": "filesystem", "action": "write_file",
             "params": {"path": "/tmp/test.txt", "content": "hello"}},
        ])
        failed_action = plan.get_action("write1")
        failed_action.rollback = RollbackConfig(action="nonexistent_action", params={})
        # Should log error but not raise
        await engine.execute(plan, failed_action, {})


# ---------------------------------------------------------------------------
# RollbackEngine.execute — depth limit
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRollbackEngineDepthLimit:
    async def test_depth_exceeded_exits_silently(
        self, engine: RollbackEngine
    ) -> None:
        plan = make_plan([
            {"id": "write1", "module": "filesystem", "action": "write_file",
             "params": {"path": "/tmp/test.txt", "content": "hello"},
             "rollback": {"action": "write1", "params": {}}},
        ])
        failed_action = plan.get_action("write1")
        # Should log error and return without executing
        await engine.execute(plan, failed_action, {}, depth=_MAX_ROLLBACK_DEPTH)


# ---------------------------------------------------------------------------
# RollbackEngine.execute — successful rollback
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRollbackEngineSuccess:
    async def test_rollback_executes_compensating_action(
        self, engine: RollbackEngine, tmp_path: Path
    ) -> None:
        # Write a file first, then rollback deletes it
        target = tmp_path / "to_rollback.txt"
        target.write_text("data")

        plan = make_plan([
            {
                "id": "write1",
                "module": "filesystem",
                "action": "write_file",
                "params": {"path": str(target), "content": "data"},
                "rollback": {"action": "delete1", "params": {}},
            },
            {
                "id": "delete1",
                "module": "filesystem",
                "action": "delete_file",
                "params": {"path": str(target)},
            },
        ])
        failed_action = plan.get_action("write1")
        await engine.execute(plan, failed_action, {})
        # File should have been deleted by rollback
        assert not target.exists()

    async def test_rollback_with_template_params(
        self, engine: RollbackEngine, tmp_path: Path
    ) -> None:
        # Create a file to read and then "roll back" by deleting it
        src = tmp_path / "src.txt"
        src.write_text("content")

        # execution_results contain results from prior actions
        execution_results = {
            "write1": {"path": str(src), "bytes_written": 7}
        }

        plan = make_plan([
            {
                "id": "write1",
                "module": "filesystem",
                "action": "write_file",
                "params": {"path": str(src), "content": "content"},
                "rollback": {"action": "delete1", "params": {"path": "{{result.write1.path}}"}},
            },
            {
                "id": "delete1",
                "module": "filesystem",
                "action": "delete_file",
                "params": {"path": str(src)},
            },
        ])
        failed_action = plan.get_action("write1")
        await engine.execute(plan, failed_action, execution_results)
        assert not src.exists()


# ---------------------------------------------------------------------------
# RollbackEngine.execute — rollback module failure
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRollbackEngineFailure:
    async def test_rollback_failure_does_not_raise(
        self, registry: ModuleRegistry
    ) -> None:
        """If the rollback action itself fails, the engine should log but not raise."""
        # Use a mock module that fails on execute
        mock_module = MagicMock()
        mock_module.execute = AsyncMock(side_effect=RuntimeError("module crashed"))
        mock_registry = MagicMock()
        mock_registry.get = MagicMock(return_value=mock_module)

        engine = RollbackEngine(module_registry=mock_registry)

        plan = make_plan([
            {
                "id": "write1",
                "module": "filesystem",
                "action": "write_file",
                "params": {"path": "/tmp/test.txt", "content": "hello"},
                "rollback": {"action": "delete1", "params": {}},
            },
            {
                "id": "delete1",
                "module": "filesystem",
                "action": "delete_file",
                "params": {"path": "/tmp/test.txt"},
            },
        ])
        failed_action = plan.get_action("write1")
        # Should log the error but not raise
        await engine.execute(plan, failed_action, {})


# ---------------------------------------------------------------------------
# RollbackEngine.execute — template resolution failure
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRollbackEngineTemplateFailure:
    async def test_template_resolution_failure_exits_cleanly(
        self, engine: RollbackEngine
    ) -> None:
        plan = make_plan([
            {
                "id": "write1",
                "module": "filesystem",
                "action": "write_file",
                "params": {"path": "/tmp/test.txt", "content": "hello"},
                # Reference a result that doesn't exist in execution_results
                "rollback": {"action": "delete1", "params": {"path": "{{result.nonexistent.path}}"}},
            },
            {
                "id": "delete1",
                "module": "filesystem",
                "action": "delete_file",
                "params": {"path": "/tmp/test.txt"},
            },
        ])
        failed_action = plan.get_action("write1")
        # Template can't be resolved — should log error and return
        # (TemplateResolver may raise or leave the template unresolved)
        # Either way, rollback should not crash the caller
        try:
            await engine.execute(plan, failed_action, {})
        except Exception:
            pytest.fail("RollbackEngine.execute should not propagate exceptions")
