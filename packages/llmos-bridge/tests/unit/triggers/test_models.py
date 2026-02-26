"""Unit tests — triggers/models.py."""

from __future__ import annotations

import time

import pytest

from llmos_bridge.triggers.models import (
    TriggerCondition,
    TriggerDefinition,
    TriggerFireEvent,
    TriggerHealth,
    TriggerPriority,
    TriggerState,
    TriggerType,
)


@pytest.mark.unit
class TestTriggerHealth:
    def test_record_fire_increments_count(self) -> None:
        h = TriggerHealth()
        h.record_fire(latency_ms=10.0)
        assert h.fire_count == 1
        assert h.last_fired_at is not None

    def test_record_fire_updates_avg_latency(self) -> None:
        h = TriggerHealth()
        h.record_fire(latency_ms=100.0)
        assert h.avg_latency_ms == 100.0
        h.record_fire(latency_ms=200.0)
        # EMA: 0.8 * 100 + 0.2 * 200 = 120
        assert abs(h.avg_latency_ms - 120.0) < 0.01

    def test_record_fail(self) -> None:
        h = TriggerHealth()
        h.record_fail("connection refused")
        assert h.fail_count == 1
        assert h.last_error == "connection refused"

    def test_record_throttle(self) -> None:
        h = TriggerHealth()
        h.record_throttle()
        assert h.throttle_count == 1


@pytest.mark.unit
class TestTriggerDefinition:
    def test_default_construction(self) -> None:
        t = TriggerDefinition()
        assert t.trigger_id  # UUID generated
        assert t.state == TriggerState.REGISTERED
        assert t.priority == TriggerPriority.NORMAL
        assert t.enabled is True
        assert t.chain_depth == 0

    def test_can_fire_when_active(self) -> None:
        t = TriggerDefinition()
        t.state = TriggerState.ACTIVE
        assert t.can_fire() is True

    def test_cannot_fire_when_disabled(self) -> None:
        t = TriggerDefinition()
        t.state = TriggerState.ACTIVE
        t.enabled = False
        assert t.can_fire() is False

    def test_cannot_fire_when_registered(self) -> None:
        t = TriggerDefinition()
        t.state = TriggerState.REGISTERED
        assert t.can_fire() is False

    def test_cannot_fire_when_failed(self) -> None:
        t = TriggerDefinition()
        t.state = TriggerState.FAILED
        assert t.can_fire() is False

    def test_cannot_fire_when_expired(self) -> None:
        t = TriggerDefinition()
        t.state = TriggerState.ACTIVE
        t.expires_at = time.time() - 1  # already expired
        assert t.can_fire() is False

    def test_can_fire_when_not_expired(self) -> None:
        t = TriggerDefinition()
        t.state = TriggerState.ACTIVE
        t.expires_at = time.time() + 3600  # 1 hour from now
        assert t.can_fire() is True

    def test_throttle_by_min_interval(self) -> None:
        t = TriggerDefinition()
        t.state = TriggerState.ACTIVE
        t.min_interval_seconds = 60.0
        t.health.last_fired_at = time.time() - 10  # only 10s ago
        assert t.can_fire() is False

    def test_no_throttle_after_interval_elapsed(self) -> None:
        t = TriggerDefinition()
        t.state = TriggerState.ACTIVE
        t.min_interval_seconds = 60.0
        t.health.last_fired_at = time.time() - 120  # 2 min ago
        assert t.can_fire() is True

    def test_is_expired(self) -> None:
        t = TriggerDefinition()
        t.expires_at = time.time() - 1
        assert t.is_expired() is True

    def test_is_not_expired_when_none(self) -> None:
        t = TriggerDefinition()
        assert t.is_expired() is False

    def test_generate_plan_id(self) -> None:
        t = TriggerDefinition(plan_id_prefix="my_trigger")
        pid = t.generate_plan_id()
        assert pid.startswith("my_trigger_")
        assert len(pid) > len("my_trigger_")

    def test_generate_unique_plan_ids(self) -> None:
        t = TriggerDefinition()
        ids = {t.generate_plan_id() for _ in range(10)}
        assert len(ids) == 10  # all unique

    def test_watching_state_can_fire(self) -> None:
        t = TriggerDefinition()
        t.state = TriggerState.WATCHING
        assert t.can_fire() is True

    def test_fired_state_can_fire(self) -> None:
        t = TriggerDefinition()
        t.state = TriggerState.FIRED
        assert t.can_fire() is True


@pytest.mark.unit
class TestTriggerFireEvent:
    def test_to_dict(self) -> None:
        e = TriggerFireEvent(
            trigger_id="t1",
            trigger_name="My Trigger",
            event_type="filesystem.changed",
            payload={"path": "/tmp/test.txt"},
        )
        d = e.to_dict()
        assert d["trigger_id"] == "t1"
        assert d["trigger_name"] == "My Trigger"
        assert d["event_type"] == "filesystem.changed"
        assert d["payload"]["path"] == "/tmp/test.txt"
        assert "fired_at" in d

    def test_as_template_context(self) -> None:
        e = TriggerFireEvent(
            trigger_id="t1",
            trigger_name="My Trigger",
            event_type="filesystem.changed",
            payload={"path": "/tmp/doc.txt"},
        )
        ctx = e.as_template_context()
        assert ctx["trigger_id"] == "t1"
        assert ctx["event_type"] == "filesystem.changed"
        assert ctx["payload"]["path"] == "/tmp/doc.txt"
        assert "fired_at" in ctx
