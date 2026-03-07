"""Tests for isolation.sandbox — SandboxEnforcer."""

from __future__ import annotations

import pytest

from llmos_bridge.isolation.sandbox import (
    SandboxEnforcer,
    SandboxLevel,
    SandboxPolicy,
)


class TestSandboxLevelEnum:
    def test_values(self):
        assert SandboxLevel.STRICT == "strict"
        assert SandboxLevel.BASIC == "basic"
        assert SandboxLevel.NONE == "none"


class TestForLevel:
    def test_strict(self):
        policy = SandboxEnforcer.for_level("strict")
        assert policy.level == SandboxLevel.STRICT
        assert policy.allow_network is False
        assert policy.allow_shell is False
        assert policy.max_timeout == 30.0
        assert len(policy.allowed_write_paths) == 0

    def test_basic(self):
        policy = SandboxEnforcer.for_level("basic", install_path="/opt/module")
        assert policy.level == SandboxLevel.BASIC
        assert policy.allow_network is True
        assert policy.allow_shell is False
        assert policy.max_timeout == 60.0
        assert "/opt/module/*" in policy.allowed_write_paths
        assert "/tmp/*" in policy.allowed_write_paths

    def test_none(self):
        policy = SandboxEnforcer.for_level("none")
        assert policy.level == SandboxLevel.NONE
        assert policy.allow_network is True
        assert policy.allow_shell is True
        assert policy.max_timeout == 300.0
        assert "*" in policy.allowed_write_paths

    def test_invalid_level_defaults_to_basic(self):
        policy = SandboxEnforcer.for_level("invalid")
        assert policy.level == SandboxLevel.BASIC


class TestCheckActionStrict:
    @pytest.fixture()
    def strict_policy(self):
        return SandboxEnforcer.for_level("strict")

    def test_write_file_blocked(self, strict_policy):
        violations = SandboxEnforcer.check_action(
            strict_policy, "mod", "write_file", {"path": "/tmp/test.txt"}
        )
        assert len(violations) > 0
        assert "filesystem writes" in violations[0].lower() or "sandbox" in violations[0].lower()

    def test_run_command_blocked(self, strict_policy):
        violations = SandboxEnforcer.check_action(
            strict_policy, "mod", "run_command", {"command": ["ls"]}
        )
        assert len(violations) > 0

    def test_http_request_blocked(self, strict_policy):
        violations = SandboxEnforcer.check_action(
            strict_policy, "mod", "http_request", {"url": "https://example.com"}
        )
        assert len(violations) > 0

    def test_read_file_allowed(self, strict_policy):
        violations = SandboxEnforcer.check_action(
            strict_policy, "mod", "read_file", {"path": "/tmp/test.txt"}
        )
        assert len(violations) == 0

    def test_delete_file_blocked(self, strict_policy):
        violations = SandboxEnforcer.check_action(
            strict_policy, "mod", "delete_file", {"path": "/tmp/test.txt"}
        )
        assert len(violations) > 0


class TestCheckActionBasic:
    @pytest.fixture()
    def basic_policy(self):
        return SandboxEnforcer.for_level("basic", install_path="/opt/module")

    def test_write_to_allowed_path(self, basic_policy):
        violations = SandboxEnforcer.check_action(
            basic_policy, "mod", "write_file", {"path": "/tmp/output.txt"}
        )
        assert len(violations) == 0

    def test_write_to_install_path(self, basic_policy):
        violations = SandboxEnforcer.check_action(
            basic_policy, "mod", "write_file", {"path": "/opt/module/data.json"}
        )
        assert len(violations) == 0

    def test_write_to_forbidden_path(self, basic_policy):
        violations = SandboxEnforcer.check_action(
            basic_policy, "mod", "write_file", {"path": "/etc/passwd"}
        )
        assert len(violations) > 0
        assert "/etc/passwd" in violations[0]

    def test_shell_blocked(self, basic_policy):
        violations = SandboxEnforcer.check_action(
            basic_policy, "mod", "run_command", {"command": ["ls"]}
        )
        assert len(violations) > 0

    def test_shell_true_blocked(self, basic_policy):
        violations = SandboxEnforcer.check_action(
            basic_policy, "mod", "run_command", {"command": "ls", "shell": True}
        )
        # Two violations: action is shell + shell=True
        assert len(violations) >= 1

    def test_network_allowed(self, basic_policy):
        violations = SandboxEnforcer.check_action(
            basic_policy, "mod", "http_request", {"url": "https://example.com"}
        )
        assert len(violations) == 0


class TestCheckActionNone:
    @pytest.fixture()
    def none_policy(self):
        return SandboxEnforcer.for_level("none")

    def test_everything_allowed(self, none_policy):
        assert SandboxEnforcer.check_action(none_policy, "mod", "write_file", {"path": "/etc/passwd"}) == []
        assert SandboxEnforcer.check_action(none_policy, "mod", "run_command", {"shell": True}) == []
        assert SandboxEnforcer.check_action(none_policy, "mod", "http_request", {}) == []


class TestPathAllowed:
    def test_wildcard_allows_all(self):
        assert SandboxEnforcer._path_allowed("/any/path", frozenset({"*"}))

    def test_glob_match(self):
        assert SandboxEnforcer._path_allowed("/tmp/test.txt", frozenset({"/tmp/*"}))

    def test_no_match(self):
        assert not SandboxEnforcer._path_allowed("/etc/passwd", frozenset({"/tmp/*"}))

    def test_empty_patterns(self):
        assert not SandboxEnforcer._path_allowed("/any", frozenset())


class TestCustomWriteActions:
    def test_action_ending_with_write(self):
        policy = SandboxEnforcer.for_level("strict")
        violations = SandboxEnforcer.check_action(
            policy, "mod", "data_write", {"path": "/foo"}
        )
        assert len(violations) > 0

    def test_action_ending_with_delete(self):
        policy = SandboxEnforcer.for_level("strict")
        violations = SandboxEnforcer.check_action(
            policy, "mod", "data_delete", {"path": "/foo"}
        )
        assert len(violations) > 0
