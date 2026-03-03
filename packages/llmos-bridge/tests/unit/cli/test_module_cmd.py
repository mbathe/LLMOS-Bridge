"""Unit tests -- CLI module commands (validate, sign, package)."""

from __future__ import annotations

import tarfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from llmos_bridge.cli.commands.module_cmd import app

runner = CliRunner()

# -- Helpers ----------------------------------------------------------------

_MINIMAL_TOML = """\
[module]
module_id = "test_mod"
version = "1.0.0"
description = "A test module"
module_class_path = "test_mod.module:TestModule"
"""

_MINIMAL_TOML_WITH_ACTIONS = """\
[module]
module_id = "test_mod"
version = "1.0.0"
description = "A test module"
module_class_path = "test_mod.module:TestModule"

[[module.actions]]
name = "do_thing"
description = "Does a thing"
risk_level = "low"
"""

_README_WITH_SECTIONS = """\
# Test Module

## Overview
A test module.

## Actions
- do_thing

## Quick Start
Just use it.

## Platform Support
All platforms.
"""


def _make_complete_module(tmp_path: Path) -> Path:
    """Create a module directory that scores high enough to be hub_ready."""
    mod = tmp_path / "test_mod"
    mod.mkdir()

    (mod / "llmos-module.toml").write_text(_MINIMAL_TOML_WITH_ACTIONS)
    (mod / "module.py").write_text("class TestModule: pass\n")
    (mod / "params.py").write_text("# params\n")
    (mod / "README.md").write_text(_README_WITH_SECTIONS)
    (mod / "CHANGELOG.md").write_text("# Changelog\n")

    docs = mod / "docs"
    docs.mkdir()
    (docs / "actions.md").write_text("# Actions\n")
    (docs / "integration.md").write_text("# Integration\n")

    return mod


def _make_minimal_module(tmp_path: Path) -> Path:
    """Create a module directory with only toml + module.py (passes but low score)."""
    mod = tmp_path / "min_mod"
    mod.mkdir()

    (mod / "llmos-module.toml").write_text(_MINIMAL_TOML)
    (mod / "module.py").write_text("class MinModule: pass\n")

    return mod


# -- validate ---------------------------------------------------------------


@pytest.mark.unit
class TestValidateCommand:
    def test_validate_complete_module_passes(self, tmp_path: Path) -> None:
        mod = _make_complete_module(tmp_path)
        result = runner.invoke(app, ["validate", str(mod)])

        assert result.exit_code == 0
        assert "PASS" in result.output
        assert "Hub Ready: Yes" in result.output

    def test_validate_missing_directory(self, tmp_path: Path) -> None:
        missing = tmp_path / "does_not_exist"
        result = runner.invoke(app, ["validate", str(missing)])

        assert result.exit_code == 1
        assert "Directory not found" in result.output

    def test_validate_not_a_directory(self, tmp_path: Path) -> None:
        file_path = tmp_path / "some_file.txt"
        file_path.write_text("not a dir")
        result = runner.invoke(app, ["validate", str(file_path)])

        assert result.exit_code == 1
        assert "Not a directory" in result.output

    def test_validate_missing_toml_shows_issues(self, tmp_path: Path) -> None:
        mod = tmp_path / "bad_mod"
        mod.mkdir()
        (mod / "module.py").write_text("class M: pass\n")

        result = runner.invoke(app, ["validate", str(mod)])

        assert result.exit_code == 1
        assert "FAIL" in result.output
        assert "Issues" in result.output

    def test_validate_verbose_shows_warnings(self, tmp_path: Path) -> None:
        mod = _make_minimal_module(tmp_path)
        result = runner.invoke(app, ["validate", "--verbose", str(mod)])

        # Minimal module has warnings (missing README, CHANGELOG, docs, params)
        assert "Warnings" in result.output

    def test_validate_non_verbose_hides_warning_details(self, tmp_path: Path) -> None:
        mod = _make_minimal_module(tmp_path)
        result = runner.invoke(app, ["validate", str(mod)])

        assert "warning(s) (use --verbose to see)" in result.output


# -- validate-all -----------------------------------------------------------


@pytest.mark.unit
class TestValidateAllCommand:
    def test_validate_all_with_modules(self, tmp_path: Path) -> None:
        _make_complete_module(tmp_path)
        _make_minimal_module(tmp_path)

        result = runner.invoke(app, ["validate-all", str(tmp_path)])

        assert "Validated 2 module(s)" in result.output

    def test_validate_all_no_modules_found(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["validate-all", str(tmp_path)])

        assert result.exit_code == 1
        assert "No modules found" in result.output

    def test_validate_all_invalid_directory(self, tmp_path: Path) -> None:
        missing = tmp_path / "nope"
        result = runner.invoke(app, ["validate-all", str(missing)])

        assert result.exit_code == 1
        assert "Invalid directory" in result.output


