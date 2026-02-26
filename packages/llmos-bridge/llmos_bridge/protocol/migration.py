"""IML Protocol — Schema migration registry and backward-compatibility policy.

As the IML protocol evolves, clients and LLMs may send plans written against
older protocol versions.  This module provides:

1. **IMLMigration** — a callable that transforms a raw plan dict from one
   protocol version to a newer one.

2. **MigrationRegistry** — a registry of all known migrations, indexed by
   (from_version, to_version) tuples.  The registry can compute a migration
   path between any two versions and apply it automatically.

3. **MigrationPipeline** — the public entry point that accepts a raw plan dict
   (or JSON string), detects its protocol version, and upgrades it to the
   current version using the shortest available migration path.

Versioning policy:
  - LLMOS Bridge follows a *rolling upgrade* policy: a server running protocol
    v2.x will accept v1.x plans and silently upgrade them.  It will never
    accept plans from a *newer* version (forward compatibility is not
    guaranteed).
  - Each migration is a pure function: dict → dict.  Migrations must not
    mutate their input; they should return a new dict.
  - Migrations are idempotent within their target version.
  - Breaking changes require a major version bump AND a registered migration.

Current migrations:
  - 1.0 → 2.0: Rename ``steps`` array to ``actions``, populate default
    ``on_error`` and ``timeout`` per step.

Adding a new migration:
  1. Implement a function matching the ``MigrationFn`` signature.
  2. Register it with ``registry.register(from_ver, to_ver, fn)``.
  3. Add tests in ``tests/unit/protocol/test_migration.py``.
"""

from __future__ import annotations

import copy
import json
from collections import defaultdict
from typing import Any, Callable

from llmos_bridge.exceptions import IMLParseError, ProtocolError
from llmos_bridge.protocol.constants import PROTOCOL_VERSION

# Type alias for a migration function.
MigrationFn = Callable[[dict[str, Any]], dict[str, Any]]


# ---------------------------------------------------------------------------
# Concrete migration: v1.0 → v2.0
# ---------------------------------------------------------------------------


def _migrate_v1_to_v2(plan: dict[str, Any]) -> dict[str, Any]:
    """Migrate an IML v1.0 plan to the v2.0 schema.

    Changes from v1 → v2:
      - ``steps`` array renamed to ``actions``
      - Each step gains ``on_error`` (default "abort") if missing
      - Each step gains ``timeout`` (default 60) if missing
      - Each step ``params`` moved from positional list to dict if it was a list
      - ``protocol_version`` updated to "2.0"
    """
    result = copy.deepcopy(plan)
    result["protocol_version"] = "2.0"

    # Rename steps → actions.
    steps = result.pop("steps", result.pop("actions", []))
    actions: list[dict[str, Any]] = []

    for i, step in enumerate(steps):
        action = dict(step)

        # Ensure required fields exist.
        if "id" not in action:
            action["id"] = f"step_{i + 1}"
        if "on_error" not in action:
            action["on_error"] = "abort"
        if "timeout" not in action:
            action["timeout"] = 60

        # Normalise params from list to dict if needed (v1 used positional lists
        # for simple modules).
        params = action.get("params", {})
        if isinstance(params, list):
            action["params"] = {f"arg_{j}": v for j, v in enumerate(params)}

        # v1 used 'type' + 'name' instead of 'module' + 'action'.
        if "type" in action and "module" not in action:
            action["module"] = action.pop("type")
        if "name" in action and "action" not in action:
            action["action"] = action.pop("name")

        actions.append(action)

    result["actions"] = actions
    return result


# ---------------------------------------------------------------------------
# Migration registry
# ---------------------------------------------------------------------------


class MigrationRegistry:
    """Registry of IML schema migrations.

    A migration is identified by a (from_version, to_version) tuple.
    The registry can compute a path between any two registered versions
    using BFS over the version graph.
    """

    def __init__(self) -> None:
        # _graph[from_ver] = list of (to_ver, migration_fn)
        self._graph: dict[str, list[tuple[str, MigrationFn]]] = defaultdict(list)

    def register(self, from_version: str, to_version: str, fn: MigrationFn) -> None:
        """Register a migration function.

        Args:
            from_version: Source protocol version string (e.g. "1.0").
            to_version:   Target protocol version string (e.g. "2.0").
            fn:           Pure function that transforms a raw plan dict.
        """
        self._graph[from_version].append((to_version, fn))

    def find_path(
        self, from_version: str, to_version: str
    ) -> list[tuple[str, MigrationFn]] | None:
        """Find a migration path from *from_version* to *to_version*.

        Uses BFS so it always finds the shortest path (fewest migrations).

        Returns:
            Ordered list of (to_version, migration_fn) steps, or ``None`` if no
            path exists.
        """
        if from_version == to_version:
            return []

        visited: set[str] = {from_version}
        # Queue items: (current_version, path_so_far)
        queue: list[tuple[str, list[tuple[str, MigrationFn]]]] = [
            (from_version, [])
        ]

        while queue:
            current, path = queue.pop(0)
            for next_ver, fn in self._graph.get(current, []):
                if next_ver in visited:
                    continue
                new_path = path + [(next_ver, fn)]
                if next_ver == to_version:
                    return new_path
                visited.add(next_ver)
                queue.append((next_ver, new_path))

        return None


# ---------------------------------------------------------------------------
# Public migration pipeline
# ---------------------------------------------------------------------------


class MigrationPipeline:
    """Upgrades raw plan dicts to the current protocol version.

    Usage::

        pipeline = MigrationPipeline()
        # raw can be a dict or a JSON string.
        upgraded = pipeline.upgrade(raw)
        plan = IMLParser().parse(upgraded)

    The pipeline is idempotent for plans already at the current version.
    """

    def __init__(self, registry: MigrationRegistry | None = None) -> None:
        self._registry = registry or _build_default_registry()

    def upgrade(self, raw: str | bytes | dict[str, Any]) -> dict[str, Any]:
        """Parse *raw* and migrate it to ``PROTOCOL_VERSION``.

        Args:
            raw: A JSON string, bytes, or already-parsed dict representing an
                 IML plan.

        Returns:
            A new dict with ``protocol_version`` == ``PROTOCOL_VERSION``.

        Raises:
            IMLParseError: If *raw* cannot be decoded as JSON.
            ProtocolError: If no migration path exists to the current version.
        """
        if isinstance(raw, (str, bytes)):
            try:
                plan = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise IMLParseError(
                    f"Cannot decode JSON for migration: {exc}"
                ) from exc
        else:
            plan = dict(raw)

        if not isinstance(plan, dict):
            raise IMLParseError("Plan must be a JSON object (dict at the top level).")

        detected_version = str(plan.get("protocol_version", "1.0"))

        if detected_version == PROTOCOL_VERSION:
            return plan

        path = self._registry.find_path(detected_version, PROTOCOL_VERSION)
        if path is None:
            raise ProtocolError(
                f"No migration path from protocol_version '{detected_version}' "
                f"to '{PROTOCOL_VERSION}'.  Supported source versions: "
                f"{sorted(self._registry._graph.keys())}"
            )

        current = plan
        for to_ver, fn in path:
            current = fn(current)

        return current


# ---------------------------------------------------------------------------
# Default registry bootstrap
# ---------------------------------------------------------------------------


def _build_default_registry() -> MigrationRegistry:
    """Build the registry pre-populated with all shipped migrations."""
    registry = MigrationRegistry()
    registry.register("1.0", "2.0", _migrate_v1_to_v2)
    return registry


# Module-level default instance for convenience.
default_pipeline = MigrationPipeline()
