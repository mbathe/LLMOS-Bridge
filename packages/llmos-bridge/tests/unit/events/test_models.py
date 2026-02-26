"""Unit tests — events/models.py (UniversalEvent, EventPriority)."""

from __future__ import annotations

import time

import pytest

from llmos_bridge.events.models import EventPriority, UniversalEvent


@pytest.mark.unit
class TestEventPriority:
    def test_ordering(self) -> None:
        assert EventPriority.CRITICAL < EventPriority.HIGH < EventPriority.NORMAL
        assert EventPriority.NORMAL < EventPriority.LOW < EventPriority.BACKGROUND

    def test_int_values(self) -> None:
        assert int(EventPriority.CRITICAL) == 0
        assert int(EventPriority.BACKGROUND) == 4


@pytest.mark.unit
class TestUniversalEvent:
    def test_default_fields(self) -> None:
        e = UniversalEvent(type="test", topic="llmos.test", source="unit")
        assert e.id  # UUID generated
        assert e.timestamp > 0
        assert e.caused_by is None
        assert e.session_id is None
        assert e.priority == EventPriority.NORMAL

    def test_to_dict_basic(self) -> None:
        e = UniversalEvent(type="action_started", topic="llmos.actions", source="executor")
        d = e.to_dict()
        assert d["_event_id"] == e.id
        assert d["_topic"] == "llmos.actions"
        assert d["event"] == "action_started"
        assert d["source"] == "executor"
        assert "_timestamp" in d

    def test_to_dict_with_payload(self) -> None:
        e = UniversalEvent(
            type="action_started",
            topic="llmos.actions",
            source="executor",
            payload={"action_id": "a1", "plan_id": "p1"},
        )
        d = e.to_dict()
        assert d["action_id"] == "a1"
        assert d["plan_id"] == "p1"

    def test_to_dict_omits_optional_fields_when_default(self) -> None:
        e = UniversalEvent(type="test", topic="llmos.test", source="s")
        d = e.to_dict()
        assert "_caused_by" not in d
        assert "_session_id" not in d
        assert "_correlation_id" not in d
        assert "_priority" not in d  # NORMAL is omitted
        assert "_causes" not in d

    def test_to_dict_includes_non_default_priority(self) -> None:
        e = UniversalEvent(type="t", topic="t", source="s", priority=EventPriority.HIGH)
        d = e.to_dict()
        assert d["_priority"] == int(EventPriority.HIGH)

    def test_to_dict_includes_causality(self) -> None:
        parent_id = "parent-uuid"
        e = UniversalEvent(type="t", topic="t", source="s", caused_by=parent_id)
        d = e.to_dict()
        assert d["_caused_by"] == parent_id

    def test_to_dict_includes_session(self) -> None:
        e = UniversalEvent(type="t", topic="t", source="s", session_id="sess_xyz")
        d = e.to_dict()
        assert d["_session_id"] == "sess_xyz"

    def test_from_dict_roundtrip(self) -> None:
        original = UniversalEvent(
            type="trigger.fired",
            topic="llmos.triggers",
            source="trigger_daemon",
            payload={"trigger_id": "t1"},
            caused_by="parent-event",
            session_id="sess_abc",
            correlation_id="corr_123",
            priority=EventPriority.HIGH,
            metadata={"extra": "data"},
        )
        d = original.to_dict()
        restored = UniversalEvent.from_dict(d)

        assert restored.id == original.id
        assert restored.type == "trigger.fired"
        assert restored.topic == "llmos.triggers"
        assert restored.source == "trigger_daemon"
        assert restored.caused_by == "parent-event"
        assert restored.session_id == "sess_abc"
        assert restored.correlation_id == "corr_123"
        assert restored.priority == EventPriority.HIGH

    def test_spawn_child(self) -> None:
        parent = UniversalEvent(
            type="trigger.fired",
            topic="llmos.triggers",
            source="trigger_daemon",
            session_id="sess_abc",
            correlation_id="corr_123",
            priority=EventPriority.HIGH,
        )
        child = parent.spawn_child("plan.submitted", "llmos.plans", "executor", {"plan_id": "p1"})

        assert child.caused_by == parent.id
        assert child.id in parent.causes
        assert child.session_id == "sess_abc"
        assert child.correlation_id == "corr_123"
        assert child.priority == EventPriority.HIGH
        assert child.payload["plan_id"] == "p1"

    def test_spawn_child_updates_parent_causes(self) -> None:
        parent = UniversalEvent(type="t", topic="t", source="s")
        child1 = parent.spawn_child("c1", "t", "s")
        child2 = parent.spawn_child("c2", "t", "s")
        assert len(parent.causes) == 2
        assert child1.id in parent.causes
        assert child2.id in parent.causes

    def test_repr(self) -> None:
        e = UniversalEvent(type="test", topic="llmos.test", source="s")
        r = repr(e)
        assert "test" in r
        assert "llmos.test" in r
