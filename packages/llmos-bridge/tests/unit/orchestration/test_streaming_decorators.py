"""Unit tests — @streams_progress decorator and streaming metadata.

Tests the metadata-only decorator and the introspection helper.
"""

from __future__ import annotations

import pytest

from llmos_bridge.orchestration.streaming_decorators import (
    _STREAMING_ATTRS,
    _copy_streaming_metadata,
    collect_streaming_metadata,
    streams_progress,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


async def _plain_action(self, params):
    return {"ok": True}


@streams_progress
async def _streaming_action(self, params):
    return {"ok": True}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStreamsProgressDecorator:
    def test_sets_attribute(self) -> None:
        assert hasattr(_streaming_action, "_streams_progress")
        assert _streaming_action._streams_progress is True

    def test_does_not_wrap_function(self) -> None:
        """@streams_progress should NOT create a wrapper — it sets an attr directly."""
        assert _streaming_action.__name__ == "_streaming_action"

    def test_plain_function_has_no_attr(self) -> None:
        assert not hasattr(_plain_action, "_streams_progress")


@pytest.mark.unit
class TestCollectStreamingMetadata:
    def test_decorated_returns_metadata(self) -> None:
        meta = collect_streaming_metadata(_streaming_action)
        assert meta == {"streams_progress": True}

    def test_undecorated_returns_empty(self) -> None:
        meta = collect_streaming_metadata(_plain_action)
        assert meta == {}

    def test_false_attr_returns_empty(self) -> None:
        async def fn(self, params):
            pass

        fn._streams_progress = False  # type: ignore
        meta = collect_streaming_metadata(fn)
        assert meta == {}


@pytest.mark.unit
class TestCopyStreamingMetadata:
    def test_copies_attr(self) -> None:
        source = _streaming_action
        target = _plain_action
        _copy_streaming_metadata(source, target)
        assert getattr(target, "_streams_progress", False) is True
        # Clean up.
        delattr(target, "_streams_progress")

    def test_noop_when_no_attr(self) -> None:
        async def source(self, params):
            pass

        async def target(self, params):
            pass

        _copy_streaming_metadata(source, target)
        assert not hasattr(target, "_streams_progress")


@pytest.mark.unit
class TestStreamingAttrs:
    def test_contains_streams_progress(self) -> None:
        assert "_streams_progress" in _STREAMING_ATTRS


@pytest.mark.unit
class TestSecurityDecoratorStacking:
    """Verify that security decorators preserve _streams_progress."""

    def test_requires_permission_preserves_streaming(self) -> None:
        from llmos_bridge.security.decorators import requires_permission

        @requires_permission("filesystem.read")
        @streams_progress
        async def _action_read(self, params):
            return {}

        assert getattr(_action_read, "_streams_progress", False) is True
        meta = collect_streaming_metadata(_action_read)
        assert meta == {"streams_progress": True}

    def test_audit_trail_preserves_streaming(self) -> None:
        from llmos_bridge.security.decorators import audit_trail

        @audit_trail("standard")
        @streams_progress
        async def _action_write(self, params):
            return {}

        assert getattr(_action_write, "_streams_progress", False) is True

    def test_sensitive_action_preserves_streaming(self) -> None:
        from llmos_bridge.security.decorators import sensitive_action
        from llmos_bridge.security.models import RiskLevel

        @sensitive_action(RiskLevel.MEDIUM)
        @streams_progress
        async def _action_delete(self, params):
            return {}

        assert getattr(_action_delete, "_streams_progress", False) is True
