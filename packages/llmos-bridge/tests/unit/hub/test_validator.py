"""Unit tests — ModuleValidator and ValidationResult."""

from __future__ import annotations

from pathlib import Path

import pytest

from llmos_bridge.hub.validator import ModuleValidator, ValidationResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FULL_README = """\
# My Module

Some preamble text.

## Overview

This module does something useful.

## Actions

- action_one: Does X.
- action_two: Does Y.

## Quick Start

```bash
pip install my-module
```

## Platform Support

Linux, macOS, Windows.
"""

_CHANGELOG = """\
# Changelog

## 1.0.0

- Initial release.
"""

_ACTIONS_DOC = """\
# Actions Reference

## action_one

Detailed description.
"""

_INTEGRATION_DOC = """\
# Integration Guide

How to integrate with other modules.
"""


def _toml_full(module_id: str = "my_module", version: str = "1.0.0") -> str:
    """Return a full llmos-module.toml with one action declared."""
    return (
        "[module]\n"
        f'module_id = "{module_id}"\n'
        f'version = "{version}"\n'
        'module_class_path = "my_module.module:MyModule"\n'
        "\n"
        "[[module.actions]]\n"
        'name = "do_something"\n'
        'description = "Does something"\n'
    )


def _toml_minimal(module_id: str = "my_module", version: str = "1.0.0") -> str:
    """Return a minimal llmos-module.toml (no actions)."""
    return (
        "[module]\n"
        f'module_id = "{module_id}"\n'
        f'version = "{version}"\n'
        'module_class_path = "my_module.module:MyModule"\n'
    )


def _create_complete_module(module_dir: Path) -> None:
    """Create a module directory that should score 100 and pass all checks."""
    module_dir.mkdir(parents=True, exist_ok=True)
    (module_dir / "llmos-module.toml").write_text(_toml_full(), encoding="utf-8")
    (module_dir / "module.py").write_text(
        "class MyModule:\n    pass\n", encoding="utf-8"
    )
    (module_dir / "params.py").write_text(
        "class DoSomethingParams:\n    pass\n", encoding="utf-8"
    )
    (module_dir / "README.md").write_text(_FULL_README, encoding="utf-8")
    (module_dir / "CHANGELOG.md").write_text(_CHANGELOG, encoding="utf-8")
    docs = module_dir / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "actions.md").write_text(_ACTIONS_DOC, encoding="utf-8")
    (docs / "integration.md").write_text(_INTEGRATION_DOC, encoding="utf-8")


# ---------------------------------------------------------------------------
# Tests — ValidationResult
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidationResult:
    def test_defaults(self) -> None:
        r = ValidationResult()
        assert r.score == 0
        assert r.issues == []
        assert r.warnings == []
        assert r.hub_ready is False
        assert r.passed is True  # No issues = passed

    def test_passed_true_when_no_issues(self) -> None:
        r = ValidationResult(score=50, warnings=["minor thing"])
        assert r.passed is True

    def test_passed_false_when_issues_exist(self) -> None:
        r = ValidationResult(score=50, issues=["blocking problem"])
        assert r.passed is False

    def test_to_dict_keys(self) -> None:
        r = ValidationResult(
            score=85,
            issues=["bad"],
            warnings=["meh"],
            hub_ready=False,
        )
        d = r.to_dict()
        assert set(d.keys()) == {"score", "issues", "warnings", "hub_ready", "passed"}
        assert d["score"] == 85
        assert d["issues"] == ["bad"]
        assert d["warnings"] == ["meh"]
        assert d["hub_ready"] is False
        assert d["passed"] is False

    def test_to_dict_passed_reflects_issues(self) -> None:
        r = ValidationResult(score=100, hub_ready=True)
        d = r.to_dict()
        assert d["passed"] is True


# ---------------------------------------------------------------------------
# Tests — ModuleValidator.validate (complete module)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateComplete:
    def test_complete_module_scores_100(self, tmp_path: Path) -> None:
        mod = tmp_path / "my_module"
        _create_complete_module(mod)
        result = ModuleValidator().validate(mod)
        assert result.score == 100
        assert result.issues == []
        assert result.hub_ready is True

    def test_complete_module_passed(self, tmp_path: Path) -> None:
        mod = tmp_path / "my_module"
        _create_complete_module(mod)
        result = ModuleValidator().validate(mod)
        assert result.passed is True


