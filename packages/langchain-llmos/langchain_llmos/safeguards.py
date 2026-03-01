"""Safety rails for autonomous agents.

Prevents the agent from accidentally closing critical applications,
executing dangerous key combinations, or getting stuck in failure loops.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SafeguardConfig:
    """Configuration for agent safety rails.

    Attributes:
        protected_windows:  Regex patterns for window titles that must never
                            be closed or minimized by the agent.
        max_consecutive_failures:  Stop the agent after N consecutive failed
                                   actions to prevent infinite loops.
        dangerous_hotkeys:  Key combinations that are blocked outright.
    """

    protected_windows: list[str] = field(default_factory=lambda: [
        r"(?i)visual\s*studio\s*code",
        r"(?i)code\s*-\s*oss",
        r"(?i)terminal",
        r"(?i)konsole",
        r"(?i)gnome-terminal",
        r"(?i)xterm",
        r"(?i)tilix",
        r"(?i)alacritty",
        r"(?i)kitty",
    ])

    max_consecutive_failures: int = 3

    dangerous_hotkeys: list[list[str]] = field(default_factory=lambda: [
        ["alt", "f4"],      # Close window — too risky for autonomous agents
        ["ctrl", "alt", "delete"],
    ])

    def is_hotkey_blocked(self, keys: list[str]) -> str | None:
        """Return a reason string if *keys* match a dangerous hotkey, else None."""
        normalised = sorted(k.lower() for k in keys)
        for blocked in self.dangerous_hotkeys:
            if sorted(k.lower() for k in blocked) == normalised:
                return f"Hotkey {'+'.join(keys)} is blocked by safeguards"
        return None

    def validate_plan_steps(
        self, steps: list[dict[str, Any]]
    ) -> list[str]:
        """Validate plan steps against safeguards. Returns list of warnings."""
        warnings: list[str] = []
        for step in steps:
            action = step.get("action", "")
            params = step.get("params", {})

            # Check for blocked hotkeys.
            if action.endswith("__key_press") or action == "key_press":
                keys = params.get("keys", [])
                reason = self.is_hotkey_blocked(keys)
                if reason:
                    warnings.append(
                        f"Step '{step.get('id', '?')}': {reason}"
                    )

        return warnings
