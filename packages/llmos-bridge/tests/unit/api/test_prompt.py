"""Unit tests — SystemPromptGenerator.

Tests the system prompt generation from module manifests without
any network or daemon dependency.
"""

from __future__ import annotations

import pytest

from llmos_bridge.api.prompt import SystemPromptGenerator
from llmos_bridge.modules.manifest import ActionSpec, ModuleManifest, ParamSpec


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fs_manifest() -> ModuleManifest:
    return ModuleManifest(
        module_id="filesystem",
        version="1.0.0",
        description="Read and write files on the local filesystem.",
        author="LLMOS",
        platforms=["all"],
        actions=[
            ActionSpec(
                name="read_file",
                description="Read the contents of a file.",
                params=[
                    ParamSpec(name="path", type="string", description="Absolute file path.", required=True),
                    ParamSpec(
                        name="encoding",
                        type="string",
                        description="Text encoding.",
                        required=False,
                        default="utf-8",
                    ),
                ],
                returns="object",
                returns_description="File contents and metadata.",
                permission_required="readonly",
            ),
            ActionSpec(
                name="write_file",
                description="Write content to a file.",
                params=[
                    ParamSpec(name="path", type="string", description="Absolute file path.", required=True),
                    ParamSpec(name="content", type="string", description="Content to write.", required=True),
                ],
                permission_required="local_worker",
            ),
            ActionSpec(
                name="delete_file",
                description="Delete a file from the filesystem.",
                params=[
                    ParamSpec(name="path", type="string", description="Absolute file path.", required=True),
                ],
                permission_required="power_user",
            ),
        ],
        declared_permissions=["filesystem_read", "filesystem_write"],
    )


@pytest.fixture
def exec_manifest() -> ModuleManifest:
    return ModuleManifest(
        module_id="os_exec",
        version="1.0.0",
        description="Execute OS commands and manage processes.",
        author="LLMOS",
        platforms=["linux", "darwin", "win32"],
        actions=[
            ActionSpec(
                name="run_command",
                description="Run a shell command.",
                params=[
                    ParamSpec(name="command", type="array", description="Command as list of strings.", required=True),
                ],
                permission_required="local_worker",
                examples=[{"command": ["ls", "-la", "/tmp"]}],
            ),
            ActionSpec(
                name="list_processes",
                description="List running processes.",
                params=[],
                permission_required="readonly",
            ),
        ],
    )