# -- sign -------------------------------------------------------------------


@pytest.mark.unit
class TestSignCommand:
    def test_sign_missing_directory(self, tmp_path: Path) -> None:
        missing = tmp_path / "gone"
        key = tmp_path / "fake.key"
        key.write_bytes(b"\x00" * 32)

        result = runner.invoke(app, ["sign", str(missing), "--key", str(key)])

        assert result.exit_code == 1
        assert "Directory not found" in result.output

    def test_sign_missing_toml(self, tmp_path: Path) -> None:
        mod = tmp_path / "no_toml"
        mod.mkdir()
        key = tmp_path / "fake.key"
        key.write_bytes(b"\x00" * 32)

        result = runner.invoke(app, ["sign", str(mod), "--key", str(key)])

        assert result.exit_code == 1
        assert "No llmos-module.toml found" in result.output

    def test_sign_missing_key_file(self, tmp_path: Path) -> None:
        mod = tmp_path / "has_toml"
        mod.mkdir()
        (mod / "llmos-module.toml").write_text(_MINIMAL_TOML)
        missing_key = tmp_path / "missing.key"

        result = runner.invoke(app, ["sign", str(mod), "--key", str(missing_key)])

        assert result.exit_code == 1
        assert "Key file not found" in result.output

    def test_sign_success(self, tmp_path: Path) -> None:
        mod = _make_complete_module(tmp_path)
        key = tmp_path / "test.key"
        key.write_bytes(b"\x00" * 32)

        mock_signature = MagicMock()
        mock_signature.signature_hex = "abcdef0123456789" * 8

        mock_signer_cls = MagicMock()
        mock_signer_cls.load_private_key.return_value = b"\x00" * 32
        mock_signer_instance = MagicMock()
        mock_signer_instance.sign_module.return_value = mock_signature
        mock_signer_cls.return_value = mock_signer_instance

        mock_signing_module = MagicMock()
        mock_signing_module.ModuleSigner = mock_signer_cls

        with patch.dict("sys.modules", {"llmos_bridge.modules.signing": mock_signing_module}):
            result = runner.invoke(app, ["sign", str(mod), "--key", str(key)])

        assert result.exit_code == 0
        assert "Module signed successfully" in result.output
        assert "Signature:" in result.output

    def test_sign_import_error(self, tmp_path: Path) -> None:
        mod = _make_complete_module(tmp_path)
        key = tmp_path / "test.key"
        key.write_bytes(b"\x00" * 32)

        # Remove the module from sys.modules so the import triggers fresh
        import sys

        saved = sys.modules.pop("llmos_bridge.modules.signing", None)
        try:
            with patch.dict(
                "sys.modules",
                {"llmos_bridge.modules.signing": None},
            ):
                result = runner.invoke(app, ["sign", str(mod), "--key", str(key)])
        finally:
            if saved is not None:
                sys.modules["llmos_bridge.modules.signing"] = saved

        assert result.exit_code == 1


# -- package ----------------------------------------------------------------


@pytest.mark.unit
class TestPackageCommand:
    def test_package_missing_directory(self, tmp_path: Path) -> None:
        missing = tmp_path / "gone"
        result = runner.invoke(app, ["package", str(missing)])

        assert result.exit_code == 1
        assert "Directory not found" in result.output

    def test_package_valid_module_creates_tarball(self, tmp_path: Path) -> None:
        mod = _make_complete_module(tmp_path)
        out = tmp_path / "output.tar.gz"

        result = runner.invoke(app, ["package", str(mod), "--output", str(out)])

        assert result.exit_code == 0
        assert "Package created" in result.output
        assert out.exists()

        # Verify it's a valid tar.gz
        with tarfile.open(out, "r:gz") as tar:
            names = tar.getnames()
            assert any("module.py" in n for n in names)

    def test_package_uses_config_for_naming(self, tmp_path: Path) -> None:
        mod = _make_complete_module(tmp_path)

        result = runner.invoke(app, ["package", str(mod)])

        assert result.exit_code == 0
        # The default name should be test_mod-1.0.0.tar.gz in the parent dir
        expected = tmp_path / "test_mod-1.0.0.tar.gz"
        assert expected.exists()
        assert "Package created" in result.output

    def test_package_with_validation_issues_fails(self, tmp_path: Path) -> None:
        mod = tmp_path / "empty_mod"
        mod.mkdir()
        # No toml, no module.py -> issues

        result = runner.invoke(app, ["package", str(mod)])

        assert result.exit_code == 1
        assert "validation issues" in result.output
