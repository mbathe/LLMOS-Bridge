"""Unit tests — DAGScheduler."""

import pytest

from llmos_bridge.exceptions import DAGCycleError
from llmos_bridge.orchestration.dag import DAGScheduler, ExecutionWave
from llmos_bridge.protocol.models import ExecutionMode, IMLAction, IMLPlan


def _action(action_id: str, depends_on: list[str] | None = None) -> IMLAction:
    return IMLAction(
        id=action_id,
        action="read_file",
        module="filesystem",
        params={"path": f"/tmp/{action_id}"},
        depends_on=depends_on or [],
    )


def _plan(*actions: IMLAction, mode: ExecutionMode = ExecutionMode.SEQUENTIAL) -> IMLPlan:
    return IMLPlan(
        plan_id="dag-test",
        description="DAG test",
        execution_mode=mode,
        actions=list(actions),
    )


class TestSequentialWaves:
    def test_single_action(self) -> None:
        plan = _plan(_action("a1"))
        scheduler = DAGScheduler(plan)
        waves = list(scheduler.waves())
        assert len(waves) == 1
        assert waves[0].action_ids == ["a1"]
        assert waves[0].is_final

    def test_linear_chain(self) -> None:
        plan = _plan(
            _action("a1"),
            _action("a2", ["a1"]),
            _action("a3", ["a2"]),
        )
        scheduler = DAGScheduler(plan)
        waves = list(scheduler.waves())
        # In sequential mode, each wave has exactly one action.
        assert len(waves) == 3
        ids = [w.action_ids[0] for w in waves]
        assert ids.index("a1") < ids.index("a2") < ids.index("a3")

    def test_no_dependencies_still_respects_order(self) -> None:
        plan = _plan(_action("a1"), _action("a2"), _action("a3"))
        scheduler = DAGScheduler(plan)
        waves = list(scheduler.waves())
        assert len(waves) == 3


class TestParallelWaves:
    def test_three_independent_actions_one_wave(self) -> None:
        plan = _plan(
            _action("a1"),
            _action("a2"),
            _action("a3"),
            mode=ExecutionMode.PARALLEL,
        )
        scheduler = DAGScheduler(plan)
        waves = list(scheduler.waves())
        assert len(waves) == 1
        assert sorted(waves[0].action_ids) == ["a1", "a2", "a3"]
        assert waves[0].is_final

    def test_diamond_dependency(self) -> None:
        plan = _plan(
            _action("root"),
            _action("left", ["root"]),
            _action("right", ["root"]),
            _action("merge", ["left", "right"]),
            mode=ExecutionMode.PARALLEL,
        )
        scheduler = DAGScheduler(plan)
        waves = list(scheduler.waves())
        # Wave 0: root
        # Wave 1: left, right
        # Wave 2: merge
        assert len(waves) == 3
        assert waves[0].action_ids == ["root"]
        assert sorted(waves[1].action_ids) == ["left", "right"]
        assert waves[2].action_ids == ["merge"]
        assert waves[2].is_final

    def test_partial_dependencies(self) -> None:
        plan = _plan(
            _action("a1"),
            _action("a2", ["a1"]),
            _action("a3"),  # Independent of a1, a2
            mode=ExecutionMode.PARALLEL,
        )
        scheduler = DAGScheduler(plan)
        waves = list(scheduler.waves())
        # Wave 0: a1, a3 (both have no deps)
        # Wave 1: a2
        assert len(waves) == 2
        assert "a3" in waves[0].action_ids
        assert "a1" in waves[0].action_ids


class TestTopologicalOrder:
    def test_topological_order_respects_dependencies(self) -> None:
        plan = _plan(
            _action("a1"),
            _action("a2", ["a1"]),
            _action("a3", ["a2"]),
        )
        order = DAGScheduler(plan).topological_order()
        assert order.index("a1") < order.index("a2")
        assert order.index("a2") < order.index("a3")


class TestGraphQueries:
    def test_successors(self) -> None:
        plan = _plan(_action("a1"), _action("a2", ["a1"]), _action("a3", ["a1"]))
        s = DAGScheduler(plan)
        assert sorted(s.successors("a1")) == ["a2", "a3"]

    def test_predecessors(self) -> None:
        plan = _plan(_action("a1"), _action("a2", ["a1"]))
        s = DAGScheduler(plan)
        assert s.predecessors("a2") == ["a1"]

    def test_ancestors(self) -> None:
        plan = _plan(
            _action("root"),
            _action("mid", ["root"]),
            _action("leaf", ["mid"]),
        )
        s = DAGScheduler(plan)
        assert s.ancestors("leaf") == {"root", "mid"}

    def test_is_independent(self) -> None:
        plan = _plan(_action("a1"), _action("a2"), _action("a3", ["a1"]))
        s = DAGScheduler(plan)
        assert s.is_independent("a1", "a2")
        assert not s.is_independent("a1", "a3")


class TestCycleDetection:
    def test_build_with_cycle_raises(self) -> None:
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
        with pytest.raises(DAGCycleError) as exc:
            DAGScheduler(plan)
        assert len(exc.value.cycle) >= 2