@pytest.fixture
def generator(fs_manifest: ModuleManifest, exec_manifest: ModuleManifest) -> SystemPromptGenerator:
    return SystemPromptGenerator(
        manifests=[fs_manifest, exec_manifest],
        permission_profile="local_worker",
        daemon_version="0.8.0",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSystemPromptGenerate:
    def test_generates_non_empty_prompt(self, generator: SystemPromptGenerator) -> None:
        prompt = generator.generate()
        assert isinstance(prompt, str)
        assert len(prompt) > 500

    def test_contains_identity_section(self, generator: SystemPromptGenerator) -> None:
        prompt = generator.generate()
        assert "LLMOS Bridge" in prompt
        assert "v0.8.0" in prompt
        assert "2 modules" in prompt

    def test_contains_protocol_section(self, generator: SystemPromptGenerator) -> None:
        prompt = generator.generate()
        assert "IML Protocol v2" in prompt
        assert "protocol_version" in prompt
        assert '"2.0"' in prompt

    def test_contains_module_listing(self, generator: SystemPromptGenerator) -> None:
        prompt = generator.generate()
        assert "### filesystem" in prompt
        assert "### os_exec" in prompt
        assert "read_file" in prompt
        assert "write_file" in prompt
        assert "run_command" in prompt

    def test_contains_param_schemas(self, generator: SystemPromptGenerator) -> None:
        prompt = generator.generate()
        assert "`path`" in prompt
        assert "Absolute file path" in prompt
        assert "*(required)*" in prompt

    def test_contains_permission_section(self, generator: SystemPromptGenerator) -> None:
        prompt = generator.generate()
        assert "Permission Model" in prompt
        assert "local_worker" in prompt
        assert "read/write files" in prompt

    def test_contains_guidelines(self, generator: SystemPromptGenerator) -> None:
        prompt = generator.generate()
        assert "Guidelines" in prompt
        assert "simplest plan" in prompt

    def test_contains_examples(self, generator: SystemPromptGenerator) -> None:
        prompt = generator.generate()
        assert "Examples" in prompt
        assert "read_file" in prompt

    def test_contains_chained_example(self, generator: SystemPromptGenerator) -> None:
        prompt = generator.generate()
        assert "depends_on" in prompt
        assert "{{result." in prompt


@pytest.mark.unit
class TestSystemPromptOptions:
    def test_no_schemas(self, fs_manifest: ModuleManifest) -> None:
        gen = SystemPromptGenerator(
            manifests=[fs_manifest],
            include_schemas=False,
        )
        prompt = gen.generate()
        assert "read_file" in prompt
        # Should NOT have parameter details
        assert "*(required)*" not in prompt

    def test_no_examples(self, fs_manifest: ModuleManifest) -> None:
        gen = SystemPromptGenerator(
            manifests=[fs_manifest],
            include_examples=False,
        )
        prompt = gen.generate()
        assert "Examples" not in prompt

    def test_max_actions_per_module(self, fs_manifest: ModuleManifest) -> None:
        # Under local_worker, filesystem has 2 allowed (read_file, write_file)
        # and 1 denied (delete_file). With max=1, we show 1 + "... and 1 more".
        gen = SystemPromptGenerator(
            manifests=[fs_manifest],
            max_actions_per_module=1,
        )
        prompt = gen.generate()
        assert "read_file" in prompt
        assert "... and 1 more actions" in prompt
        # delete_file should appear in the denied section
        assert "Denied by current profile" in prompt
        assert "`delete_file`" in prompt

    def test_empty_manifests(self) -> None:
        gen = SystemPromptGenerator(manifests=[])
        prompt = gen.generate()
        assert "No modules loaded" in prompt

    def test_permission_profiles(self, fs_manifest: ModuleManifest) -> None:
        for profile in ["readonly", "local_worker", "power_user", "unrestricted"]:
            gen = SystemPromptGenerator(
                manifests=[fs_manifest],
                permission_profile=profile,
            )
            prompt = gen.generate()
            assert f"**{profile}**" in prompt


@pytest.mark.unit
class TestSystemPromptToDict:
    def test_to_dict_structure(self, generator: SystemPromptGenerator) -> None:
        data = generator.to_dict()
        assert "system_prompt" in data
        assert "permission_profile" in data
        assert "daemon_version" in data
        assert "modules" in data
        assert "total_actions" in data

    def test_to_dict_modules_list(self, generator: SystemPromptGenerator) -> None:
        data = generator.to_dict()
        assert len(data["modules"]) == 2
        assert data["modules"][0]["module_id"] == "filesystem"
        assert data["modules"][1]["module_id"] == "os_exec"

    def test_to_dict_action_count(self, generator: SystemPromptGenerator) -> None:
        data = generator.to_dict()
        assert data["total_actions"] == 5  # 3 fs + 2 exec

    def test_to_dict_prompt_is_string(self, generator: SystemPromptGenerator) -> None:
        data = generator.to_dict()
        assert isinstance(data["system_prompt"], str)
        assert len(data["system_prompt"]) > 0


@pytest.mark.unit
class TestActionSpecExamples:
    def test_module_specific_examples_included(self) -> None:
        manifest = ModuleManifest(
            module_id="os_exec",
            version="1.0.0",
            description="Execute OS commands.",
            actions=[
                ActionSpec(
                    name="run_command",
                    description="Run a command.",
                    params=[
                        ParamSpec(name="command", type="array", description="Cmd", required=True),
                    ],
                    examples=[{"command": ["echo", "hello"]}],
                ),
            ],
        )
        gen = SystemPromptGenerator(manifests=[manifest])
        prompt = gen.generate()
        assert "Module-specific examples" in prompt
        assert "echo" in prompt

    def test_no_module_examples_when_none(self) -> None:
        manifest = ModuleManifest(
            module_id="test",
            version="1.0.0",
            description="Test module.",
            actions=[
                ActionSpec(name="do_thing", description="Does a thing."),
            ],
        )
        gen = SystemPromptGenerator(manifests=[manifest])
        prompt = gen.generate()
        # No filesystem or os_exec → no built-in examples either
        # No action examples → no module-specific examples
        assert "Module-specific examples" not in prompt


@pytest.mark.unit
class TestPermissionDisplayInActions:
    def test_non_default_permission_shown(self, fs_manifest: ModuleManifest) -> None:
        gen = SystemPromptGenerator(manifests=[fs_manifest])
        prompt = gen.generate()
        # read_file has permission_required="readonly" which is != local_worker default
        assert "Permission: `readonly`" in prompt
        # delete_file (power_user) is denied under local_worker → shown in denied section
        assert "Denied by current profile" in prompt
        assert "`delete_file`" in prompt

    def test_non_default_permission_shown_power_user(self, fs_manifest: ModuleManifest) -> None:
        """Under power_user, delete_file is allowed and shows its permission level."""
        gen = SystemPromptGenerator(
            manifests=[fs_manifest], permission_profile="power_user"
        )
        prompt = gen.generate()
        assert "Permission: `power_user`" in prompt
        assert "Denied by current profile" not in prompt

    def test_default_permission_not_shown(self, fs_manifest: ModuleManifest) -> None:
        gen = SystemPromptGenerator(manifests=[fs_manifest])
        prompt = gen.generate()
        lines = prompt.split("\n")
        # write_file has local_worker (default) — should NOT have a Permission line
        in_write_section = False
        for line in lines:
            if "**write_file**" in line:
                in_write_section = True
            elif line.startswith("- **") and in_write_section:
                break
            elif in_write_section and "Permission: `local_worker`" in line:
                pytest.fail("Default permission local_worker should not be displayed for write_file")
