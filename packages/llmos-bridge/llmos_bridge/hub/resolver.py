"""Dependency resolver — topological sort of module-to-module + Python deps.

Given a set of modules to install, resolves:
  1. Module-to-module dependencies (from module_dependencies in manifest)
  2. Python package dependencies (from requirements in llmos-module.toml)
  3. Topological ordering for installation

Uses ``ModuleVersionChecker`` (protocol/compat.py) for PEP-440
version constraint checking.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from llmos_bridge.logging import get_logger

log = get_logger(__name__)


@dataclass
class ResolutionResult:
    """Result of dependency resolution."""

    install_order: list[str] = field(default_factory=list)
    python_deps: dict[str, list[str]] = field(default_factory=dict)
    conflicts: list[str] = field(default_factory=list)

    @property
    def has_conflicts(self) -> bool:
        return len(self.conflicts) > 0


class DependencyResolver:
    """Resolve module-to-module and Python dependencies.

    Args:
        installed_versions: dict of module_id → version for already-installed modules.
        available_packages: dict of module_id → ModulePackageConfig for packages
            that can be installed to satisfy dependencies.
    """

    def __init__(
        self,
        installed_versions: dict[str, str] | None = None,
        available_packages: dict[str, Any] | None = None,
    ) -> None:
        self._installed = installed_versions or {}
        self._packages = available_packages or {}

    def resolve(
        self,
        module_ids: list[str],
        package_configs: dict[str, Any],
    ) -> ResolutionResult:
        """Resolve dependencies for the given modules.

        Args:
            module_ids: Module IDs to install.
            package_configs: dict of module_id → ModulePackageConfig with
                ``module_dependencies`` and ``requirements`` fields.

        Returns:
            A ResolutionResult with topologically sorted install_order,
            per-module python_deps, and any conflict descriptions.
        """
        conflicts: list[str] = []
        python_deps: dict[str, list[str]] = {}
        graph: dict[str, set[str]] = {}

        # Build dependency graph.
        for mid in module_ids:
            config = package_configs.get(mid)
            if config is None:
                conflicts.append(f"Package config not found for '{mid}'")
                continue

            graph.setdefault(mid, set())
            deps = getattr(config, "module_dependencies", {})
            reqs = getattr(config, "requirements", [])
            python_deps[mid] = list(reqs)

            for dep_id, version_spec in deps.items():
                graph[mid].add(dep_id)

                # Check if the dependency is already installed with a compatible version.
                installed_ver = self._installed.get(dep_id)
                if installed_ver is not None:
                    if not self._check_version(installed_ver, version_spec):
                        conflicts.append(
                            f"'{mid}' requires '{dep_id}' {version_spec}, "
                            f"but version {installed_ver} is installed"
                        )
                elif dep_id not in module_ids and dep_id not in self._packages:
                    conflicts.append(
                        f"'{mid}' requires '{dep_id}' {version_spec}, "
                        f"but it is not installed and not available"
                    )

        # Topological sort.
        install_order = self._topological_sort(graph, conflicts)

        return ResolutionResult(
            install_order=install_order,
            python_deps=python_deps,
            conflicts=conflicts,
        )

    @staticmethod
    def _check_version(installed_ver: str, version_spec: str) -> bool:
        """Check if installed_ver satisfies the PEP-440 version_spec."""
        from llmos_bridge.protocol.compat import ModuleVersionChecker

        checker = ModuleVersionChecker(
            available_versions={"dep": installed_ver}
        )
        report = checker.check({"dep": version_spec})
        return report.is_compatible

    @staticmethod
    def _topological_sort(
        graph: dict[str, set[str]], conflicts: list[str]
    ) -> list[str]:
        """Kahn's algorithm for topological sorting.

        ``graph[node] = {deps}`` means *node depends on deps*.
        We want to install dependencies first, so the in-degree of a node
        is the number of its dependencies that are also in the graph.
        """
        # Count how many unresolved deps each node has (only those in the graph).
        in_degree: dict[str, int] = {}
        for node in graph:
            count = sum(1 for dep in graph[node] if dep in graph)
            in_degree[node] = count

        # Nodes with no in-graph dependencies are ready immediately.
        queue = sorted(node for node, deg in in_degree.items() if deg == 0)
        result: list[str] = []

        while queue:
            node = queue.pop(0)
            result.append(node)
            # For every other node that depends on `node`, decrement its in-degree.
            for other, deps in graph.items():
                if node in deps:
                    in_degree[other] -= 1
                    if in_degree[other] == 0:
                        queue.append(other)
                        queue.sort()

        if len(result) != len(graph):
            cycle_nodes = set(graph.keys()) - set(result)
            conflicts.append(
                f"Circular dependency detected among: {sorted(cycle_nodes)}"
            )
            # Add remaining nodes anyway (best-effort).
            result.extend(sorted(cycle_nodes))

        return result
