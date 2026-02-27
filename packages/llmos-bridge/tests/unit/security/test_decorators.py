"""Unit tests — Security decorators (decorators.py).

Tests cover:
  - Metadata storage for each decorator
  - __name__ preservation via functools.wraps
  - Stacking preserves all metadata
  - Graceful degradation when self._security is None
  - Runtime enforcement with mock _security
  - collect_security_metadata helper
  - _safe_summary helper
  - _copy_metadata helper
  - Error propagation from permission_manager and rate_limiter
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from llmos_bridge.exceptions import PermissionNotGrantedError, RateLimitExceededError
from llmos_bridge.security.decorators import (
    _copy_metadata,
    _safe_summary,
    audit_trail,
    collect_security_metadata,
    data_classification,
    intent_verified,
    rate_limited,
    requires_permission,
    sensitive_action,
)
from llmos_bridge.security.models import DataClassification, RiskLevel

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Mock module helper
# ---------------------------------------------------------------------------


class MockModule:
    """Minimal stand-in for BaseModule with optional _security."""

    MODULE_ID = "test_module"

    def __init__(self, security: object | None = None) -> None:
        self._security = security


def _make_security() -> MagicMock:
    """Build a mock SecurityManager with the attributes decorators access."""
    security = MagicMock()
    security.permission_manager = MagicMock()
    security.permission_manager.check_or_raise = AsyncMock()
    security.rate_limiter = MagicMock()
    security.rate_limiter.check_or_raise = MagicMock()
    security.audit = MagicMock()
    security.audit.bus = MagicMock()
    security.audit.bus.emit = AsyncMock()
    return security


# ===================================================================
# 1. Metadata tests — each decorator stores correct attributes (6)
# ===================================================================


class TestMetadataStorage:

    def test_requires_permission_stores_metadata(self) -> None:
        @requires_permission("filesystem.write", "filesystem.delete", reason="test reason")
        async def _action_write(self, params):
            pass

        assert _action_write._required_permissions == ["filesystem.write", "filesystem.delete"]
        assert _action_write._permission_reason == "test reason"

    def test_sensitive_action_stores_metadata(self) -> None:
        @sensitive_action(RiskLevel.CRITICAL, requires_confirmation=False, irreversible=True)
        async def _action_destroy(self, params):
            pass

        assert _action_destroy._sensitive_action is True
        assert _action_destroy._risk_level == RiskLevel.CRITICAL
        assert _action_destroy._requires_confirmation is False
        assert _action_destroy._irreversible is True

    def test_rate_limited_stores_metadata(self) -> None:
        @rate_limited(calls_per_minute=10, calls_per_hour=200)
        async def _action_call_api(self, params):
            pass

        assert _action_call_api._rate_limit == {
            "calls_per_minute": 10,
            "calls_per_hour": 200,
        }

    def test_audit_trail_stores_metadata(self) -> None:
        @audit_trail("detailed")
        async def _action_read(self, params):
            pass

        assert _action_read._audit_level == "detailed"

    def test_data_classification_stores_metadata(self) -> None:
        @data_classification(DataClassification.CONFIDENTIAL)
        async def _action_read_secret(self, params):
            pass

        assert _action_read_secret._data_classification == DataClassification.CONFIDENTIAL

    def test_intent_verified_stores_metadata(self) -> None:
        @intent_verified(strict=True)
        async def _action_rm(self, params):
            pass

        assert _action_rm._intent_verified is True
        assert _action_rm._intent_strict is True


# ===================================================================
# 2. __name__ preservation (1)
# ===================================================================


class TestNamePreservation:

    def test_functools_wraps_preserves_name(self) -> None:
        @requires_permission("filesystem.write", reason="w")
        async def _action_write_file(self, params):
            pass

        assert _action_write_file.__name__ == "_action_write_file"


# ===================================================================
# 3. Stacking preserves all metadata (2)
# ===================================================================


class TestStacking:

    def test_two_decorators_preserve_both_metadata(self) -> None:
        @requires_permission("filesystem.write", reason="writes")
        @audit_trail("standard")
        async def _action_write(self, params):
            return {"ok": True}

        # outer decorator metadata
        assert _action_write._required_permissions == ["filesystem.write"]
        assert _action_write._permission_reason == "writes"
        # inner decorator metadata copied outward
        assert _action_write._audit_level == "standard"

    def test_four_decorators_preserve_all_metadata(self) -> None:
        @requires_permission("filesystem.write", reason="w")
        @sensitive_action(RiskLevel.HIGH, irreversible=True)
        @rate_limited(calls_per_minute=5)
        @audit_trail("detailed")
        async def _action_nuke(self, params):
            return None

        assert _action_nuke._required_permissions == ["filesystem.write"]
        assert _action_nuke._permission_reason == "w"
        assert _action_nuke._sensitive_action is True
        assert _action_nuke._risk_level == RiskLevel.HIGH
        assert _action_nuke._irreversible is True
        assert _action_nuke._rate_limit == {"calls_per_minute": 5, "calls_per_hour": None}
        assert _action_nuke._audit_level == "detailed"
        assert _action_nuke.__name__ == "_action_nuke"


# ===================================================================
# 4. Graceful degradation — no _security (3)
# ===================================================================


class TestGracefulDegradation:

    @pytest.mark.asyncio
    async def test_requires_permission_passes_through(self) -> None:
        mod = MockModule(security=None)

        @requires_permission("filesystem.write", reason="w")
        async def _action_write(self, params):
            return {"written": True}

        result = await _action_write(mod, {"path": "/tmp/x"})
        assert result == {"written": True}

    @pytest.mark.asyncio
    async def test_rate_limited_passes_through(self) -> None:
        mod = MockModule(security=None)

        @rate_limited(calls_per_minute=1)
        async def _action_call(self, params):
            return "ok"

        result = await _action_call(mod, {})
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_audit_trail_passes_through(self) -> None:
        mod = MockModule(security=None)

        @audit_trail("detailed")
        async def _action_read(self, params):
            return {"data": 42}

        result = await _action_read(mod, {})
        assert result == {"data": 42}


# ===================================================================
# 5. Runtime with mock _security (5)
# ===================================================================


class TestRuntimeEnforcement:

    @pytest.mark.asyncio
    async def test_requires_permission_calls_check_or_raise(self) -> None:
        security = _make_security()
        mod = MockModule(security=security)

        @requires_permission("filesystem.write", reason="w")
        async def _action_write_file(self, params):
            return {"ok": True}

        result = await _action_write_file(mod, {"path": "/tmp/x"})
        assert result == {"ok": True}
        security.permission_manager.check_or_raise.assert_awaited_once_with(
            "filesystem.write", "test_module", action="write_file"
        )

    @pytest.mark.asyncio
    async def test_sensitive_action_emits_audit_event(self) -> None:
        security = _make_security()
        mod = MockModule(security=security)

        @sensitive_action(RiskLevel.HIGH, irreversible=True)
        async def _action_delete(self, params):
            return {"deleted": True}

        result = await _action_delete(mod, {})
        assert result == {"deleted": True}
        security.audit.bus.emit.assert_awaited_once()
        call_args = security.audit.bus.emit.call_args
        assert call_args[0][0] == "llmos.security"
        payload = call_args[0][1]
        assert payload["event"] == "sensitive_action_invoked"
        assert payload["risk_level"] == "high"
        assert payload["irreversible"] is True

    @pytest.mark.asyncio
    async def test_rate_limited_calls_check_or_raise(self) -> None:
        security = _make_security()
        mod = MockModule(security=security)

        @rate_limited(calls_per_minute=10, calls_per_hour=100)
        async def _action_api_call(self, params):
            return "done"

        result = await _action_api_call(mod, {})
        assert result == "done"
        security.rate_limiter.check_or_raise.assert_called_once_with(
            "test_module.api_call",
            calls_per_minute=10,
            calls_per_hour=100,
        )

    @pytest.mark.asyncio
    async def test_audit_trail_emits_before_and_after(self) -> None:
        security = _make_security()
        mod = MockModule(security=security)

        @audit_trail("standard")
        async def _action_read(self, params):
            return {"content": "hello"}

        result = await _action_read(mod, {})
        assert result == {"content": "hello"}
        assert security.audit.bus.emit.await_count == 2
        before_call = security.audit.bus.emit.call_args_list[0]
        assert before_call[0][1]["event"] == "audit_action_before"
        after_call = security.audit.bus.emit.call_args_list[1]
        assert after_call[0][1]["event"] == "audit_action_after"
        assert after_call[0][1]["success"] is True

    @pytest.mark.asyncio
    async def test_audit_trail_detailed_includes_params_and_result(self) -> None:
        security = _make_security()
        mod = MockModule(security=security)

        @audit_trail("detailed")
        async def _action_read(self, params):
            return {"value": 99}

        await _action_read(mod, {"key": "abc"})
        before_payload = security.audit.bus.emit.call_args_list[0][0][1]
        assert "params" in before_payload
        after_payload = security.audit.bus.emit.call_args_list[1][0][1]
        assert "result_summary" in after_payload


# ===================================================================
# 6. collect_security_metadata (3)
# ===================================================================


class TestCollectSecurityMetadata:

    def test_single_decorator(self) -> None:
        @requires_permission("filesystem.read", reason="r")
        async def _action_read(self, params):
            pass

        meta = collect_security_metadata(_action_read)
        assert meta["permissions"] == ["filesystem.read"]
        assert meta["permission_reason"] == "r"
        assert "risk_level" not in meta

    def test_multiple_decorators(self) -> None:
        @requires_permission("filesystem.write", reason="w")
        @sensitive_action(RiskLevel.HIGH, irreversible=True)
        @rate_limited(calls_per_minute=30)
        @data_classification(DataClassification.RESTRICTED)
        @intent_verified(strict=True)
        async def _action_complex(self, params):
            pass

        meta = collect_security_metadata(_action_complex)
        assert meta["permissions"] == ["filesystem.write"]
        assert meta["risk_level"] == "high"
        assert meta["irreversible"] is True
        assert meta["requires_confirmation"] is True
        assert meta["rate_limit"] == {"calls_per_minute": 30, "calls_per_hour": None}
        assert meta["data_classification"] == "restricted"
        assert meta["intent_verified"] is True
        assert meta["intent_strict"] is True

    def test_no_decorators_returns_empty(self) -> None:
        async def _action_plain(self, params):
            pass

        meta = collect_security_metadata(_action_plain)
        assert meta == {}


# ===================================================================
# 7. _safe_summary helper (2)
# ===================================================================


class TestSafeSummary:

    def test_truncates_long_string(self) -> None:
        long_str = "a" * 300
        result = _safe_summary(long_str, max_len=200)
        assert result.endswith("...")
        assert len(result) == 203  # 200 chars + "..."

    def test_handles_nested_structures(self) -> None:
        data = {"key": [1, "hello", True, None, 5.5]}
        result = _safe_summary(data)
        assert result == {"key": [1, "hello", True, None, 5.5]}


# ===================================================================
# 8. _copy_metadata helper (1)
# ===================================================================


class TestCopyMetadata:

    def test_copies_existing_attributes(self) -> None:
        class Source:
            _required_permissions = ["x"]
            _audit_level = "detailed"

        class Target:
            pass

        _copy_metadata(Source, Target)
        assert Target._required_permissions == ["x"]
        assert Target._audit_level == "detailed"
        # Attributes not set on source should not appear on target
        assert not hasattr(Target, "_rate_limit")


# ===================================================================
# 9. requires_permission raises on check failure (1)
# ===================================================================


class TestPermissionDenied:

    @pytest.mark.asyncio
    async def test_requires_permission_raises_when_check_fails(self) -> None:
        security = _make_security()
        security.permission_manager.check_or_raise = AsyncMock(
            side_effect=PermissionNotGrantedError(
                permission="filesystem.write",
                module_id="test_module",
                action="write_file",
            )
        )
        mod = MockModule(security=security)

        @requires_permission("filesystem.write", reason="w")
        async def _action_write_file(self, params):
            return {"ok": True}

        with pytest.raises(PermissionNotGrantedError):
            await _action_write_file(mod, {})


# ===================================================================
# 10. rate_limited raises on limiter failure (1)
# ===================================================================


class TestRateLimitRaise:

    @pytest.mark.asyncio
    async def test_rate_limited_raises_when_limiter_raises(self) -> None:
        security = _make_security()
        security.rate_limiter.check_or_raise = MagicMock(
            side_effect=RateLimitExceededError(
                action_key="test_module.call",
                limit=5,
                window="minute",
            )
        )
        mod = MockModule(security=security)

        @rate_limited(calls_per_minute=5)
        async def _action_call(self, params):
            return "ok"

        with pytest.raises(RateLimitExceededError):
            await _action_call(mod, {})
