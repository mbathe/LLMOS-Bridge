"""Unit tests — ModulePackageConfig v2 with enhanced fields."""

from __future__ import annotations

from pathlib import Path

import pytest

from llmos_bridge.hub.package import (
    ActionDeclaration,
    CapabilityDeclaration,
    DocsConfig,
    ModulePackage,
    ModulePackageConfig,
)


@pytest.mark.unit
class TestActionDeclaration:
    def test_defaults(self):
        action = ActionDeclaration(name="click")
        assert action.name == "click"
        assert action.description == ""
        assert action.risk_level == "low"
        assert action.permission == "local_worker"
        assert action.category == ""

    def test_all_fields(self):
        action = ActionDeclaration(
            name="delete_file",
            description="Permanently delete a file from disk",
            risk_level="high",
            permission="power_user",
            category="filesystem",
        )
        assert action.name == "delete_file"
        assert action.description == "Permanently delete a file from disk"
        assert action.risk_level == "high"
        assert action.permission == "power_user"
        assert action.category == "filesystem"


@pytest.mark.unit
class TestCapabilityDeclaration:
    def test_defaults(self):
        cap = CapabilityDeclaration()
        assert cap.permissions == []
        assert cap.side_effects == []
        assert cap.events_emitted == []
        assert cap.events_subscribed == []
        assert cap.services_provided == []
        assert cap.services_consumed == []

    def test_populated_fields(self):
        cap = CapabilityDeclaration(
            permissions=["filesystem.read", "filesystem.write"],
            side_effects=["creates_files", "modifies_registry"],
            events_emitted=["file.created", "file.deleted"],
            events_subscribed=["plan.started"],
            services_provided=["file_search"],
            services_consumed=["vision", "memory"],
        )
        assert cap.permissions == ["filesystem.read", "filesystem.write"]
        assert cap.side_effects == ["creates_files", "modifies_registry"]
        assert cap.events_emitted == ["file.created", "file.deleted"]
        assert cap.events_subscribed == ["plan.started"]
        assert cap.services_provided == ["file_search"]
        assert cap.services_consumed == ["vision", "memory"]


@pytest.mark.unit
class TestDocsConfig:
    def test_defaults(self):
        docs = DocsConfig()
        assert docs.readme == "README.md"
        assert docs.changelog == "CHANGELOG.md"
        assert docs.actions == "docs/actions.md"
        assert docs.integration == "docs/integration.md"

    def test_custom_paths(self):
        docs = DocsConfig(
            readme="docs/README.rst",
            changelog="docs/HISTORY.md",
            actions="reference/actions.md",
            integration="reference/setup.md",
        )
        assert docs.readme == "docs/README.rst"
        assert docs.changelog == "docs/HISTORY.md"
        assert docs.actions == "reference/actions.md"
        assert docs.integration == "reference/setup.md"


