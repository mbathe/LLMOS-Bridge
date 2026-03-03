"""Tests for hub.package — ModulePackageConfig + ModulePackage."""

from __future__ import annotations

from pathlib import Path

import pytest

from llmos_bridge.hub.package import ModulePackage, ModulePackageConfig


# ---------------------------------------------------------------------------
# ModulePackageConfig
# ---------------------------------------------------------------------------

class TestModulePackageConfig:
    def test_from_toml_minimal(self, tmp_path: Path):
        toml = tmp_path / "llmos-module.toml"
        toml.write_text(
            '[module]\n'
            'module_id = "hello"\n'
            'version = "1.0.0"\n'
            'module_class_path = "hello.module:HelloModule"\n'
        )
        config = ModulePackageConfig.from_toml(toml)
        assert config.module_id == "hello"
        assert config.version == "1.0.0"
        assert config.module_class_path == "hello.module:HelloModule"
        assert config.platforms == ["all"]
        assert config.requirements == []
        assert config.sandbox_level == "basic"

    def test_from_toml_full(self, tmp_path: Path):
        toml = tmp_path / "llmos-module.toml"
        toml.write_text(
            '[module]\n'
            'module_id = "smart_sensor"\n'
            'version = "2.3.1"\n'
            'description = "Smart sensor integration"\n'
            'author = "Jane Doe"\n'
            'license = "MIT"\n'
            'homepage = "https://example.com"\n'
            'module_class_path = "smart_sensor.module:SmartSensor"\n'
            'platforms = ["linux", "macos"]\n'
            'requirements = ["numpy>=1.20", "scipy"]\n'
            'tags = ["iot", "sensor"]\n'
            'sandbox_level = "strict"\n'
            '\n'
            '[module.module_dependencies]\n'
            'iot = ">=1.0.0"\n'
        )
        config = ModulePackageConfig.from_toml(toml)
        assert config.module_id == "smart_sensor"
        assert config.version == "2.3.1"
        assert config.description == "Smart sensor integration"
        assert config.author == "Jane Doe"
        assert config.license == "MIT"
        assert config.homepage == "https://example.com"
        assert config.platforms == ["linux", "macos"]
        assert config.requirements == ["numpy>=1.20", "scipy"]
        assert config.tags == ["iot", "sensor"]
        assert config.sandbox_level == "strict"
        assert config.module_dependencies == {"iot": ">=1.0.0"}

    def test_from_toml_missing_required_field(self, tmp_path: Path):
        toml = tmp_path / "llmos-module.toml"
        toml.write_text(
            '[module]\n'
            'module_id = "broken"\n'
            # missing version and module_class_path
        )
        with pytest.raises(Exception):
            ModulePackageConfig.from_toml(toml)

    def test_defaults(self):
        config = ModulePackageConfig(
            module_id="test",
            version="0.1.0",
            module_class_path="test:Test",
        )
        assert config.description == ""
        assert config.author == ""
        assert config.license == ""
        assert config.homepage == ""
        assert config.module_dependencies == {}


# ---------------------------------------------------------------------------
# ModulePackage
# ---------------------------------------------------------------------------

class TestModulePackage:
    def _create_package(self, tmp_path: Path) -> Path:
        pkg = tmp_path / "my_module"
        pkg.mkdir()
        (pkg / "llmos-module.toml").write_text(
            '[module]\n'
            'module_id = "my_module"\n'
            'version = "1.0.0"\n'
            'module_class_path = "my_module.module:MyModule"\n'
        )
        mod_dir = pkg / "my_module"
        mod_dir.mkdir()
        (mod_dir / "__init__.py").write_text("")
        (mod_dir / "module.py").write_text("class MyModule: pass")
        return pkg

    def test_from_directory(self, tmp_path: Path):
        pkg_path = self._create_package(tmp_path)
        package = ModulePackage.from_directory(pkg_path)
        assert package.config.module_id == "my_module"
        assert package.config.version == "1.0.0"
        assert package.path == pkg_path

    def test_from_directory_no_toml(self, tmp_path: Path):
        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(FileNotFoundError, match="No llmos-module.toml"):
            ModulePackage.from_directory(empty)

    def test_package_has_config_and_path(self, tmp_path: Path):
        pkg_path = self._create_package(tmp_path)
        package = ModulePackage.from_directory(pkg_path)
        assert isinstance(package.config, ModulePackageConfig)
        assert isinstance(package.path, Path)

    def test_multiple_packages_in_same_dir(self, tmp_path: Path):
        for name in ["mod_a", "mod_b"]:
            pkg = tmp_path / name
            pkg.mkdir()
            (pkg / "llmos-module.toml").write_text(
                f'[module]\nmodule_id = "{name}"\nversion = "1.0.0"\n'
                f'module_class_path = "{name}.module:Mod"\n'
            )
        pkg_a = ModulePackage.from_directory(tmp_path / "mod_a")
        pkg_b = ModulePackage.from_directory(tmp_path / "mod_b")
        assert pkg_a.config.module_id == "mod_a"
        assert pkg_b.config.module_id == "mod_b"
