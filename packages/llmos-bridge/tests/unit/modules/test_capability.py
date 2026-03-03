"""Tests for Module Spec v3 — Structured Capability objects.

Tests the Capability dataclass, its integration with ActionSpec and
ModuleManifest, and serialization behavior.
"""

from __future__ import annotations

import pytest

from llmos_bridge.modules.manifest import (
    ActionSpec,
    Capability,
    ModuleManifest,
    ParamSpec,
)


# ---------------------------------------------------------------------------
# Capability dataclass tests
# ---------------------------------------------------------------------------


class TestCapability:
    def test_basic_creation(self):
        cap = Capability(permission="filesystem.write")
        assert cap.permission == "filesystem.write"
        assert cap.scope == ""
        assert cap.constraints == {}

    def test_with_scope(self):
        cap = Capability(permission="filesystem.write", scope="sandbox_only")
        assert cap.scope == "sandbox_only"

    def test_with_constraints(self):
        cap = Capability(
            permission="network.send",
            constraints={"allowed_hosts": ["api.example.com"]},
        )
        assert cap.constraints["allowed_hosts"] == ["api.example.com"]

    def test_full_creation(self):
        cap = Capability(
            permission="database.write",
            scope="schema_only",
            constraints={"tables": ["users", "orders"], "max_rows": 1000},
        )
        assert cap.permission == "database.write"
        assert cap.scope == "schema_only"
        assert cap.constraints["tables"] == ["users", "orders"]

    def test_to_dict_minimal(self):
        cap = Capability(permission="filesystem.read")
        d = cap.to_dict()
        assert d == {"permission": "filesystem.read"}
        assert "scope" not in d
        assert "constraints" not in d

    def test_to_dict_full(self):
        cap = Capability(
            permission="network.send",
            scope="external",
            constraints={"rate_limit": 100},
        )
        d = cap.to_dict()
        assert d == {
            "permission": "network.send",
            "scope": "external",
            "constraints": {"rate_limit": 100},
        }

    def test_from_string(self):
        cap = Capability.from_string("filesystem.write")
        assert cap.permission == "filesystem.write"
        assert cap.scope == ""
        assert cap.constraints == {}

    def test_from_dict(self):
        cap = Capability.from_dict({
            "permission": "database.read",
            "scope": "read_only",
            "constraints": {"max_rows": 500},
        })
        assert cap.permission == "database.read"
        assert cap.scope == "read_only"
        assert cap.constraints == {"max_rows": 500}

    def test_from_dict_minimal(self):
        cap = Capability.from_dict({"permission": "os.process.execute"})
        assert cap.permission == "os.process.execute"
        assert cap.scope == ""
        assert cap.constraints == {}


# ---------------------------------------------------------------------------
# ActionSpec integration tests
# ---------------------------------------------------------------------------


class TestActionSpecCapabilities:
    def test_default_empty_capabilities(self):
        spec = ActionSpec(name="test", description="test")
        assert spec.capabilities == []

    def test_with_capabilities(self):
        spec = ActionSpec(
            name="write_file",
            description="Write a file",
            capabilities=[
                Capability("filesystem.write", scope="sandbox_only"),
                Capability("filesystem.read"),
            ],
        )
        assert len(spec.capabilities) == 2
        assert spec.capabilities[0].permission == "filesystem.write"
        assert spec.capabilities[0].scope == "sandbox_only"

    def test_to_dict_with_capabilities(self):
        spec = ActionSpec(
            name="test",
            description="test",
            capabilities=[
                Capability("filesystem.write", scope="sandbox_only"),
            ],
        )
        d = ModuleManifest._action_to_dict(spec)
        assert "capabilities" in d
        assert d["capabilities"] == [
            {"permission": "filesystem.write", "scope": "sandbox_only"}
        ]

    def test_to_dict_without_capabilities(self):
        spec = ActionSpec(name="test", description="test")
        d = ModuleManifest._action_to_dict(spec)
        assert "capabilities" not in d


# ---------------------------------------------------------------------------
# ModuleManifest integration tests
# ---------------------------------------------------------------------------


class TestManifestCapabilities:
    def test_default_empty_declared_capabilities(self):
        manifest = ModuleManifest(
            module_id="test", version="1.0.0", description="test"
        )
        assert manifest.declared_capabilities == []

    def test_with_declared_capabilities(self):
        manifest = ModuleManifest(
            module_id="test",
            version="1.0.0",
            description="test",
            declared_capabilities=[
                Capability("filesystem.write", scope="sandbox_only"),
                Capability("network.send", constraints={"rate_limit": 100}),
            ],
        )
        assert len(manifest.declared_capabilities) == 2

    def test_to_dict_includes_capabilities(self):
        manifest = ModuleManifest(
            module_id="test",
            version="1.0.0",
            description="test",
            declared_capabilities=[
                Capability("filesystem.write", scope="sandbox_only"),
            ],
        )
        d = manifest.to_dict()
        assert "declared_capabilities" in d
        assert d["declared_capabilities"] == [
            {"permission": "filesystem.write", "scope": "sandbox_only"}
        ]

    def test_to_dict_excludes_empty_capabilities(self):
        manifest = ModuleManifest(
            module_id="test", version="1.0.0", description="test"
        )
        d = manifest.to_dict()
        assert "declared_capabilities" not in d

    def test_capabilities_coexist_with_permissions(self):
        """Capabilities and plain string permissions coexist."""
        manifest = ModuleManifest(
            module_id="test",
            version="1.0.0",
            description="test",
            declared_permissions=["filesystem.read", "filesystem.write"],
            declared_capabilities=[
                Capability("filesystem.write", scope="sandbox_only"),
            ],
        )
        d = manifest.to_dict()
        assert d["declared_permissions"] == ["filesystem.read", "filesystem.write"]
        assert d["declared_capabilities"] == [
            {"permission": "filesystem.write", "scope": "sandbox_only"}
        ]
