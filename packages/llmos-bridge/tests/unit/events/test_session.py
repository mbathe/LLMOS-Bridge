"""Unit tests — events/session.py (SessionContextPropagator)."""

from __future__ import annotations

import pytest

from llmos_bridge.events.session import SessionContextPropagator


@pytest.mark.unit
class TestSessionContextPropagator:
    async def test_bind_and_get(self) -> None:
        p = SessionContextPropagator()
        ctx = {"trigger_id": "t1", "event_type": "filesystem.changed", "fired_at": 1234.0}
        await p.bind("plan_xyz", ctx)
        result = p.get("plan_xyz")
        assert result is not None
        assert result["trigger_id"] == "t1"

    async def test_get_returns_none_for_unknown_plan(self) -> None:
        p = SessionContextPropagator()
        assert p.get("unknown") is None

    async def test_unbind_removes_context(self) -> None:
        p = SessionContextPropagator()
        await p.bind("plan_abc", {"key": "value"})
        await p.unbind("plan_abc")
        assert p.get("plan_abc") is None

    async def test_unbind_nonexistent_is_noop(self) -> None:
        p = SessionContextPropagator()
        await p.unbind("nonexistent")  # should not raise

    async def test_active_count(self) -> None:
        p = SessionContextPropagator()
        assert p.active_count == 0
        await p.bind("plan_1", {})
        await p.bind("plan_2", {})
        assert p.active_count == 2
        await p.unbind("plan_1")
        assert p.active_count == 1

    async def test_active_plan_ids(self) -> None:
        p = SessionContextPropagator()
        await p.bind("plan_a", {})
        await p.bind("plan_b", {})
        ids = p.active_plan_ids()
        assert "plan_a" in ids
        assert "plan_b" in ids

    async def test_overwrite_existing_bind(self) -> None:
        p = SessionContextPropagator()
        await p.bind("plan_x", {"v": 1})
        await p.bind("plan_x", {"v": 2})  # overwrite
        assert p.get("plan_x")["v"] == 2  # type: ignore[index]
