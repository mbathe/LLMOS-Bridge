"""Unit tests — MigrationPipeline, MigrationRegistry, and _migrate_v1_to_v2."""

from __future__ import annotations

import json

import pytest

from llmos_bridge.exceptions import IMLParseError, ProtocolError
from llmos_bridge.protocol.migration import (
    MigrationPipeline,
    MigrationRegistry,
    _migrate_v1_to_v2,
)


# ---------------------------------------------------------------------------
# _migrate_v1_to_v2 — the concrete migration function
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMigrateV1ToV2:
    def test_renames_steps_to_actions(self) -> None:
        plan = {
            "protocol_version": "1.0",
            "steps": [
                {"id": "s1", "module": "filesystem", "action": "read_file", "params": {}}
            ],
        }
        result = _migrate_v1_to_v2(plan)
        assert "actions" in result
        assert "steps" not in result

    def test_sets_protocol_version_to_2(self) -> None:
        plan = {"protocol_version": "1.0", "steps": []}
        result = _migrate_v1_to_v2(plan)
        assert result["protocol_version"] == "2.0"

    def test_adds_on_error_default(self) -> None:
        plan = {
            "steps": [
                {"id": "s1", "module": "fs", "action": "read", "params": {}}
            ]
        }
        result = _migrate_v1_to_v2(plan)
        assert result["actions"][0]["on_error"] == "abort"

    def test_adds_timeout_default(self) -> None:
        plan = {
            "steps": [
                {"id": "s1", "module": "fs", "action": "read", "params": {}}
            ]
        }
        result = _migrate_v1_to_v2(plan)
        assert result["actions"][0]["timeout"] == 60

    def test_existing_on_error_preserved(self) -> None:
        plan = {
            "steps": [
                {"id": "s1", "module": "fs", "action": "read", "params": {},
                 "on_error": "continue"}
            ]
        }
        result = _migrate_v1_to_v2(plan)
        assert result["actions"][0]["on_error"] == "continue"

    def test_auto_generates_id_when_missing(self) -> None:
        plan = {
            "steps": [
                {"module": "fs", "action": "read", "params": {}}
            ]
        }
        result = _migrate_v1_to_v2(plan)
        assert result["actions"][0]["id"] == "step_1"

    def test_positional_params_converted_to_dict(self) -> None:
        plan = {
            "steps": [
                {"id": "s1", "module": "fs", "action": "read", "params": ["a", "b", "c"]}
            ]
        }
        result = _migrate_v1_to_v2(plan)
        params = result["actions"][0]["params"]
        assert params == {"arg_0": "a", "arg_1": "b", "arg_2": "c"}

    def test_renames_type_to_module(self) -> None:
        plan = {
            "steps": [
                {"id": "s1", "type": "filesystem", "action": "read", "params": {}}
            ]
        }
        result = _migrate_v1_to_v2(plan)
        assert "module" in result["actions"][0]
        assert result["actions"][0]["module"] == "filesystem"
        assert "type" not in result["actions"][0]

    def test_renames_name_to_action(self) -> None:
        plan = {
            "steps": [
                {"id": "s1", "module": "filesystem", "name": "read_file", "params": {}}
            ]
        }
        result = _migrate_v1_to_v2(plan)
        assert "action" in result["actions"][0]
        assert result["actions"][0]["action"] == "read_file"
        assert "name" not in result["actions"][0]

    def test_does_not_mutate_input(self) -> None:
        plan = {"protocol_version": "1.0", "steps": [{"id": "s1", "params": {}}]}
        original_id = id(plan)
        _migrate_v1_to_v2(plan)
        assert plan["protocol_version"] == "1.0"
        assert "steps" in plan


