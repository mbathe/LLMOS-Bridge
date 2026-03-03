"""Tests for Module Spec v3 — ActionSpec, ModuleManifest, ResourceLimits, ModuleSignature.

Covers:
  - ActionSpec v3 fields: output_schema, side_effects, execution_mode
  - ResourceLimits dataclass
  - ModuleSignature dataclass
  - ModuleManifest v3 fields: resource_limits, sandbox_level, license,
    optional_dependencies, module_dependencies, signing
  - Serialization: to_dict() includes v3 fields only when non-default
  - Backwards compatibility: old manifests still work
"""

from __future__ import annotations

import pytest

from llmos_bridge.modules.manifest import (
    ActionSpec,
    ModuleManifest,
    ModuleSignature,
    ParamSpec,
    ResourceLimits,
    ServiceDescriptor,
)


# ---------------------------------------------------------------------------
# ResourceLimits
# ---------------------------------------------------------------------------

class TestResourceLimits:
    def test_defaults(self):
        r = ResourceLimits()
        assert r.max_cpu_percent == 100.0
        assert r.max_memory_mb == 0
        assert r.max_execution_seconds == 0.0
        assert r.max_concurrent_actions == 0

    def test_custom_values(self):
        r = ResourceLimits(
            max_cpu_percent=50.0,
            max_memory_mb=512,
            max_execution_seconds=30.0,
            max_concurrent_actions=5,
        )
        assert r.max_cpu_percent == 50.0
        assert r.max_memory_mb == 512
        assert r.max_execution_seconds == 30.0
        assert r.max_concurrent_actions == 5


# ---------------------------------------------------------------------------
# ModuleSignature
# ---------------------------------------------------------------------------

class TestModuleSignature:
    def test_required_fields(self):
        sig = ModuleSignature(
            public_key_fingerprint="abc123",
            signature_hex="deadbeef",
            signed_hash="cafebabe",
        )
        assert sig.public_key_fingerprint == "abc123"
        assert sig.signature_hex == "deadbeef"
        assert sig.signed_hash == "cafebabe"
        assert sig.signed_at == ""

    def test_with_timestamp(self):
        sig = ModuleSignature(
            public_key_fingerprint="abc",
            signature_hex="def",
            signed_hash="123",
            signed_at="2026-03-01T12:00:00Z",
        )
        assert sig.signed_at == "2026-03-01T12:00:00Z"


# ---------------------------------------------------------------------------
# ActionSpec v3 fields
# ---------------------------------------------------------------------------

class TestActionSpecV3:
    def test_v3_defaults(self):
        a = ActionSpec(name="test", description="A test action")
        assert a.output_schema is None
        assert a.side_effects == []
        assert a.execution_mode == "async"

    def test_v3_with_output_schema(self):
        schema = {
            "type": "object",
            "properties": {
                "result": {"type": "string"},
            },
            "required": ["result"],
        }
        a = ActionSpec(
            name="compute",
            description="Compute something",
            output_schema=schema,
        )
        assert a.output_schema == schema

    def test_v3_with_side_effects(self):
        a = ActionSpec(
            name="write_file",
            description="Write a file",
            side_effects=["filesystem_write", "state_mutation"],
        )
        assert a.side_effects == ["filesystem_write", "state_mutation"]

    def test_v3_execution_modes(self):
        for mode in ["sync", "async", "background", "scheduled"]:
            a = ActionSpec(name="test", description="Test", execution_mode=mode)
            assert a.execution_mode == mode

    def test_v3_backwards_compat(self):
        """Old ActionSpec creation without v3 fields still works."""
        a = ActionSpec(
            name="old_action",
            description="Legacy action",
            params=[ParamSpec(name="x", type="integer", description="An integer")],
            returns="object",
            permission_required="local_worker",
        )
        assert a.output_schema is None
        assert a.side_effects == []
        assert a.execution_mode == "async"


# ---------------------------------------------------------------------------
# ActionSpec serialization via _action_to_dict
# ---------------------------------------------------------------------------

class TestActionSpecSerialization:
    def test_v3_fields_excluded_when_default(self):
        a = ActionSpec(name="simple", description="Simple action")
        d = ModuleManifest._action_to_dict(a)
        assert "output_schema" not in d
        assert "side_effects" not in d
        assert "execution_mode" not in d

    def test_v3_fields_included_when_set(self):
        a = ActionSpec(
            name="complex",
            description="Complex action",
            output_schema={"type": "object"},
            side_effects=["filesystem_write"],
            execution_mode="background",
        )
        d = ModuleManifest._action_to_dict(a)
        assert d["output_schema"] == {"type": "object"}
        assert d["side_effects"] == ["filesystem_write"]
        assert d["execution_mode"] == "background"

    def test_partial_v3_fields(self):
        """Only non-default v3 fields are included."""
        a = ActionSpec(
            name="partial",
            description="Partial v3",
            side_effects=["network_request"],
            # output_schema and execution_mode stay default
        )
        d = ModuleManifest._action_to_dict(a)
        assert "side_effects" in d
        assert "output_schema" not in d
        assert "execution_mode" not in d


