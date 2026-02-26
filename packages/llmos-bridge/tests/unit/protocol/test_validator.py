"""Unit tests — IML Validator (semantic checks)."""

import pytest

from llmos_bridge.exceptions import DAGCycleError, IMLValidationError
from llmos_bridge.protocol.models import ExecutionMode, IMLAction, IMLPlan
from llmos_bridge.protocol.validator import IMLValidator


@pytest.fixture
def validator() -> IMLValidator:
    return IMLValidator()


def _plan(*actions: IMLAction, mode: ExecutionMode = ExecutionMode.SEQUENTIAL) -> IMLPlan:
    return IMLPlan(
        plan_id="v-test",
        description="Validator test plan",
        execution_mode=mode,
        actions=list(actions),
    )


class TestDAGValidation:
    def test_no_cycle_passes(self, validator: IMLValidator) -> None:
        plan = _plan(
            IMLAction(id="a1", action="read_file", module="filesystem", params={"path": "/a"}),
            IMLAction(id="a2", action="write_file", module="filesystem",
                      params={"path": "/b", "content": "x"}, depends_on=["a1"]),
        )
        validator.validate(plan)  # Should not raise

    def test_direct_cycle_raises(self, validator: IMLValidator) -> None:
        # a1 -> a2 -> a1 — cycle
        # Note: Pydantic catches self-dependency, so we test a 2-node cycle.
        # We need to bypass Pydantic's depends_on check for this test.
        # Use parse_partial to build the plan.
        from llmos_bridge.protocol.parser import IMLParser

        plan = IMLParser().parse_partial(
            {
                "protocol_version": "2.0",
                "description": "Cycle",
                "actions": [
                    {"id": "a1", "action": "read_file", "module": "filesystem",
                     "params": {"path": "/a"}, "depends_on": ["a2"]},
                    {"id": "a2", "action": "read_file", "module": "filesystem",
                     "params": {"path": "/b"}, "depends_on": ["a1"]},
                ],
            }
        )
        with pytest.raises(DAGCycleError):
            validator.validate(plan)

    def test_diamond_dag_no_cycle(self, validator: IMLValidator) -> None:
        plan = _plan(
            IMLAction(id="a1", action="read_file", module="filesystem", params={"path": "/a"}),
            IMLAction(id="a2", action="read_file", module="filesystem", params={"path": "/b"}, depends_on=["a1"]),
            IMLAction(id="a3", action="read_file", module="filesystem", params={"path": "/c"}, depends_on=["a1"]),
            IMLAction(id="a4", action="write_file", module="filesystem",
                      params={"path": "/d", "content": "x"}, depends_on=["a2", "a3"]),
        )
        validator.validate(plan)  # Should not raise


class TestTemplateReferences:
    def test_valid_template_reference(self, validator: IMLValidator) -> None:
        plan = _plan(
            IMLAction(id="a1", action="read_file", module="filesystem", params={"path": "/a"}),
            IMLAction(id="a2", action="write_file", module="filesystem",
                      params={"path": "/b", "content": "{{result.a1.content}}"}, depends_on=["a1"]),
        )
        validator.validate(plan)  # Should not raise

    def test_invalid_template_reference(self, validator: IMLValidator) -> None:
        plan = _plan(
            IMLAction(id="a1", action="write_file", module="filesystem",
                      params={"path": "/b", "content": "{{result.nonexistent.content}}"}),
        )
        with pytest.raises(IMLValidationError, match="Template reference errors"):
            validator.validate(plan)

    def test_memory_template_no_validation(self, validator: IMLValidator) -> None:
        plan = _plan(
            IMLAction(id="a1", action="write_file", module="filesystem",
                      params={"path": "/b", "content": "{{memory.saved_content}}"}),
        )
        validator.validate(plan)  # memory.X refs are not validated at compile time


class TestRollbackChains:
    def test_valid_rollback(self, validator: IMLValidator) -> None:
        plan = _plan(
            IMLAction(id="a1", action="delete_file", module="filesystem", params={"path": "/a"},
                      rollback={"action": "restore", "params": {}}),
            IMLAction(id="restore", action="write_file", module="filesystem",
                      params={"path": "/a", "content": "backup"}),
        )
        validator.validate(plan)

    def test_rollback_cycle_raises(self, validator: IMLValidator) -> None:
        plan = _plan(
            IMLAction(id="a1", action="delete_file", module="filesystem", params={"path": "/a"},
                      rollback={"action": "a2", "params": {}}),
            IMLAction(id="a2", action="write_file", module="filesystem",
                      params={"path": "/a", "content": "x"},
                      rollback={"action": "a1", "params": {}}),
        )
        with pytest.raises(IMLValidationError, match="Rollback cycle"):
            validator.validate(plan)