# ---------------------------------------------------------------------------
# MigrationRegistry
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMigrationRegistry:
    def test_register_and_find_direct_path(self) -> None:
        registry = MigrationRegistry()
        fn = lambda p: {**p, "protocol_version": "2.0"}
        registry.register("1.0", "2.0", fn)

        path = registry.find_path("1.0", "2.0")
        assert path is not None
        assert len(path) == 1
        assert path[0][0] == "2.0"

    def test_find_path_same_version_returns_empty(self) -> None:
        registry = MigrationRegistry()
        path = registry.find_path("2.0", "2.0")
        assert path == []

    def test_find_path_no_path_returns_none(self) -> None:
        registry = MigrationRegistry()
        path = registry.find_path("1.0", "3.0")
        assert path is None

    def test_find_path_multi_hop(self) -> None:
        registry = MigrationRegistry()
        fn_1_2 = lambda p: {**p, "protocol_version": "2.0"}
        fn_2_3 = lambda p: {**p, "protocol_version": "3.0"}
        registry.register("1.0", "2.0", fn_1_2)
        registry.register("2.0", "3.0", fn_2_3)

        path = registry.find_path("1.0", "3.0")
        assert path is not None
        assert len(path) == 2
        versions = [step[0] for step in path]
        assert versions == ["2.0", "3.0"]

    def test_find_path_bfs_finds_shortest(self) -> None:
        registry = MigrationRegistry()
        # Direct path: 1.0 → 2.0 → 3.0
        # Alternative: 1.0 → 3.0 (but not registered)
        fn_1_2 = lambda p: {**p, "v": "2"}
        fn_2_3 = lambda p: {**p, "v": "3"}
        fn_1_3 = lambda p: {**p, "v": "3_direct"}
        registry.register("1.0", "2.0", fn_1_2)
        registry.register("2.0", "3.0", fn_2_3)
        registry.register("1.0", "3.0", fn_1_3)

        path = registry.find_path("1.0", "3.0")
        # BFS should return the direct path (1 hop)
        assert path is not None
        assert len(path) == 1


# ---------------------------------------------------------------------------
# MigrationPipeline
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMigrationPipeline:
    def test_upgrade_v2_plan_unchanged(self) -> None:
        pipeline = MigrationPipeline()
        plan = {
            "protocol_version": "2.0",
            "plan_id": "test",
            "actions": [],
        }
        result = pipeline.upgrade(plan)
        assert result["protocol_version"] == "2.0"

    def test_upgrade_v1_plan_to_v2(self) -> None:
        pipeline = MigrationPipeline()
        plan = {
            "protocol_version": "1.0",
            "steps": [
                {"id": "s1", "module": "filesystem", "action": "read_file",
                 "params": {"path": "/tmp/test.txt"}}
            ],
        }
        result = pipeline.upgrade(plan)
        assert result["protocol_version"] == "2.0"
        assert "actions" in result
        assert len(result["actions"]) == 1

    def test_upgrade_from_json_string(self) -> None:
        pipeline = MigrationPipeline()
        plan_json = json.dumps({
            "protocol_version": "1.0",
            "steps": [
                {"id": "s1", "module": "filesystem", "action": "read_file",
                 "params": {"path": "/tmp/test.txt"}}
            ],
        })
        result = pipeline.upgrade(plan_json)
        assert result["protocol_version"] == "2.0"

    def test_upgrade_from_bytes(self) -> None:
        pipeline = MigrationPipeline()
        plan_bytes = json.dumps({
            "protocol_version": "1.0",
            "steps": [],
        }).encode()
        result = pipeline.upgrade(plan_bytes)
        assert result["protocol_version"] == "2.0"

    def test_upgrade_invalid_json_raises(self) -> None:
        pipeline = MigrationPipeline()
        with pytest.raises(IMLParseError, match="Cannot decode JSON"):
            pipeline.upgrade("not valid json {{{")

    def test_upgrade_non_dict_raises(self) -> None:
        pipeline = MigrationPipeline()
        with pytest.raises(IMLParseError, match="JSON object"):
            pipeline.upgrade("[1, 2, 3]")

    def test_upgrade_unknown_version_raises_protocol_error(self) -> None:
        pipeline = MigrationPipeline()
        plan = {"protocol_version": "99.0", "actions": []}
        with pytest.raises(ProtocolError, match="No migration path"):
            pipeline.upgrade(plan)

    def test_upgrade_plan_without_version_assumes_v1(self) -> None:
        """Plans without protocol_version are assumed to be v1.0."""
        pipeline = MigrationPipeline()
        plan = {
            "steps": [
                {"id": "s1", "module": "filesystem", "action": "read_file",
                 "params": {}}
            ]
        }
        result = pipeline.upgrade(plan)
        assert result["protocol_version"] == "2.0"

    def test_custom_registry_used(self) -> None:
        registry = MigrationRegistry()
        fn = lambda p: {**p, "protocol_version": "2.0", "migrated": True}
        registry.register("1.5", "2.0", fn)

        pipeline = MigrationPipeline(registry=registry)
        plan = {"protocol_version": "1.5", "actions": []}
        result = pipeline.upgrade(plan)
        assert result.get("migrated") is True