@pytest.mark.unit
class TestModulePackageConfigV2:
    def test_minimal_required_fields(self):
        config = ModulePackageConfig(
            module_id="minimal",
            version="0.1.0",
            module_class_path="minimal:Mod",
        )
        assert config.module_id == "minimal"
        assert config.version == "0.1.0"
        assert config.module_class_path == "minimal:Mod"

    def test_new_fields_defaults(self):
        config = ModulePackageConfig(
            module_id="test",
            version="1.0.0",
            module_class_path="test:Test",
        )
        assert config.module_type == "user"
        assert config.min_bridge_version == ""
        assert config.icon == ""
        assert config.actions == []
        assert config.capabilities is None
        assert config.docs is None

    def test_with_actions_list(self):
        config = ModulePackageConfig(
            module_id="fs",
            version="2.0.0",
            module_class_path="fs.module:FsModule",
            actions=[
                ActionDeclaration(name="read_file", risk_level="low"),
                ActionDeclaration(
                    name="delete_file",
                    description="Remove a file",
                    risk_level="high",
                    permission="power_user",
                    category="destructive",
                ),
            ],
        )
        assert len(config.actions) == 2
        assert config.actions[0].name == "read_file"
        assert config.actions[0].risk_level == "low"
        assert config.actions[1].name == "delete_file"
        assert config.actions[1].permission == "power_user"
        assert config.actions[1].category == "destructive"

    def test_with_capabilities(self):
        config = ModulePackageConfig(
            module_id="smart",
            version="1.0.0",
            module_class_path="smart:Smart",
            capabilities=CapabilityDeclaration(
                permissions=["os_exec.run"],
                side_effects=["spawns_process"],
                services_provided=["code_runner"],
            ),
        )
        assert config.capabilities is not None
        assert config.capabilities.permissions == ["os_exec.run"]
        assert config.capabilities.side_effects == ["spawns_process"]
        assert config.capabilities.services_provided == ["code_runner"]
        assert config.capabilities.events_emitted == []

    def test_with_docs_config(self):
        config = ModulePackageConfig(
            module_id="documented",
            version="1.0.0",
            module_class_path="documented:Doc",
            docs=DocsConfig(readme="README.rst"),
        )
        assert config.docs is not None
        assert config.docs.readme == "README.rst"
        assert config.docs.changelog == "CHANGELOG.md"

    def test_from_toml_minimal(self, tmp_path: Path):
        toml = tmp_path / "llmos-module.toml"
        toml.write_text(
            "[module]\n"
            'module_id = "basic"\n'
            'version = "0.1.0"\n'
            'module_class_path = "basic:Basic"\n'
        )
        config = ModulePackageConfig.from_toml(toml)
        assert config.module_id == "basic"
        assert config.version == "0.1.0"
        assert config.module_type == "user"
        assert config.actions == []
        assert config.capabilities is None
        assert config.docs is None

    def test_from_toml_enhanced(self, tmp_path: Path):
        toml = tmp_path / "llmos-module.toml"
        toml.write_text(
            "[module]\n"
            'module_id = "advanced"\n'
            'version = "3.0.0"\n'
            'description = "Advanced module"\n'
            'author = "LLMOS Team"\n'
            'license = "Apache-2.0"\n'
            'module_class_path = "advanced.mod:AdvancedModule"\n'
            'platforms = ["linux", "macos"]\n'
            'module_type = "system"\n'
            'min_bridge_version = "0.5.0"\n'
            'icon = "puzzle-piece"\n'
            'tags = ["automation", "advanced"]\n'
            'sandbox_level = "strict"\n'
            "\n"
            "[[module.actions]]\n"
            'name = "scan"\n'
            'description = "Scan the environment"\n'
            'risk_level = "medium"\n'
            'permission = "local_worker"\n'
            'category = "perception"\n'
            "\n"
            "[[module.actions]]\n"
            'name = "reset"\n'
            'description = "Factory reset"\n'
            'risk_level = "critical"\n'
            'permission = "unrestricted"\n'
            'category = "admin"\n'
            "\n"
            "[module.capabilities]\n"
            'permissions = ["os_exec.run", "filesystem.write"]\n'
            'side_effects = ["modifies_state"]\n'
            'events_emitted = ["module.reset"]\n'
            'events_subscribed = ["plan.completed"]\n'
            'services_provided = ["scanner"]\n'
            'services_consumed = ["vision"]\n'
            "\n"
            "[module.docs]\n"
            'readme = "docs/README.md"\n'
            'changelog = "HISTORY.md"\n'
            'actions = "docs/api/actions.md"\n'
            'integration = "docs/guides/setup.md"\n'
        )
        config = ModulePackageConfig.from_toml(toml)

        assert config.module_id == "advanced"
        assert config.version == "3.0.0"
        assert config.description == "Advanced module"
        assert config.author == "LLMOS Team"
        assert config.license == "Apache-2.0"
        assert config.module_type == "system"
        assert config.min_bridge_version == "0.5.0"
        assert config.icon == "puzzle-piece"
        assert config.platforms == ["linux", "macos"]
        assert config.tags == ["automation", "advanced"]
        assert config.sandbox_level == "strict"

        assert len(config.actions) == 2
        assert config.actions[0].name == "scan"
        assert config.actions[0].risk_level == "medium"
        assert config.actions[0].category == "perception"
        assert config.actions[1].name == "reset"
        assert config.actions[1].risk_level == "critical"
        assert config.actions[1].permission == "unrestricted"

        assert config.capabilities is not None
        assert config.capabilities.permissions == ["os_exec.run", "filesystem.write"]
        assert config.capabilities.side_effects == ["modifies_state"]
        assert config.capabilities.events_emitted == ["module.reset"]
        assert config.capabilities.events_subscribed == ["plan.completed"]
        assert config.capabilities.services_provided == ["scanner"]
        assert config.capabilities.services_consumed == ["vision"]

        assert config.docs is not None
        assert config.docs.readme == "docs/README.md"
        assert config.docs.changelog == "HISTORY.md"
        assert config.docs.actions == "docs/api/actions.md"
        assert config.docs.integration == "docs/guides/setup.md"

    def test_backward_compat_old_toml_without_new_fields(self, tmp_path: Path):
        """Old TOML files without actions/capabilities/docs/module_type still parse."""
        toml = tmp_path / "llmos-module.toml"
        toml.write_text(
            "[module]\n"
            'module_id = "legacy"\n'
            'version = "1.0.0"\n'
            'description = "A legacy module"\n'
            'author = "Old Author"\n'
            'module_class_path = "legacy.mod:LegacyModule"\n'
            'platforms = ["all"]\n'
            'requirements = ["requests>=2.0"]\n'
            'tags = ["legacy"]\n'
            'sandbox_level = "basic"\n'
            "\n"
            "[module.module_dependencies]\n"
            'filesystem = ">=1.0.0"\n'
        )
        config = ModulePackageConfig.from_toml(toml)
        assert config.module_id == "legacy"
        assert config.version == "1.0.0"
        assert config.module_class_path == "legacy.mod:LegacyModule"
        assert config.requirements == ["requests>=2.0"]
        assert config.module_dependencies == {"filesystem": ">=1.0.0"}
        # New v2 fields fall back to defaults
        assert config.module_type == "user"
        assert config.min_bridge_version == ""
        assert config.icon == ""
        assert config.actions == []
        assert config.capabilities is None
        assert config.docs is None


@pytest.mark.unit
class TestModulePackageV2:
    def test_from_directory(self, tmp_path: Path):
        pkg = tmp_path / "my_mod"
        pkg.mkdir()
        (pkg / "llmos-module.toml").write_text(
            "[module]\n"
            'module_id = "my_mod"\n'
            'version = "1.0.0"\n'
            'module_class_path = "my_mod.module:MyMod"\n'
            'module_type = "system"\n'
            'min_bridge_version = "0.4.0"\n'
            "\n"
            "[[module.actions]]\n"
            'name = "do_thing"\n'
            'description = "Does the thing"\n'
            'risk_level = "medium"\n'
        )
        package = ModulePackage.from_directory(pkg)
        assert package.config.module_id == "my_mod"
        assert package.config.module_type == "system"
        assert package.config.min_bridge_version == "0.4.0"
        assert len(package.config.actions) == 1
        assert package.config.actions[0].name == "do_thing"
        assert package.path == pkg

    def test_from_directory_no_toml(self, tmp_path: Path):
        empty = tmp_path / "empty_mod"
        empty.mkdir()
        with pytest.raises(FileNotFoundError, match="No llmos-module.toml"):
            ModulePackage.from_directory(empty)
