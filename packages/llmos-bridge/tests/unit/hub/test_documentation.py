"""Unit tests — ModuleDocumentation parser."""

from __future__ import annotations

from pathlib import Path

import pytest

from llmos_bridge.hub.documentation import ModuleDocumentation


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FULL_README = """\
# My Module

Some preamble text before sections.

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

_ACTIONS_DOC = """\
# Actions Reference

## action_one

Detailed description of action_one.
"""

_INTEGRATION_DOC = """\
# Integration Guide

How to integrate with other modules.
"""

_CHANGELOG = """\
# Changelog

## 1.0.0

- Initial release.
"""


def _write_all_docs(module_dir: Path) -> None:
    """Write all 4 documentation files into *module_dir*."""
    module_dir.mkdir(parents=True, exist_ok=True)
    (module_dir / "README.md").write_text(_FULL_README, encoding="utf-8")
    docs = module_dir / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "actions.md").write_text(_ACTIONS_DOC, encoding="utf-8")
    (docs / "integration.md").write_text(_INTEGRATION_DOC, encoding="utf-8")
    (module_dir / "CHANGELOG.md").write_text(_CHANGELOG, encoding="utf-8")


# ---------------------------------------------------------------------------
# Tests — from_directory
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFromDirectory:
    def test_loads_readme(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text("# Hello\n\nWorld.", encoding="utf-8")
        doc = ModuleDocumentation.from_directory(tmp_path)
        assert doc.readme == "# Hello\n\nWorld."

    def test_missing_files_return_empty_strings(self, tmp_path: Path) -> None:
        doc = ModuleDocumentation.from_directory(tmp_path)
        assert doc.readme == ""
        assert doc.actions_doc == ""
        assert doc.integration_doc == ""
        assert doc.changelog == ""

    def test_loads_all_four_files(self, tmp_path: Path) -> None:
        _write_all_docs(tmp_path)
        doc = ModuleDocumentation.from_directory(tmp_path)
        assert "My Module" in doc.readme
        assert "Actions Reference" in doc.actions_doc
        assert "Integration Guide" in doc.integration_doc
        assert "Changelog" in doc.changelog

    def test_loads_docs_subdirectory(self, tmp_path: Path) -> None:
        """docs/actions.md and docs/integration.md live under a docs/ subdir."""
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "actions.md").write_text("Actions content", encoding="utf-8")
        (docs / "integration.md").write_text("Integration content", encoding="utf-8")
        doc = ModuleDocumentation.from_directory(tmp_path)
        assert doc.actions_doc == "Actions content"
        assert doc.integration_doc == "Integration content"


# ---------------------------------------------------------------------------
# Tests — sections()
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSections:
    def test_parses_headings(self, tmp_path: Path) -> None:
        _write_all_docs(tmp_path)
        doc = ModuleDocumentation.from_directory(tmp_path)
        secs = doc.sections()
        assert "overview" in secs
        assert "actions" in secs
        assert "quick start" in secs
        assert "platform support" in secs

    def test_empty_readme_returns_empty_dict(self) -> None:
        doc = ModuleDocumentation(readme="")
        assert doc.sections() == {}

    def test_captures_header_before_first_heading(self) -> None:
        doc = ModuleDocumentation(readme="Preamble line.\n\n## First\n\nContent.")
        secs = doc.sections()
        assert "header" in secs
        assert "Preamble line." in secs["header"]
        assert "first" in secs
        assert "Content." in secs["first"]

    def test_sections_lowercased(self) -> None:
        doc = ModuleDocumentation(readme="## My Section\n\nBody text.")
        secs = doc.sections()
        assert "my section" in secs

    def test_multiple_sections_content(self) -> None:
        readme = "## Alpha\n\nAlpha body.\n\n## Beta\n\nBeta body.\n"
        doc = ModuleDocumentation(readme=readme)
        secs = doc.sections()
        assert secs["alpha"] == "Alpha body."
        assert secs["beta"] == "Beta body."


# ---------------------------------------------------------------------------
# Tests — has_required_sections()
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHasRequiredSections:
    def test_all_present(self, tmp_path: Path) -> None:
        _write_all_docs(tmp_path)
        doc = ModuleDocumentation.from_directory(tmp_path)
        ok, missing = doc.has_required_sections()
        assert ok is True
        assert missing == []

    def test_missing_sections_reported(self) -> None:
        doc = ModuleDocumentation(readme="## Overview\n\nSome text.")
        ok, missing = doc.has_required_sections()
        assert ok is False
        assert "actions" in missing
        assert "quick start" in missing
        assert "platform support" in missing

    def test_empty_readme_all_missing(self) -> None:
        doc = ModuleDocumentation(readme="")
        ok, missing = doc.has_required_sections()
        assert ok is False
        assert len(missing) == 4


# ---------------------------------------------------------------------------
# Tests — to_dict()
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestToDict:
    def test_expected_keys(self, tmp_path: Path) -> None:
        _write_all_docs(tmp_path)
        doc = ModuleDocumentation.from_directory(tmp_path)
        d = doc.to_dict()
        assert "readme" in d
        assert "actions_doc" in d
        assert "integration_doc" in d
        assert "changelog" in d
        assert "sections" in d
        assert "has_all_required_sections" in d

    def test_sections_list_matches(self, tmp_path: Path) -> None:
        _write_all_docs(tmp_path)
        doc = ModuleDocumentation.from_directory(tmp_path)
        d = doc.to_dict()
        assert isinstance(d["sections"], list)
        assert "overview" in d["sections"]
        assert d["has_all_required_sections"] is True

    def test_empty_doc_to_dict(self) -> None:
        doc = ModuleDocumentation()
        d = doc.to_dict()
        assert d["readme"] == ""
        assert d["sections"] == []
        assert d["has_all_required_sections"] is False