# ---------------------------------------------------------------------------
# Tests — ModuleValidator.validate (missing files / issues)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateMissingFiles:
    def test_missing_toml_is_issue(self, tmp_path: Path) -> None:
        mod = tmp_path / "my_module"
        mod.mkdir()
        (mod / "module.py").write_text("class M: pass\n")
        result = ModuleValidator().validate(mod)
        assert any("llmos-module.toml" in i for i in result.issues)
        assert result.hub_ready is False

    def test_invalid_toml_is_issue(self, tmp_path: Path) -> None:
        mod = tmp_path / "my_module"
        mod.mkdir()
        (mod / "llmos-module.toml").write_text("not valid toml {{{\n")
        (mod / "module.py").write_text("class M: pass\n")
        result = ModuleValidator().validate(mod)
        assert any("Invalid llmos-module.toml" in i for i in result.issues)
        assert result.hub_ready is False

    def test_missing_module_py_is_issue(self, tmp_path: Path) -> None:
        mod = tmp_path / "my_module"
        mod.mkdir()
        (mod / "llmos-module.toml").write_text(_toml_full())
        (mod / "README.md").write_text(_FULL_README)
        result = ModuleValidator().validate(mod)
        assert any("module.py" in i for i in result.issues)

    def test_subdirectory_module_py_accepted(self, tmp_path: Path) -> None:
        """module.py inside a subdirectory should count (e.g. omniparser/module.py)."""
        mod = tmp_path / "my_module"
        mod.mkdir()
        (mod / "llmos-module.toml").write_text(_toml_full())
        (mod / "README.md").write_text(_FULL_README)
        sub = mod / "sub_impl"
        sub.mkdir()
        (sub / "module.py").write_text("class Sub: pass\n")
        result = ModuleValidator().validate(mod)
        # Should NOT have a module.py issue
        assert not any("module.py" in i for i in result.issues)

    def test_missing_readme_is_issue(self, tmp_path: Path) -> None:
        mod = tmp_path / "my_module"
        mod.mkdir()
        (mod / "llmos-module.toml").write_text(_toml_full())
        (mod / "module.py").write_text("class M: pass\n")
        result = ModuleValidator().validate(mod)
        assert any("README.md" in i for i in result.issues)


# ---------------------------------------------------------------------------
# Tests — ModuleValidator.validate (warnings / lower scores)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateWarnings:
    def test_missing_optional_files_produce_warnings(self, tmp_path: Path) -> None:
        """Missing params.py, CHANGELOG, docs/* are warnings, not issues."""
        mod = tmp_path / "my_module"
        mod.mkdir()
        (mod / "llmos-module.toml").write_text(_toml_full())
        (mod / "module.py").write_text("class M: pass\n")
        (mod / "README.md").write_text(_FULL_README)
        result = ModuleValidator().validate(mod)
        # Should warn about params.py, CHANGELOG.md, docs/actions.md, docs/integration.md
        assert any("params.py" in w for w in result.warnings)
        assert any("CHANGELOG.md" in w for w in result.warnings)
        assert any("docs/actions.md" in w for w in result.warnings)
        assert any("docs/integration.md" in w for w in result.warnings)
        # No blocking issues
        assert result.issues == []

    def test_missing_optional_files_lower_score(self, tmp_path: Path) -> None:
        """Score should be lower than 100 when optional files are missing."""
        mod = tmp_path / "my_module"
        mod.mkdir()
        (mod / "llmos-module.toml").write_text(_toml_full())
        (mod / "module.py").write_text("class M: pass\n")
        (mod / "README.md").write_text(_FULL_README)
        result = ModuleValidator().validate(mod)
        # Missing: params.py (10), CHANGELOG (5), docs/actions.md (10), docs/integration.md (5) = -30
        assert result.score == 70
        assert result.hub_ready is True  # 70 >= 70 and no issues

    def test_no_actions_declared_is_warning(self, tmp_path: Path) -> None:
        mod = tmp_path / "my_module"
        _create_complete_module(mod)
        # Overwrite toml with no actions
        (mod / "llmos-module.toml").write_text(_toml_minimal())
        result = ModuleValidator().validate(mod)
        assert any("No actions declared" in w for w in result.warnings)
        # Score drops by 5 (no action points)
        assert result.score == 95

    def test_readme_missing_sections_warning(self, tmp_path: Path) -> None:
        """README without required sections produces a warning."""
        mod = tmp_path / "my_module"
        _create_complete_module(mod)
        # Overwrite README with only overview section
        (mod / "README.md").write_text("## Overview\n\nSome text.\n")
        result = ModuleValidator().validate(mod)
        assert any("README.md missing sections" in w for w in result.warnings)
        # Should still not be an issue
        assert not any("README.md" in i for i in result.issues)


