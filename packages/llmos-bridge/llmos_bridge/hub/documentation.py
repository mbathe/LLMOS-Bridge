"""Hub documentation parser — loads and structures module documentation.

Reads README.md, docs/actions.md, docs/integration.md, and CHANGELOG.md
from a module directory. Used by:
  - GET /admin/modules/{id}/docs endpoint
  - Hub renderer for displaying module docs on the web
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from llmos_bridge.logging import get_logger

log = get_logger(__name__)


@dataclass
class ModuleDocumentation:
    """Parsed module documentation."""

    readme: str = ""
    actions_doc: str = ""
    integration_doc: str = ""
    changelog: str = ""

    @classmethod
    def from_directory(cls, module_dir: Path) -> "ModuleDocumentation":
        """Load documentation files from a module directory.

        Gracefully handles missing files (returns empty strings).
        """
        doc = cls()

        file_map = {
            "readme": "README.md",
            "actions_doc": "docs/actions.md",
            "integration_doc": "docs/integration.md",
            "changelog": "CHANGELOG.md",
        }

        for attr, filename in file_map.items():
            path = module_dir / filename
            if path.exists():
                try:
                    setattr(doc, attr, path.read_text(encoding="utf-8"))
                except OSError as exc:
                    log.warning(
                        "doc_read_failed",
                        module_dir=str(module_dir),
                        file=filename,
                        error=str(exc),
                    )

        return doc

    def sections(self) -> dict[str, str]:
        """Parse README.md into named sections (by ## headings).

        Returns a dict mapping section name (lowercased) to content.
        Example: {"overview": "...", "actions": "...", "quick start": "..."}
        """
        if not self.readme:
            return {}

        sections: dict[str, str] = {}
        current_section = "header"
        current_content: list[str] = []

        for line in self.readme.split("\n"):
            heading_match = re.match(r"^##\s+(.+)$", line)
            if heading_match:
                # Save previous section.
                if current_content:
                    sections[current_section] = "\n".join(current_content).strip()
                current_section = heading_match.group(1).strip().lower()
                current_content = []
            else:
                current_content.append(line)

        # Save final section.
        if current_content:
            sections[current_section] = "\n".join(current_content).strip()

        return sections

    def has_required_sections(self) -> tuple[bool, list[str]]:
        """Check if README has all required sections for hub publishing.

        Returns (all_present, missing_sections).
        """
        required = {"overview", "actions", "quick start", "platform support"}
        present = set(self.sections().keys())
        missing = required - present
        return len(missing) == 0, sorted(missing)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for API response."""
        sections = self.sections()
        return {
            "readme": self.readme,
            "actions_doc": self.actions_doc,
            "integration_doc": self.integration_doc,
            "changelog": self.changelog,
            "sections": list(sections.keys()),
            "has_all_required_sections": self.has_required_sections()[0],
        }
