"""Tests for hub.resolver — DependencyResolver + topological sort."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from llmos_bridge.hub.resolver import DependencyResolver, ResolutionResult


@dataclass
class FakeConfig:
    """Minimal stand-in for ModulePackageConfig."""
    module_dependencies: dict[str, str] = field(default_factory=dict)
    requirements: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# ResolutionResult
# ---------------------------------------------------------------------------

class TestResolutionResult:
    def test_no_conflicts(self):
        r = ResolutionResult(install_order=["a", "b"])
        assert not r.has_conflicts

    def test_has_conflicts(self):
        r = ResolutionResult(conflicts=["some conflict"])
        assert r.has_conflicts

    def test_defaults(self):
        r = ResolutionResult()
        assert r.install_order == []
        assert r.python_deps == {}
        assert r.conflicts == []


# ---------------------------------------------------------------------------
# DependencyResolver — basic
# ---------------------------------------------------------------------------

class TestDependencyResolverBasic:
    def test_single_module_no_deps(self):
        resolver = DependencyResolver()
        result = resolver.resolve(
            ["a"],
            {"a": FakeConfig(requirements=["numpy"])},
        )
        assert result.install_order == ["a"]
        assert result.python_deps["a"] == ["numpy"]
        assert not result.has_conflicts

    def test_two_independent_modules(self):
        resolver = DependencyResolver()
        result = resolver.resolve(
            ["a", "b"],
            {
                "a": FakeConfig(),
                "b": FakeConfig(),
            },
        )
        assert set(result.install_order) == {"a", "b"}
        assert not result.has_conflicts

    def test_missing_package_config(self):
        resolver = DependencyResolver()
        result = resolver.resolve(["a"], {})
        assert result.has_conflicts
        assert "not found" in result.conflicts[0].lower()


# ---------------------------------------------------------------------------
# DependencyResolver — dependency chains
# ---------------------------------------------------------------------------

class TestDependencyResolverChains:
    def test_linear_dependency(self):
        """A depends on B → install order: B then A."""
        resolver = DependencyResolver()
        result = resolver.resolve(
            ["a", "b"],
            {
                "a": FakeConfig(module_dependencies={"b": ">=1.0"}),
                "b": FakeConfig(),
            },
        )
        assert result.install_order.index("b") < result.install_order.index("a")
        assert not result.has_conflicts

    def test_diamond_dependency(self):
        """A→B, A→C, B→D, C→D → D before B,C before A."""
        resolver = DependencyResolver()
        result = resolver.resolve(
            ["a", "b", "c", "d"],
            {
                "a": FakeConfig(module_dependencies={"b": ">=1.0", "c": ">=1.0"}),
                "b": FakeConfig(module_dependencies={"d": ">=1.0"}),
                "c": FakeConfig(module_dependencies={"d": ">=1.0"}),
                "d": FakeConfig(),
            },
        )
        order = result.install_order
        assert order.index("d") < order.index("b")
        assert order.index("d") < order.index("c")
        assert order.index("b") < order.index("a")
        assert order.index("c") < order.index("a")

    def test_circular_dependency_detected(self):
        resolver = DependencyResolver()
        result = resolver.resolve(
            ["a", "b"],
            {
                "a": FakeConfig(module_dependencies={"b": ">=1.0"}),
                "b": FakeConfig(module_dependencies={"a": ">=1.0"}),
            },
        )
        assert result.has_conflicts
        assert any("circular" in c.lower() for c in result.conflicts)
        # Best-effort: all modules still appear.
        assert set(result.install_order) == {"a", "b"}


# ---------------------------------------------------------------------------
# DependencyResolver — version compatibility
# ---------------------------------------------------------------------------

class TestDependencyResolverVersions:
    def test_satisfied_installed_dependency(self):
        """If dep is already installed with compatible version → no conflict."""
        resolver = DependencyResolver(
            installed_versions={"base_module": "1.5.0"}
        )
        result = resolver.resolve(
            ["a"],
            {"a": FakeConfig(module_dependencies={"base_module": ">=1.0.0"})},
        )
        assert not result.has_conflicts

    def test_incompatible_installed_version(self):
        """If dep is installed but version is incompatible → conflict."""
        resolver = DependencyResolver(
            installed_versions={"base_module": "0.5.0"}
        )
        result = resolver.resolve(
            ["a"],
            {"a": FakeConfig(module_dependencies={"base_module": ">=1.0.0"})},
        )
        assert result.has_conflicts
        assert "base_module" in result.conflicts[0]

    def test_missing_dependency_not_available(self):
        """If dep is not installed and not in available packages → conflict."""
        resolver = DependencyResolver()
        result = resolver.resolve(
            ["a"],
            {"a": FakeConfig(module_dependencies={"missing": ">=1.0.0"})},
        )
        assert result.has_conflicts
        assert "missing" in result.conflicts[0]

    def test_dependency_in_install_set(self):
        """If dep is in the install set → no conflict (will be installed)."""
        resolver = DependencyResolver()
        result = resolver.resolve(
            ["a", "missing"],
            {
                "a": FakeConfig(module_dependencies={"missing": ">=1.0.0"}),
                "missing": FakeConfig(),
            },
        )
        assert not result.has_conflicts


# ---------------------------------------------------------------------------
# Python deps collection
# ---------------------------------------------------------------------------

class TestDependencyResolverPythonDeps:
    def test_python_deps_collected(self):
        resolver = DependencyResolver()
        result = resolver.resolve(
            ["a", "b"],
            {
                "a": FakeConfig(requirements=["numpy>=1.20", "scipy"]),
                "b": FakeConfig(requirements=["requests"]),
            },
        )
        assert result.python_deps["a"] == ["numpy>=1.20", "scipy"]
        assert result.python_deps["b"] == ["requests"]

    def test_empty_python_deps(self):
        resolver = DependencyResolver()
        result = resolver.resolve(
            ["a"],
            {"a": FakeConfig()},
        )
        assert result.python_deps["a"] == []
