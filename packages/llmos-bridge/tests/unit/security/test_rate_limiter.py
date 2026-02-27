"""Unit tests — ActionRateLimiter (rate_limiter.py).

Tests cover:
  - check returns True/False based on limits
  - check_or_raise raises RateLimitExceededError when exceeded
  - check_or_raise records invocation on success
  - per-hour limit enforcement
  - reset (specific key and global)
  - get_counts accuracy
  - timestamp pruning via time patching
  - independent action keys
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from llmos_bridge.exceptions import RateLimitExceededError
from llmos_bridge.security.rate_limiter import ActionRateLimiter

pytestmark = pytest.mark.unit


class TestActionRateLimiter:

    def test_check_returns_true_when_under_limit(self) -> None:
        limiter = ActionRateLimiter()
        limiter.record("fs.write")
        limiter.record("fs.write")
        assert limiter.check("fs.write", calls_per_minute=5) is True

    def test_check_returns_false_when_over_per_minute_limit(self) -> None:
        limiter = ActionRateLimiter()
        for _ in range(3):
            limiter.record("fs.write")
        assert limiter.check("fs.write", calls_per_minute=3) is False

    def test_check_or_raise_raises_when_exceeded(self) -> None:
        limiter = ActionRateLimiter()
        for _ in range(2):
            limiter.record("api.call")
        with pytest.raises(RateLimitExceededError) as exc_info:
            limiter.check_or_raise("api.call", calls_per_minute=2)
        assert exc_info.value.action_key == "api.call"
        assert exc_info.value.limit == 2
        assert exc_info.value.window == "minute"

    def test_check_or_raise_records_invocation_on_success(self) -> None:
        limiter = ActionRateLimiter()
        limiter.check_or_raise("fs.read", calls_per_minute=10)
        counts = limiter.get_counts("fs.read")
        assert counts["minute"] == 1

    def test_per_hour_limit_works(self) -> None:
        limiter = ActionRateLimiter()
        for _ in range(5):
            limiter.record("slow.action")
        assert limiter.check("slow.action", calls_per_hour=5) is False
        assert limiter.check("slow.action", calls_per_hour=10) is True

    def test_reset_clears_specific_key(self) -> None:
        limiter = ActionRateLimiter()
        limiter.record("a.one")
        limiter.record("a.two")
        limiter.reset("a.one")
        assert limiter.get_counts("a.one") == {"minute": 0, "hour": 0}
        assert limiter.get_counts("a.two")["minute"] == 1

    def test_reset_with_no_args_clears_all(self) -> None:
        limiter = ActionRateLimiter()
        limiter.record("a.one")
        limiter.record("a.two")
        limiter.reset()
        assert limiter.get_counts("a.one") == {"minute": 0, "hour": 0}
        assert limiter.get_counts("a.two") == {"minute": 0, "hour": 0}

    def test_get_counts_returns_correct_values(self) -> None:
        limiter = ActionRateLimiter()
        for _ in range(4):
            limiter.record("fs.write")
        counts = limiter.get_counts("fs.write")
        assert counts["minute"] == 4
        assert counts["hour"] == 4

    def test_old_timestamps_are_pruned(self) -> None:
        """Timestamps older than 1 hour are pruned on check."""
        limiter = ActionRateLimiter()
        old_time = 1000.0
        recent_time = old_time + 3601.0  # 1 hour + 1 second later

        # Inject an old timestamp directly
        limiter._timestamps["fs.write"] = [old_time]

        with patch("llmos_bridge.security.rate_limiter.time") as mock_time:
            mock_time.time.return_value = recent_time
            counts = limiter.get_counts("fs.write")

        # The old timestamp should have been pruned
        assert counts["minute"] == 0
        assert counts["hour"] == 0

    def test_separate_action_keys_are_independent(self) -> None:
        limiter = ActionRateLimiter()
        for _ in range(5):
            limiter.record("module_a.action")
        limiter.record("module_b.action")

        assert limiter.check("module_a.action", calls_per_minute=5) is False
        assert limiter.check("module_b.action", calls_per_minute=5) is True