# ---------------------------------------------------------------------------
# ModuleManifest v3 fields
# ---------------------------------------------------------------------------

class TestModuleManifestV3:
    def test_v3_defaults(self):
        m = ModuleManifest(
            module_id="test",
            version="1.0.0",
            description="Test module",
        )
        assert m.resource_limits is None
        assert m.sandbox_level == "none"
        assert m.license == ""
        assert m.optional_dependencies == []
        assert m.module_dependencies == {}
        assert m.signing is None

    def test_v3_with_resource_limits(self):
        limits = ResourceLimits(max_memory_mb=256, max_concurrent_actions=3)
        m = ModuleManifest(
            module_id="test",
            version="1.0.0",
            description="Test module",
            resource_limits=limits,
        )
        assert m.resource_limits is not None
        assert m.resource_limits.max_memory_mb == 256

    def test_v3_with_sandbox_level(self):
        m = ModuleManifest(
            module_id="test",
            version="1.0.0",
            description="Test",
            sandbox_level="strict",
        )
        assert m.sandbox_level == "strict"

    def test_v3_with_license(self):
        m = ModuleManifest(
            module_id="test",
            version="1.0.0",
            description="Test",
            license="MIT",
        )
        assert m.license == "MIT"

    def test_v3_with_module_dependencies(self):
        m = ModuleManifest(
            module_id="advanced",
            version="1.0.0",
            description="Advanced module",
            module_dependencies={
                "filesystem": ">=1.0.0",
                "database": ">=2.0.0,<3.0.0",
            },
        )
        assert m.module_dependencies["filesystem"] == ">=1.0.0"
        assert m.module_dependencies["database"] == ">=2.0.0,<3.0.0"

    def test_v3_with_signing(self):
        sig = ModuleSignature(
            public_key_fingerprint="fp123",
            signature_hex="sig456",
            signed_hash="hash789",
            signed_at="2026-03-01T00:00:00Z",
        )
        m = ModuleManifest(
            module_id="signed",
            version="1.0.0",
            description="Signed module",
            signing=sig,
        )
        assert m.signing is not None
        assert m.signing.public_key_fingerprint == "fp123"

    def test_v3_with_optional_dependencies(self):
        m = ModuleManifest(
            module_id="test",
            version="1.0.0",
            description="Test",
            optional_dependencies=["chromadb", "torch"],
        )
        assert m.optional_dependencies == ["chromadb", "torch"]

    def test_v3_full_manifest(self):
        """A fully-populated v3 manifest with all fields."""
        m = ModuleManifest(
            module_id="full_v3",
            version="3.0.0",
            description="Full v3 module",
            author="Test Author",
            license="Apache-2.0",
            sandbox_level="isolated",
            resource_limits=ResourceLimits(max_memory_mb=1024),
            optional_dependencies=["torch"],
            module_dependencies={"filesystem": ">=1.0.0"},
            signing=ModuleSignature(
                public_key_fingerprint="fp",
                signature_hex="sig",
                signed_hash="hash",
            ),
            actions=[
                ActionSpec(
                    name="analyze",
                    description="Analyze data",
                    output_schema={"type": "object"},
                    side_effects=["state_mutation"],
                    execution_mode="background",
                ),
            ],
        )
        assert m.module_id == "full_v3"
        assert m.sandbox_level == "isolated"
        assert m.resource_limits is not None
        assert m.signing is not None
        assert len(m.actions) == 1
        assert m.actions[0].execution_mode == "background"


# ---------------------------------------------------------------------------
# to_dict() serialization
# ---------------------------------------------------------------------------

