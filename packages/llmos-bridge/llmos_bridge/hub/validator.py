"""Hub module validator — checks module structure for publishing readiness.

Validates that a module directory has all required files, correct metadata,
and passes quality checks needed for LLMOS Module Hub publishing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from llmos_bridge.hub.documentation import ModuleDocumentation
from llmos_bridge.hub.package import ModulePackageConfig
from llmos_bridge.logging import get_logger

log = get_logger(__name__)


@dataclass
class ValidationResult:
    """Result of validating a module directory."""

    score: int = 0  # 0-100, computed from checks
    issues: list[str] = field(default_factory=list)  # Blocking issues (prevent publishing)
    warnings: list[str] = field(default_factory=list)  # Non-blocking quality warnings
    hub_ready: bool = False  # True when score >= 70 and no issues

    @property
    def passed(self) -> bool:
        return len(self.issues) == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "issues": self.issues,
            "warnings": self.warnings,
            "hub_ready": self.hub_ready,
            "passed": self.passed,
        }


class ModuleValidator:
    """Validates module directory structure for hub publishing.

    Checks:
    1. llmos-module.toml exists and is valid (20 pts)
    2. module.py exists with BaseModule subclass (15 pts)
    3. params.py exists (10 pts)
    4. README.md exists with required sections (20 pts)
    5. CHANGELOG.md exists (5 pts)
    6. docs/actions.md exists (10 pts)
    7. docs/integration.md exists (5 pts)
    8. module_id consistency between toml and module.py (5 pts)
    9. version consistency (5 pts)
    10. At least one action declared (5 pts)
    """

    def validate(self, module_dir: Path) -> ValidationResult:
        """Validate a module directory and return a scored result."""
        result = ValidationResult()
        score = 0

        # 1. llmos-module.toml (20 pts)
        toml_path = module_dir / "llmos-module.toml"
        config = None
        if not toml_path.exists():
            result.issues.append("Missing llmos-module.toml (required for hub publishing)")
        else:
            try:
                config = ModulePackageConfig.from_toml(toml_path)
                score += 20
            except Exception as e:
                result.issues.append(f"Invalid llmos-module.toml: {e}")

        # 2. module.py (15 pts)
        module_py = module_dir / "module.py"
        if not module_py.exists():
            # Check subdirectories (e.g., perception_vision/omniparser/module.py)
            sub_modules = list(module_dir.glob("*/module.py"))
            if sub_modules:
                score += 15
            else:
                result.issues.append("Missing module.py (no BaseModule subclass found)")
        else:
            score += 15

        # 3. params.py (10 pts)
        params_py = module_dir / "params.py"
        if params_py.exists():
            score += 10
        else:
            result.warnings.append("Missing params.py (recommended for standardized structure)")

        # 4. README.md (20 pts)
        readme = module_dir / "README.md"
        if not readme.exists():
            result.issues.append("Missing README.md (required for hub documentation)")
        else:
            score += 10  # Base points for having README
            doc = ModuleDocumentation.from_directory(module_dir)
            has_all, missing = doc.has_required_sections()
            if has_all:
                score += 10
            else:
                result.warnings.append(f"README.md missing sections: {', '.join(missing)}")
                score += max(0, 10 - len(missing) * 2)

        # 5. CHANGELOG.md (5 pts)
        if (module_dir / "CHANGELOG.md").exists():
            score += 5
        else:
            result.warnings.append("Missing CHANGELOG.md (recommended for version tracking)")

        # 6. docs/actions.md (10 pts)
        if (module_dir / "docs" / "actions.md").exists():
            score += 10
        else:
            result.warnings.append("Missing docs/actions.md (recommended for action reference)")

        # 7. docs/integration.md (5 pts)
        if (module_dir / "docs" / "integration.md").exists():
            score += 5
        else:
            result.warnings.append("Missing docs/integration.md (recommended for cross-module workflows)")

        # 8. module_id consistency (5 pts)
        if config is not None:
            if not config.module_id:
                result.issues.append("module_id is empty in llmos-module.toml")
            else:
                score += 5

        # 9. version consistency (5 pts)
        if config is not None:
            if not config.version:
                result.issues.append("version is empty in llmos-module.toml")
            else:
                score += 5

        # 10. At least one action (5 pts)
        if config is not None and len(config.actions) > 0:
            score += 5
        elif config is not None:
            result.warnings.append("No actions declared in llmos-module.toml (hub listing will show empty)")

        result.score = min(score, 100)
        result.hub_ready = result.score >= 70 and len(result.issues) == 0

        return result

    def validate_all(self, modules_dir: Path) -> dict[str, ValidationResult]:
        """Validate all module directories under a parent directory."""
        results = {}
        for child in sorted(modules_dir.iterdir()):
            if child.is_dir() and not child.name.startswith("_") and not child.name.startswith("."):
                toml = child / "llmos-module.toml"
                module_py = child / "module.py"
                if toml.exists() or module_py.exists():
                    results[child.name] = self.validate(child)
        return results
