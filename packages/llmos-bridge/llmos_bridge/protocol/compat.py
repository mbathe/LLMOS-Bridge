"""IML Protocol — Module version compatibility checker.

When an IML plan declares ``module_requirements``, this module validates
that the currently installed module versions satisfy those constraints
before execution begins.

Version specifiers follow PEP-440 (e.g. ``">=1.2.0"``, ``"==2.0.0"``,
``">=1.0.0,<2.0.0"``).  The ``packaging`` library handles all comparison
logic, which is already a transitive dependency via pip/poetry.

Design decisions:
  - Validation is done at plan submission time (fail fast) rather than
    at action dispatch time (fail late).
  - Specifiers are validated for syntax before comparison so that malformed
    requirements surface a clear ProtocolError instead of a cryptic
    packaging exception.
  - Unknown module IDs in requirements are treated as hard failures, not
    warnings, so that plans cannot silently run against the wrong module set.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from llmos_bridge.exceptions import IMLValidationError


@dataclass
class CompatibilityViolation:
    """A single module version constraint that was not satisfied."""

    module_id: str
    required_specifier: str
    installed_version: str | None  # None if the module is not registered


@dataclass
class CompatibilityReport:
    """Result of a compatibility check for an entire plan."""

    violations: list[CompatibilityViolation] = field(default_factory=list)

    @property
    def is_compatible(self) -> bool:
        return len(self.violations) == 0

    def format_errors(self) -> str:
        """Return a human-readable summary of all violations."""
        lines: list[str] = []
        for v in self.violations:
            if v.installed_version is None:
                lines.append(
                    f"  - Module '{v.module_id}' required ({v.required_specifier}) "
                    f"but is not registered."
                )
            else:
                lines.append(
                    f"  - Module '{v.module_id}' required {v.required_specifier}, "
                    f"installed {v.installed_version}."
                )
        return "\n".join(lines)


class ModuleVersionChecker:
    """Validates plan ``module_requirements`` against registered module versions.

    Usage::

        checker = ModuleVersionChecker(
            available_versions={"filesystem": "1.3.0", "browser": "0.4.1"}
        )
        report = checker.check(plan)
        if not report.is_compatible:
            raise IMLValidationError(report.format_errors())

    Args:
        available_versions: Mapping of module_id → installed version string.
            The :class:`~llmos_bridge.modules.registry.ModuleRegistry` provides
            this via ``{m: reg.get_manifest(m).version for m in reg.list_available()}``.
    """

    def __init__(self, available_versions: dict[str, str]) -> None:
        self._versions = available_versions

    def check(self, requirements: dict[str, str]) -> CompatibilityReport:
        """Check *requirements* against installed versions.

        Args:
            requirements: Mapping of module_id → PEP-440 specifier string,
                as stored in ``IMLPlan.module_requirements``.

        Returns:
            A :class:`CompatibilityReport`.  Call ``.is_compatible`` to test
            the result and ``.format_errors()`` to produce a human-readable
            message for the LLM or API caller.
        """
        report = CompatibilityReport()

        for module_id, specifier_str in requirements.items():
            installed_raw = self._versions.get(module_id)

            if installed_raw is None:
                report.violations.append(
                    CompatibilityViolation(
                        module_id=module_id,
                        required_specifier=specifier_str,
                        installed_version=None,
                    )
                )
                continue

            # Validate specifier syntax.
            try:
                spec = SpecifierSet(specifier_str, prereleases=True)
            except InvalidSpecifier:
                raise IMLValidationError(
                    f"module_requirements['{module_id}']: invalid version specifier "
                    f"'{specifier_str}'.  Must be a PEP-440 specifier "
                    f"(e.g. '>=1.0.0', '==2.0.0', '>=1.0.0,<2.0.0')."
                )

            # Validate installed version string.
            try:
                installed_version = Version(installed_raw)
            except InvalidVersion:
                raise IMLValidationError(
                    f"Module '{module_id}' has an invalid version string "
                    f"'{installed_raw}' that cannot be parsed as PEP-440."
                )

            if installed_version not in spec:
                report.violations.append(
                    CompatibilityViolation(
                        module_id=module_id,
                        required_specifier=specifier_str,
                        installed_version=installed_raw,
                    )
                )

        return report

    def assert_compatible(self, requirements: dict[str, str]) -> None:
        """Raise :class:`~llmos_bridge.exceptions.IMLValidationError` if any
        constraint is violated.

        This is the convenience method used by the executor's pre-flight check.
        """
        report = self.check(requirements)
        if not report.is_compatible:
            raise IMLValidationError(
                "Plan module_requirements are not satisfied:\n" + report.format_errors()
            )