class TestManifestToDictV3:
    def test_v3_fields_excluded_when_default(self):
        m = ModuleManifest(
            module_id="minimal",
            version="1.0.0",
            description="Minimal module",
        )
        d = m.to_dict()
        assert "resource_limits" not in d
        assert "sandbox_level" not in d
        assert "license" not in d
        assert "optional_dependencies" not in d
        assert "module_dependencies" not in d
        assert "signing" not in d

    def test_v3_resource_limits_serialized(self):
        m = ModuleManifest(
            module_id="limited",
            version="1.0.0",
            description="Limited module",
            resource_limits=ResourceLimits(max_memory_mb=512, max_concurrent_actions=3),
        )
        d = m.to_dict()
        assert "resource_limits" in d
        assert d["resource_limits"]["max_memory_mb"] == 512
        assert d["resource_limits"]["max_concurrent_actions"] == 3

    def test_v3_sandbox_level_serialized(self):
        m = ModuleManifest(
            module_id="sandboxed",
            version="1.0.0",
            description="Sandboxed",
            sandbox_level="strict",
        )
        d = m.to_dict()
        assert d["sandbox_level"] == "strict"

    def test_v3_license_serialized(self):
        m = ModuleManifest(
            module_id="licensed",
            version="1.0.0",
            description="Licensed",
            license="MIT",
        )
        d = m.to_dict()
        assert d["license"] == "MIT"

    def test_v3_optional_dependencies_serialized(self):
        m = ModuleManifest(
            module_id="deps",
            version="1.0.0",
            description="With deps",
            optional_dependencies=["torch", "chromadb"],
        )
        d = m.to_dict()
        assert d["optional_dependencies"] == ["torch", "chromadb"]

    def test_v3_module_dependencies_serialized(self):
        m = ModuleManifest(
            module_id="mod_deps",
            version="1.0.0",
            description="With module deps",
            module_dependencies={"filesystem": ">=1.0.0", "gui": ">=0.5.0"},
        )
        d = m.to_dict()
        assert d["module_dependencies"]["filesystem"] == ">=1.0.0"
        assert d["module_dependencies"]["gui"] == ">=0.5.0"

    def test_v3_signing_serialized(self):
        sig = ModuleSignature(
            public_key_fingerprint="fp",
            signature_hex="sig_hex",
            signed_hash="hash",
            signed_at="2026-03-01T00:00:00Z",
        )
        m = ModuleManifest(
            module_id="signed",
            version="1.0.0",
            description="Signed",
            signing=sig,
        )
        d = m.to_dict()
        assert d["signing"]["public_key_fingerprint"] == "fp"
        assert d["signing"]["signed_at"] == "2026-03-01T00:00:00Z"

    def test_v3_action_fields_in_manifest_to_dict(self):
        """Actions with v3 fields are properly serialized within manifest."""
        m = ModuleManifest(
            module_id="test",
            version="1.0.0",
            description="Test",
            actions=[
                ActionSpec(
                    name="action_with_v3",
                    description="Has v3 fields",
                    output_schema={"type": "string"},
                    side_effects=["filesystem_write"],
                    execution_mode="background",
                ),
                ActionSpec(
                    name="action_without_v3",
                    description="No v3 fields",
                ),
            ],
        )
        d = m.to_dict()
        a0 = d["actions"][0]
        assert a0["output_schema"] == {"type": "string"}
        assert a0["side_effects"] == ["filesystem_write"]
        assert a0["execution_mode"] == "background"

        a1 = d["actions"][1]
        assert "output_schema" not in a1
        assert "side_effects" not in a1
        assert "execution_mode" not in a1

    def test_backwards_compatible_to_dict(self):
        """Old-style manifest to_dict still works correctly."""
        m = ModuleManifest(
            module_id="legacy",
            version="0.1.0",
            description="Legacy module",
            author="Old Author",
            actions=[
                ActionSpec(
                    name="old_action",
                    description="Old",
                    params=[ParamSpec(name="x", type="integer", description="X")],
                ),
            ],
        )
        d = m.to_dict()
        assert d["module_id"] == "legacy"
        assert len(d["actions"]) == 1
        assert d["actions"][0]["name"] == "old_action"
        # No v3 fields present
        assert "resource_limits" not in d
        assert "signing" not in d

    def test_v2_and_v3_fields_coexist(self):
        """Both v2 and v3 fields serialize correctly together."""
        m = ModuleManifest(
            module_id="hybrid",
            version="2.5.0",
            description="Hybrid module",
            module_type="system",
            provides_services=[ServiceDescriptor(name="my_service", methods=["do"])],
            consumes_services=["other_service"],
            license="MIT",
            sandbox_level="basic",
            resource_limits=ResourceLimits(max_memory_mb=256),
        )
        d = m.to_dict()
        # v2
        assert d["module_type"] == "system"
        assert d["provides_services"][0]["name"] == "my_service"
        assert d["consumes_services"] == ["other_service"]
        # v3
        assert d["license"] == "MIT"
        assert d["sandbox_level"] == "basic"
        assert d["resource_limits"]["max_memory_mb"] == 256
