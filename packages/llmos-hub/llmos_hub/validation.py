"""Server-side validation for module tarballs before publishing.

Standalone — does NOT import anything from llmos_bridge to keep the hub
server independent.
"""

from __future__ import annotations

import io
import tarfile
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)


@dataclass
class PublishValidation:
    """Result of validating a tarball for publishing."""

    score: int = 0
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    module_id: str = ""
    version: str = ""
    description: str = ""
    author: str = ""
    tags: list[str] = field(default_factory=list)
    hub_ready: bool = False
    # Phase 4: compatibility metadata
    min_bridge_version: str = ""
    max_bridge_version: str = ""
    python_requires: str = ""
    # Phase 4: extracted root for scanner (set when keep_extracted=True)
    extracted_root: Path | None = None


def _parse_toml_simple(text: str) -> dict[str, str]:
    """Minimal TOML parser for simple key=value pairs."""
    result: dict[str, str] = {}
    for line in text.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("["):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            result[key] = value
    return result


def _parse_toml_section(text: str, section: str) -> dict[str, str]:
    """Extract key=value pairs from a specific [section]."""
    result: dict[str, str] = {}
    in_section = False
    for line in text.strip().splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            in_section = stripped.lower() == f"[{section}]"
            continue
        if in_section and "=" in stripped and not stripped.startswith("#"):
            key, _, value = stripped.partition("=")
            result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def _parse_toml_list(text: str, key: str) -> list[str]:
    """Extract a simple TOML list value for a key like ``tags = ["a", "b"]``."""
    for line in text.strip().splitlines():
        line = line.strip()
        if line.startswith(f"{key}") and "=" in line:
            _, _, value = line.partition("=")
            value = value.strip()
            if value.startswith("[") and value.endswith("]"):
                items = value[1:-1].split(",")
                return [i.strip().strip('"').strip("'") for i in items if i.strip()]
    return []


async def validate_for_publish(
    tarball_data: bytes,
    *,
    min_score: int = 70,
    keep_extracted: bool = False,
) -> PublishValidation:
    """Validate a tarball for publishing to the hub.

    If *keep_extracted* is True, ``result.extracted_root`` points to the
    extracted directory and the caller is responsible for cleanup.

    Scoring (100 max):
    - llmos-module.toml present + valid:  20
    - module.py present:                  15
    - params.py present:                  10
    - README.md with sections:            20
    - CHANGELOG.md:                        5
    - docs/actions.md:                    10
    - docs/integration.md:                 5
    - module_id consistency:               5
    - version present:                     5
    - ≥1 action declared:                  5
    """
    result = PublishValidation()

    tmpdir_obj = tempfile.TemporaryDirectory()
    tmp = Path(tmpdir_obj.name)
    try:
        try:
            with tarfile.open(fileobj=io.BytesIO(tarball_data), mode="r:gz") as tar:
                # Security: reject members with path traversal
                for member in tar.getmembers():
                    if member.name.startswith("/") or ".." in member.name:
                        result.issues.append(f"Path traversal in tarball: {member.name}")
                        return result
                tar.extractall(tmp, filter="data")
        except (tarfile.TarError, OSError) as exc:
            result.issues.append(f"Invalid tarball: {exc}")
            return result

        # Find the module root (may be nested one level)
        candidates = list(tmp.iterdir())
        if len(candidates) == 1 and candidates[0].is_dir():
            root = candidates[0]
        else:
            root = tmp

        score = 0

        # 1. llmos-module.toml (20pts)
        toml_path = root / "llmos-module.toml"
        toml_data: dict[str, str] = {}
        toml_text = ""
        if toml_path.exists():
            toml_text = toml_path.read_text(encoding="utf-8", errors="replace")
            toml_data = _parse_toml_simple(toml_text)
            if toml_data.get("module_id") and toml_data.get("version"):
                score += 20
                result.module_id = toml_data["module_id"]
                result.version = toml_data["version"]
                result.description = toml_data.get("description", "")
                result.author = toml_data.get("author", "")
                result.tags = _parse_toml_list(toml_text, "tags")
            else:
                result.issues.append("llmos-module.toml missing module_id or version")
                score += 5
        else:
            result.issues.append("Missing llmos-module.toml")

        # Parse [compatibility] section for bridge/python version constraints.
        if toml_text:
            compat = _parse_toml_section(toml_text, "compatibility")
            result.min_bridge_version = compat.get("min_bridge_version", "")
            result.max_bridge_version = compat.get("max_bridge_version", "")
            result.python_requires = compat.get("python_requires", "")

        # 2. module.py (15pts)
        if (root / "module.py").exists():
            score += 15
        else:
            result.issues.append("Missing module.py")

        # 3. params.py (10pts)
        if (root / "params.py").exists():
            score += 10
        else:
            result.warnings.append("Missing params.py")

        # 4. README.md (20pts)
        readme = root / "README.md"
        if readme.exists():
            content = readme.read_text(encoding="utf-8", errors="replace").lower()
            sections_found = sum(1 for h in ["# ", "## "] if h in content)
            if sections_found >= 2:
                score += 20
            else:
                score += 10
                result.warnings.append("README.md has few sections")
        else:
            result.warnings.append("Missing README.md")

        # 5. CHANGELOG.md (5pts)
        if (root / "CHANGELOG.md").exists():
            score += 5
        else:
            result.warnings.append("Missing CHANGELOG.md")

        # 6. docs/actions.md (10pts)
        if (root / "docs" / "actions.md").exists():
            score += 10
        else:
            result.warnings.append("Missing docs/actions.md")

        # 7. docs/integration.md (5pts)
        if (root / "docs" / "integration.md").exists():
            score += 5
        else:
            result.warnings.append("Missing docs/integration.md")

        # 8. module_id consistency (5pts)
        if result.module_id and root.name == result.module_id:
            score += 5
        elif result.module_id:
            result.warnings.append(f"Directory name '{root.name}' differs from module_id '{result.module_id}'")

        # 9. version present (5pts)
        if result.version:
            score += 5

        # 10. ≥1 action declared in toml (5pts)
        actions_str = toml_data.get("actions", "")
        if actions_str:
            score += 5
        elif (root / "module.py").exists():
            # Check for _action_ methods
            mod_text = (root / "module.py").read_text(encoding="utf-8", errors="replace")
            if "_action_" in mod_text:
                score += 5

        result.score = min(score, 100)
        result.hub_ready = result.score >= min_score and not result.issues

        # Expose extracted root for scanner.
        if keep_extracted:
            result.extracted_root = root
    finally:
        if not keep_extracted:
            tmpdir_obj.cleanup()

    return result
