"""Tests — TriggerDaemon execution_source metadata injection."""
from __future__ import annotations

import pytest

from llmos_bridge.triggers.daemon import TriggerDaemon
from llmos_bridge.triggers.models import TriggerDefinition, TriggerFireEvent


class TestDaemonMetadata:
    def _make_daemon(self):
        """Create a minimal TriggerDaemon (no store needed for _build_plan)."""
        daemon = object.__new__(TriggerDaemon)
        daemon._store = None
        daemon._bus = None
        daemon._executor = None
        daemon._propagator = None
        daemon._max_concurrent = 5
        daemon._watchers = {}
        daemon._triggers = {}
        daemon._conflict = None
        daemon._scheduler = None
        daemon._health_task = None
        daemon._started = False
        return daemon

    def _make_trigger(self) -> TriggerDefinition:
        return TriggerDefinition(
            name="test-trigger",
            condition={"type": "cron", "schedule": "* * * * *"},
            plan_template={
                "description": "Triggered plan",
                "actions": [{"id": "a1", "action": "echo", "module": "os_exec", "params": {}}],
            },
        )

    def _make_fire_event(self, trigger: TriggerDefinition) -> TriggerFireEvent:
        return TriggerFireEvent(
            trigger_id=trigger.trigger_id,
            trigger_name=trigger.name,
            event_type="cron.tick",
            payload={},
        )

    def test_build_plan_injects_execution_source(self):
        daemon = self._make_daemon()
        trigger = self._make_trigger()
        fire_event = self._make_fire_event(trigger)
        plan = daemon._build_plan(trigger, fire_event, "plan-001")
        assert plan["metadata"]["execution_source"] == "trigger_daemon"

    def test_build_plan_injects_trigger_id(self):
        daemon = self._make_daemon()
        trigger = self._make_trigger()
        fire_event = self._make_fire_event(trigger)
        plan = daemon._build_plan(trigger, fire_event, "plan-001")
        assert plan["metadata"]["trigger_id"] == trigger.trigger_id

    def test_build_plan_injects_trigger_name(self):
        daemon = self._make_daemon()
        trigger = self._make_trigger()
        fire_event = self._make_fire_event(trigger)
        plan = daemon._build_plan(trigger, fire_event, "plan-001")
        assert plan["metadata"]["trigger_name"] == "test-trigger"

    def test_build_plan_preserves_existing_template(self):
        daemon = self._make_daemon()
        trigger = self._make_trigger()
        trigger.plan_template["metadata"] = {"custom_key": "custom_value"}
        fire_event = self._make_fire_event(trigger)
        plan = daemon._build_plan(trigger, fire_event, "plan-002")
        # Custom key preserved
        assert plan["metadata"]["custom_key"] == "custom_value"
        # Daemon keys still injected
        assert plan["metadata"]["execution_source"] == "trigger_daemon"
