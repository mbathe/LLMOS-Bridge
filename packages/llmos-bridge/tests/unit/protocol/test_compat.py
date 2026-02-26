"""Unit tests — ModuleVersionChecker and CompatibilityReport."""

from __future__ import annotations

import pytest

from llmos_bridge.exceptions import IMLValidationError
from llmos_bridge.protocol.compat import (
    CompatibilityReport,
    CompatibilityViolation,
    ModuleVersionChecker,
)


# ---------------------------------------------------------------------------
# CompatibilityReport
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCompatibilityReport:
    def test_empty_report_is_compatible(self) -> None:
        report = CompatibilityReport()
        assert report.is_compatible is True

    def test_report_with_violation_not_compatible(self) -> None:
        report = CompatibilityReport(
            violations=[
                CompatibilityViolation(
                    module_id="filesystem",
                    required_specifier=">=2.0.0",
                    installed_version="1.0.0",
                )
            ]
        )
        assert report.is_compatible is False

    def test_format_errors_missing_module(self) -> None:
        report = CompatibilityReport(
            violations=[
                CompatibilityViolation(
                    module_id="missing_mod",
                    required_specifier=">=1.0.0",
                    installed_version=None,
                )
            ]
        )
        text = report.format_errors()
        assert "missing_mod" in text
        assert "not registered" in text

    def test_format_errors_version_mismatch(self) -> None:
        report = CompatibilityReport(
            violations=[
                CompatibilityViolation(
                    module_id="filesystem",
                    required_specifier=">=2.0.0",
                    installed_version="1.0.0",
                )
            ]
        )
        text = report.format_errors()
        assert "filesystem" in text
        assert ">=2.0.0" in text
        assert "1.0.0" in text


# ---------------------------------------------------------------------------
# ModuleVersionChecker
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestModuleVersionChecker:
    def test_compatible_when_versions_match(self) -> None:
        checker = ModuleVersionChecker(
            available_versions={"filesystem": "1.3.0", "excel": "2.0.0"}
        )
        report = checker.check({"filesystem": ">=1.0.0", "excel": "==2.0.0"})
        assert report.is_compatible is True
        assert report.violations == []

    def test_violation_when_version_too_low(self) -> None:
        checker = ModuleVersionChecker(
            available_versions={"filesystem": "0.9.0"}
        )
        report = checker.check({"filesystem": ">=1.0.0"})
        assert not report.is_compatible
        assert len(report.violations) == 1
        assert report.violations[0].module_id == "filesystem"

    def test_violation_when_module_not_registered(self) -> None:
        checker = ModuleVersionChecker(available_versions={})
        report = checker.check({"filesystem": ">=1.0.0"})
        assert not report.is_compatible
        v = report.violations[0]
        assert v.module_id == "filesystem"
        assert v.installed_version is None

    def test_multiple_violations(self) -> None:
        checker = ModuleVersionChecker(
            available_versions={"filesystem": "0.5.0"}
        )
        report = checker.check(
            {"filesystem": ">=1.0.0", "missing_mod": ">=0.1.0"}
        )
        assert len(report.violations) == 2

    def test_exact_version_match_passes(self) -> None:
        checker = ModuleVersionChecker(
            available_versions={"filesystem": "1.0.0"}
        )
        report = checker.check({"filesystem": "==1.0.0"})
        assert report.is_compatible

    def test_version_range_passes(self) -> None:
        checker = ModuleVersionChecker(
            available_versions={"filesystem": "1.5.0"}
        )
        report = checker.check({"filesystem": ">=1.0.0,<2.0.0"})
        assert report.is_compatible

    def test_version_range_fails_upper_bound(self) -> None:
        checker = ModuleVersionChecker(
            available_versions={"filesystem": "2.1.0"}
        )
        report = checker.check({"filesystem": ">=1.0.0,<2.0.0"})
        assert not report.is_compatible

    def test_invalid_specifier_raises(self) -> None:
        checker = ModuleVersionChecker(
            available_versions={"filesystem": "1.0.0"}
        )
        with pytest.raises(IMLValidationError, match="invalid version specifier"):
            checker.check({"filesystem": "not_a_specifier"})

    def test_assert_compatible_raises_on_violation(self) -> None:
        checker = ModuleVersionChecker(
            available_versions={"filesystem": "0.1.0"}
        )
        with pytest.raises(IMLValidationError, match="not satisfied"):
            checker.assert_compatible({"filesystem": ">=1.0.0"})

    def test_assert_compatible_passes_when_ok(self) -> None:
        checker = ModuleVersionChecker(
            available_versions={"filesystem": "1.5.0"}
        )
        # Should not raise
        checker.assert_compatible({"filesystem": ">=1.0.0"})

    def test_empty_requirements_always_compatible(self) -> None:
        checker = ModuleVersionChecker(available_versions={"filesystem": "1.0.0"})
        report = checker.check({})
        assert report.is_compatible

    def test_prerelease_version_in_spec(self) -> None:
        checker = ModuleVersionChecker(
            available_versions={"mod": "1.0.0a1"}
        )
        report = checker.check({"mod": ">=1.0.0a1"})
        assert report.is_compatible
