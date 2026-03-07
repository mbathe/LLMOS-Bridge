"""Sandbox policy enforcement for isolated community modules.

Cooperative enforcement — the proxy checks action parameters against sandbox
constraints BEFORE dispatching to the subprocess worker.  This is NOT OS-level
sandboxing (seccomp, namespaces); the source code scanner already catches
dangerous patterns at install time.  The sandbox enforcer is a runtime
safety net that prevents actions that violate the module's declared sandbox
level.

Three levels::

    strict  — Read-only filesystem, no network, no shell commands.
    basic   — Controlled write paths (install dir + /tmp), no shell=True.
    none    — No restrictions (official / system modules only).
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SandboxLevel(str, Enum):
    """Sandbox restriction level for community modules."""

    STRICT = "strict"
    BASIC = "basic"
    NONE = "none"


@dataclass(frozen=True)
class SandboxPolicy:
    """Immutable sandbox policy applied to a module."""

    level: SandboxLevel
    allowed_write_paths: frozenset[str] = field(default_factory=frozenset)
    allow_network: bool = False
    allow_shell: bool = False
    max_timeout: float = 60.0


# Actions known to perform filesystem writes.
_WRITE_ACTIONS: frozenset[str] = frozenset({
    "write_file", "create_file", "append_file", "copy_file", "move_file",
    "delete_file", "create_directory", "delete_directory",
    "archive_directory", "write_cell", "save_workbook",
    "add_slide", "save_presentation", "write_document", "save_document",
})

# Actions that spawn shell commands.
_SHELL_ACTIONS: frozenset[str] = frozenset({
    "run_command", "execute_script",
})

# Actions involving network access.
_NETWORK_ACTIONS: frozenset[str] = frozenset({
    "http_request", "http_get", "http_post", "http_put", "http_delete",
    "send_email", "fetch_url", "browse_url", "navigate",
    "execute_query", "connect",
})


class SandboxEnforcer:
    """Checks action parameters against a SandboxPolicy.

    Usage::

        policy = SandboxEnforcer.for_level("strict")
        violations = SandboxEnforcer.check_action(policy, "my_mod", "write_file", params)
        if violations:
            raise PermissionDeniedError(...)
    """

    # Default policies per level.
    _LEVEL_DEFAULTS: dict[SandboxLevel, dict[str, Any]] = {
        SandboxLevel.STRICT: {
            "allow_network": False,
            "allow_shell": False,
            "max_timeout": 30.0,
        },
        SandboxLevel.BASIC: {
            "allow_network": True,
            "allow_shell": False,
            "max_timeout": 60.0,
        },
        SandboxLevel.NONE: {
            "allow_network": True,
            "allow_shell": True,
            "max_timeout": 300.0,
        },
    }

    @classmethod
    def for_level(cls, level: str, *, install_path: str = "") -> SandboxPolicy:
        """Create a SandboxPolicy for the given level string.

        Args:
            level: One of "strict", "basic", "none".
            install_path: Module install directory (allowed for writes in basic mode).
        """
        try:
            sandbox_level = SandboxLevel(level)
        except ValueError:
            sandbox_level = SandboxLevel.BASIC  # Safe default

        defaults = cls._LEVEL_DEFAULTS[sandbox_level]

        # Compute allowed write paths.
        if sandbox_level == SandboxLevel.NONE:
            write_paths: frozenset[str] = frozenset({"*"})
        elif sandbox_level == SandboxLevel.BASIC:
            paths = {"/tmp/*", "/var/tmp/*"}
            if install_path:
                paths.add(f"{install_path}/*")
            write_paths = frozenset(paths)
        else:
            write_paths = frozenset()  # strict: no writes

        return SandboxPolicy(
            level=sandbox_level,
            allowed_write_paths=write_paths,
            allow_network=defaults["allow_network"],
            allow_shell=defaults["allow_shell"],
            max_timeout=defaults["max_timeout"],
        )

    @classmethod
    def check_action(
        cls,
        policy: SandboxPolicy,
        module_id: str,
        action: str,
        params: dict[str, Any],
    ) -> list[str]:
        """Check an action against the sandbox policy.

        Returns:
            List of violation descriptions. Empty list means the action is allowed.
        """
        if policy.level == SandboxLevel.NONE:
            return []  # No restrictions.

        violations: list[str] = []

        # Check filesystem writes.
        if action in _WRITE_ACTIONS or action.endswith("_write") or action.endswith("_delete"):
            if policy.level == SandboxLevel.STRICT:
                violations.append(
                    f"Action '{action}' performs filesystem writes (sandbox: strict)"
                )
            else:
                # basic: check path against allowed patterns.
                target_path = params.get("path") or params.get("file_path") or params.get("dest")
                if target_path and isinstance(target_path, str):
                    if not cls._path_allowed(target_path, policy.allowed_write_paths):
                        violations.append(
                            f"Write path '{target_path}' not in allowed paths (sandbox: {policy.level.value})"
                        )

        # Check shell execution.
        if action in _SHELL_ACTIONS:
            if not policy.allow_shell:
                violations.append(
                    f"Action '{action}' executes shell commands (sandbox: {policy.level.value})"
                )
            # Even in basic, reject shell=True in params.
            if params.get("shell") is True:
                violations.append(
                    f"shell=True not allowed (sandbox: {policy.level.value})"
                )

        # Check network access.
        if action in _NETWORK_ACTIONS:
            if not policy.allow_network:
                violations.append(
                    f"Action '{action}' requires network access (sandbox: {policy.level.value})"
                )

        return violations

    @staticmethod
    def _path_allowed(path: str, allowed_patterns: frozenset[str]) -> bool:
        """Check if a path matches any of the allowed glob patterns."""
        for pattern in allowed_patterns:
            if pattern == "*":
                return True
            if fnmatch.fnmatch(path, pattern):
                return True
        return False