# ---------------------------------------------------------------------------
# Tests — ModuleValidator.validate (metadata checks)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateMetadata:
    def test_empty_module_id_is_issue(self, tmp_path: Path) -> None:
        mod = tmp_path / "my_module"
        mod.mkdir()
        toml_content = (
            "[module]\n"
            'module_id = ""\n'
            'version = "1.0.0"\n'
            'module_class_path = "my_module.module:MyModule"\n'
        )
        (mod / "llmos-module.toml").write_text(toml_content)
        (mod / "module.py").write_text("class M: pass\n")
        (mod / "README.md").write_text(_FULL_README)
        result = ModuleValidator().validate(mod)
        assert any("module_id is empty" in i for i in result.issues)
        assert result.hub_ready is False

    def test_empty_version_is_issue(self, tmp_path: Path) -> None:
        mod = tmp_path / "my_module"
        mod.mkdir()
        toml_content = (
            "[module]\n"
            'module_id = "my_module"\n'
            'version = ""\n'
            'module_class_path = "my_module.module:MyModule"\n'
        )
        (mod / "llmos-module.toml").write_text(toml_content)
        (mod / "module.py").write_text("class M: pass\n")
        (mod / "README.md").write_text(_FULL_README)
        result = ModuleValidator().validate(mod)
        assert any("version is empty" in i for i in result.issues)
        assert result.hub_ready is False


# ---------------------------------------------------------------------------
# Tests — ModuleValidator.validate (score calculation)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestScoreCalculation:
    def test_only_toml_and_module_py(self, tmp_path: Path) -> None:
        """Only toml (20) + module.py (15) + module_id (5) + version (5) = 45."""
        mod = tmp_path / "my_module"
        mod.mkdir()
        (mod / "llmos-module.toml").write_text(_toml_minimal())
        (mod / "module.py").write_text("class M: pass\n")
        result = ModuleValidator().validate(mod)
        # 20 (toml) + 15 (module.py) + 5 (module_id) + 5 (version) = 45
        assert result.score == 45
        assert result.hub_ready is False  # 45 < 70

    def test_score_capped_at_100(self, tmp_path: Path) -> None:
        """Score should never exceed 100."""
        mod = tmp_path / "my_module"
        _create_complete_module(mod)
        result = ModuleValidator().validate(mod)
        assert result.score <= 100

    def test_hub_ready_threshold(self, tmp_path: Path) -> None:
        """hub_ready requires score >= 70 AND no issues."""
        mod = tmp_path / "my_module"
        mod.mkdir()
        # Set up enough for score >= 70 but with an issue
        (mod / "llmos-module.toml").write_text(_toml_full())
        # No module.py -> issue, but score from other items could be high
        (mod / "README.md").write_text(_FULL_README)
        (mod / "params.py").write_text("class P: pass\n")
        (mod / "CHANGELOG.md").write_text(_CHANGELOG)
        docs = mod / "docs"
        docs.mkdir()
        (docs / "actions.md").write_text(_ACTIONS_DOC)
        (docs / "integration.md").write_text(_INTEGRATION_DOC)
        result = ModuleValidator().validate(mod)
        # Has issues (missing module.py) so hub_ready must be False
        assert len(result.issues) > 0
        assert result.hub_ready is False


# ---------------------------------------------------------------------------
# Tests — ModuleValidator.validate_all
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateAll:
    def test_validates_multiple_modules(self, tmp_path: Path) -> None:
        # Module A — complete
        mod_a = tmp_path / "mod_a"
        _create_complete_module(mod_a)

        # Module B — only toml + module.py
        mod_b = tmp_path / "mod_b"
        mod_b.mkdir()
        (mod_b / "llmos-module.toml").write_text(_toml_minimal("mod_b"))
        (mod_b / "module.py").write_text("class B: pass\n")

        results = ModuleValidator().validate_all(tmp_path)
        assert "mod_a" in results
        assert "mod_b" in results
        assert results["mod_a"].score == 100
        assert results["mod_b"].score < 100

    def test_skips_hidden_and_underscore_dirs(self, tmp_path: Path) -> None:
        # Hidden directory
        hidden = tmp_path / ".hidden"
        hidden.mkdir()
        (hidden / "llmos-module.toml").write_text(_toml_minimal("hidden"))

        # Underscore directory
        under = tmp_path / "_internal"
        under.mkdir()
        (under / "llmos-module.toml").write_text(_toml_minimal("internal"))

        # Normal module
        normal = tmp_path / "normal"
        _create_complete_module(normal)

        results = ModuleValidator().validate_all(tmp_path)
        assert ".hidden" not in results
        assert "_internal" not in results
        assert "normal" in results

    def test_skips_dirs_without_toml_or_module_py(self, tmp_path: Path) -> None:
        """Directories that have neither llmos-module.toml nor module.py are skipped."""
        unrelated = tmp_path / "random_dir"
        unrelated.mkdir()
        (unrelated / "some_file.txt").write_text("not a module")

        results = ModuleValidator().validate_all(tmp_path)
        assert "random_dir" not in results

    def test_empty_parent_returns_empty_dict(self, tmp_path: Path) -> None:
        results = ModuleValidator().validate_all(tmp_path)
        assert results == {}
