"""Security layer — Per-action sliding window rate limiter.

In-memory rate limiter keyed by ``module_id.action_name``.  Timestamps
older than 1 hour are pruned on each check to prevent unbounded growth.

Usage::

    limiter = ActionRateLimiter()
    limiter.check_or_raise("filesystem.write_file", calls_per_minute=30)
    limiter.check_or_raise("os_exec.run_command", calls_per_minute=10, calls_per_hour=100)
"""

from __future__ import annotations

import time

from llmos_bridge.exceptions import RateLimitExceededError
from llmos_bridge.logging import get_logger

log = get_logger(__name__)

# Prune timestamps older than this (1 hour)
_PRUNE_WINDOW = 3600.0


class ActionRateLimiter:
    """Sliding-window rate limiter for action execution."""

    def __init__(self) -> None:
        self._timestamps: dict[str, list[float]] = {}

    def check(
        self,
        action_key: str,
        *,
        calls_per_minute: int | None = None,
        calls_per_hour: int | None = None,
    ) -> bool:
        """Return True if the action is within its rate limits."""
        now = time.time()
        self._prune(action_key, now)
        timestamps = self._timestamps.get(action_key, [])

        if calls_per_minute is not None:
            cutoff = now - 60.0
            recent = sum(1 for t in timestamps if t > cutoff)
            if recent >= calls_per_minute:
                return False

        if calls_per_hour is not None:
            cutoff = now - 3600.0
            recent = sum(1 for t in timestamps if t > cutoff)
            if recent >= calls_per_hour:
                return False

        return True

    def check_or_raise(
        self,
        action_key: str,
        *,
        calls_per_minute: int | None = None,
        calls_per_hour: int | None = None,
    ) -> None:
        """Check rate limits; raise :class:`RateLimitExceededError` if exceeded."""
        now = time.time()
        self._prune(action_key, now)
        timestamps = self._timestamps.get(action_key, [])

        if calls_per_minute is not None:
            cutoff = now - 60.0
            recent = sum(1 for t in timestamps if t > cutoff)
            if recent >= calls_per_minute:
                raise RateLimitExceededError(
                    action_key=action_key,
                    limit=calls_per_minute,
                    window="minute",
                )

        if calls_per_hour is not None:
            cutoff = now - 3600.0
            recent = sum(1 for t in timestamps if t > cutoff)
            if recent >= calls_per_hour:
                raise RateLimitExceededError(
                    action_key=action_key,
                    limit=calls_per_hour,
                    window="hour",
                )

        # Record the invocation
        self._record(action_key, now)

    def record(self, action_key: str) -> None:
        """Record an action invocation without checking limits."""
        self._record(action_key, time.time())

    def reset(self, action_key: str | None = None) -> None:
        """Reset rate limit state.

        If *action_key* is provided, only that key is reset.
        Otherwise all keys are cleared.
        """
        if action_key is None:
            self._timestamps.clear()
        else:
            self._timestamps.pop(action_key, None)

    def get_counts(
        self, action_key: str
    ) -> dict[str, int]:
        """Return current counts for an action key (minute / hour)."""
        now = time.time()
        self._prune(action_key, now)
        timestamps = self._timestamps.get(action_key, [])
        return {
            "minute": sum(1 for t in timestamps if t > now - 60.0),
            "hour": sum(1 for t in timestamps if t > now - 3600.0),
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _record(self, action_key: str, now: float) -> None:
        if action_key not in self._timestamps:
            self._timestamps[action_key] = []
        self._timestamps[action_key].append(now)

    def _prune(self, action_key: str, now: float) -> None:
        """Remove timestamps older than the prune window."""
        if action_key not in self._timestamps:
            return
        cutoff = now - _PRUNE_WINDOW
        self._timestamps[action_key] = [
            t for t in self._timestamps[action_key] if t > cutoff
        ]
