"""Tests for AppTriggerBridge — bridging YAML triggers to daemon TriggerDaemon."""

import asyncio
import pytest

from llmos_bridge.apps.models import (
    AppConfig, AppDefinition, TriggerDefinition, TriggerType,
)
from llmos_bridge.apps.trigger_bridge import AppTriggerBridge, _parse_duration


# ─── Helpers ──────────────────────────────────────────────────────────


def make_app_def(*triggers: TriggerDefinition) -> AppDefinition:
    return AppDefinition(
        app=AppConfig(name="test-app", version="1.0"),
        triggers=list(triggers),
    )


def schedule_trigger(**kwargs) -> TriggerDefinition:
    return TriggerDefinition(type=TriggerType.schedule, **kwargs)


def watch_trigger(**kwargs) -> TriggerDefinition:
    return TriggerDefinition(type=TriggerType.watch, **kwargs)


def event_trigger(**kwargs) -> TriggerDefinition:
    return TriggerDefinition(type=TriggerType.event, **kwargs)


def cli_trigger(**kwargs) -> TriggerDefinition:
    return TriggerDefinition(type=TriggerType.cli, **kwargs)


def http_trigger(**kwargs) -> TriggerDefinition:
    return TriggerDefinition(type=TriggerType.http, **kwargs)


class MockTriggerDaemon:
    """Minimal mock of TriggerDaemon for testing the bridge."""

    def __init__(self):
        self.registered: list = []
        self.deleted: list[str] = []
        self.fire_callbacks: dict = {}

    async def register(self, trigger):
        self.registered.append(trigger)
        return trigger

    async def delete(self, trigger_id: str) -> bool:
        self.deleted.append(trigger_id)
        return True

    def set_fire_callback(self, trigger_id: str, callback) -> None:
        self.fire_callbacks[trigger_id] = callback

    def remove_fire_callback(self, trigger_id: str) -> None:
        self.fire_callbacks.pop(trigger_id, None)


# ─── Bridge Registration ─────────────────────────────────────────────


class TestBridgeRegistration:
    @pytest.mark.asyncio
    async def test_register_schedule_trigger(self):
        daemon = MockTriggerDaemon()
        bridge = AppTriggerBridge(trigger_daemon=daemon)
        app_def = make_app_def(schedule_trigger(cron="0 9 * * 1-5"))

        ids = await bridge.register_app_triggers("app1", app_def, self._noop)
        assert len(ids) == 1
        assert len(daemon.registered) == 1
        assert daemon.registered[0].condition.type.value == "temporal"
        assert "schedule" in daemon.registered[0].condition.params

    @pytest.mark.asyncio
    async def test_register_watch_trigger(self):
        daemon = MockTriggerDaemon()
        bridge = AppTriggerBridge(trigger_daemon=daemon)
        app_def = make_app_def(watch_trigger(paths=["src/**/*.py"]))

        ids = await bridge.register_app_triggers("app1", app_def, self._noop)
        assert len(ids) == 1
        assert daemon.registered[0].condition.type.value == "filesystem"
        assert daemon.registered[0].condition.params["path"] == "src/**/*.py"

    @pytest.mark.asyncio
    async def test_register_event_trigger(self):
        daemon = MockTriggerDaemon()
        bridge = AppTriggerBridge(trigger_daemon=daemon)
        app_def = make_app_def(event_trigger(topic="llmos.actions"))

        ids = await bridge.register_app_triggers("app1", app_def, self._noop)
        assert len(ids) == 1
        assert daemon.registered[0].condition.type.value == "application"
        assert daemon.registered[0].condition.params["topic"] == "llmos.actions"

    @pytest.mark.asyncio
    async def test_ignores_cli_http_triggers(self):
        """CLI and HTTP triggers are entry points — not daemon background triggers."""
        daemon = MockTriggerDaemon()
        bridge = AppTriggerBridge(trigger_daemon=daemon)
        app_def = make_app_def(
            cli_trigger(),
            http_trigger(path="/api"),
            schedule_trigger(when="every 5m"),
        )

        ids = await bridge.register_app_triggers("app1", app_def, self._noop)
        assert len(ids) == 1  # Only the schedule trigger
        assert len(daemon.registered) == 1

    @pytest.mark.asyncio
    async def test_register_multiple_triggers(self):
        daemon = MockTriggerDaemon()
        bridge = AppTriggerBridge(trigger_daemon=daemon)
        app_def = make_app_def(
            schedule_trigger(cron="*/5 * * * *"),
            watch_trigger(paths=["data/"]),
            event_trigger(topic="llmos.modules.installed"),
        )

        ids = await bridge.register_app_triggers("app1", app_def, self._noop)
        assert len(ids) == 3
        assert len(daemon.registered) == 3

    @pytest.mark.asyncio
    async def test_idempotent_registration(self):
        """Registering the same app twice should not duplicate triggers."""
        daemon = MockTriggerDaemon()
        bridge = AppTriggerBridge(trigger_daemon=daemon)
        app_def = make_app_def(schedule_trigger(when="every 1h"))

        await bridge.register_app_triggers("app1", app_def, self._noop)
        await bridge.register_app_triggers("app1", app_def, self._noop)
        assert len(daemon.registered) == 1

    @pytest.mark.asyncio
    async def test_unregister_deletes_triggers(self):
        daemon = MockTriggerDaemon()
        bridge = AppTriggerBridge(trigger_daemon=daemon)
        app_def = make_app_def(
            schedule_trigger(cron="0 0 * * *"),
            watch_trigger(paths=["logs/"]),
        )

        await bridge.register_app_triggers("app1", app_def, self._noop)
        assert len(daemon.registered) == 2

        await bridge.unregister_app_triggers("app1")
        assert len(daemon.deleted) == 2
        assert bridge.get_app_trigger_ids("app1") == []

    @pytest.mark.asyncio
    async def test_unregister_nonexistent_is_noop(self):
        daemon = MockTriggerDaemon()
        bridge = AppTriggerBridge(trigger_daemon=daemon)
        await bridge.unregister_app_triggers("nonexistent")  # Should not error

    @staticmethod
    async def _noop(input_text: str, metadata: dict):
        pass


# ─── Schedule Conversion ────────────────────────────────────────────


class TestScheduleConversion:
    def test_cron_to_temporal_schedule(self):
        bridge = AppTriggerBridge(trigger_daemon=MockTriggerDaemon())
        t = schedule_trigger(cron="0 9 * * 1-5")
        condition = bridge._build_condition(t)
        assert condition.type.value == "temporal"
        assert condition.params["schedule"] == "0 9 * * 1-5"

    def test_natural_language_to_interval(self):
        bridge = AppTriggerBridge(trigger_daemon=MockTriggerDaemon())
        t = schedule_trigger(when="every 5m")
        condition = bridge._build_condition(t)
        assert condition.type.value == "temporal"
        assert condition.params["interval_seconds"] == 300.0

    def test_natural_language_every_30s(self):
        bridge = AppTriggerBridge(trigger_daemon=MockTriggerDaemon())
        t = schedule_trigger(when="every 30s")
        condition = bridge._build_condition(t)
        assert condition.params["interval_seconds"] == 30.0

    def test_natural_language_every_1h(self):
        bridge = AppTriggerBridge(trigger_daemon=MockTriggerDaemon())
        t = schedule_trigger(when="every 1h")
        condition = bridge._build_condition(t)
        assert condition.params["interval_seconds"] == 3600.0


# ─── Watch Conversion ───────────────────────────────────────────────


class TestWatchConversion:
    def test_watch_to_filesystem(self):
        bridge = AppTriggerBridge(trigger_daemon=MockTriggerDaemon())
        t = watch_trigger(paths=["src/**/*.py", "tests/**/*.py"])
        condition = bridge._build_condition(t)
        assert condition.type.value == "filesystem"
        assert condition.params["path"] == "src/**/*.py"
        assert condition.params["recursive"] is True

    def test_watch_default_path(self):
        bridge = AppTriggerBridge(trigger_daemon=MockTriggerDaemon())
        t = watch_trigger(paths=[])
        condition = bridge._build_condition(t)
        assert condition.params["path"] == "."


# ─── Transform / Filter (ExpressionEngine) ──────────────────────────


class TestBridgeTransformFilter:
    def test_apply_transform_simple(self):
        bridge = AppTriggerBridge(trigger_daemon=MockTriggerDaemon())
        t = schedule_trigger(transform="Task: {{input}}")
        result = bridge.apply_transform(t, "fix bugs", {})
        assert result == "Task: fix bugs"

    def test_apply_transform_with_metadata(self):
        bridge = AppTriggerBridge(trigger_daemon=MockTriggerDaemon())
        t = http_trigger(path="/api", transform="From {{source}}: {{payload}}")
        result = bridge.apply_transform(t, "data", {"source": "webhook"})
        assert result == "From webhook: data"

    def test_apply_transform_no_template(self):
        bridge = AppTriggerBridge(trigger_daemon=MockTriggerDaemon())
        t = schedule_trigger()
        result = bridge.apply_transform(t, "raw input", {})
        assert result == "raw input"

    def test_check_filters_no_filters(self):
        bridge = AppTriggerBridge(trigger_daemon=MockTriggerDaemon())
        t = cli_trigger()
        assert bridge.check_filters(t, "anything", {}) is True

    def test_check_filters_glob_match(self):
        bridge = AppTriggerBridge(trigger_daemon=MockTriggerDaemon())
        t = cli_trigger(filters=["fix*", "bug*"])
        assert bridge.check_filters(t, "fix the issue", {}) is True

    def test_check_filters_glob_no_match(self):
        bridge = AppTriggerBridge(trigger_daemon=MockTriggerDaemon())
        t = cli_trigger(filters=["deploy*"])
        assert bridge.check_filters(t, "fix the issue", {}) is False


# ─── Plan Template ──────────────────────────────────────────────────


class TestPlanTemplate:
    def test_plan_template_has_metadata(self):
        bridge = AppTriggerBridge(trigger_daemon=MockTriggerDaemon())
        app_def = make_app_def(schedule_trigger(cron="0 9 * * *", input="Daily report"))
        t = app_def.triggers[0]
        template = bridge._build_plan_template("app1", app_def, t, self._noop)

        assert template["metadata"]["app_id"] == "app1"
        assert template["metadata"]["app_name"] == "test-app"
        assert template["metadata"]["trigger_type"] == "schedule"
        assert template["metadata"]["static_input"] == "Daily report"
        assert template["protocol_version"] == "2.0"

    @staticmethod
    async def _noop(input_text: str, metadata: dict):
        pass


# ─── Parse Duration ─────────────────────────────────────────────────


class TestBridgeParseDuration:
    def test_seconds(self):
        assert _parse_duration("30s") == 30.0

    def test_minutes(self):
        assert _parse_duration("5m") == 300.0

    def test_hours(self):
        assert _parse_duration("1h") == 3600.0

    def test_days(self):
        assert _parse_duration("1d") == 86400.0

    def test_milliseconds(self):
        assert _parse_duration("500ms") == 0.5

    def test_empty(self):
        assert _parse_duration("") == 0

    def test_invalid(self):
        assert _parse_duration("abc") == 0

    def test_plain_number(self):
        assert _parse_duration("10") == 10.0


# ─── Trigger ID tracking ────────────────────────────────────────────


class TestTriggerIdTracking:
    @pytest.mark.asyncio
    async def test_get_app_trigger_ids(self):
        daemon = MockTriggerDaemon()
        bridge = AppTriggerBridge(trigger_daemon=daemon)
        app_def = make_app_def(
            schedule_trigger(when="every 1h"),
            event_trigger(topic="test.events"),
        )

        await bridge.register_app_triggers("app1", app_def, self._noop)
        ids = bridge.get_app_trigger_ids("app1")
        assert len(ids) == 2
        assert all(id.startswith("app:app1:") for id in ids)

    @pytest.mark.asyncio
    async def test_get_app_trigger_ids_empty(self):
        bridge = AppTriggerBridge(trigger_daemon=MockTriggerDaemon())
        assert bridge.get_app_trigger_ids("nonexistent") == []

    @staticmethod
    async def _noop(input_text: str, metadata: dict):
        pass


# ─── Fire Callback Integration ────────────────────────────────────


class TestFireCallbackIntegration:
    """Tests that the bridge correctly stores and wires fire callbacks."""

    @pytest.mark.asyncio
    async def test_fire_callbacks_registered_with_daemon(self):
        """After register, daemon should have fire callbacks for each trigger."""
        daemon = MockTriggerDaemon()
        bridge = AppTriggerBridge(trigger_daemon=daemon)
        app_def = make_app_def(
            schedule_trigger(when="every 5m"),
            watch_trigger(paths=["src/"]),
        )

        await bridge.register_app_triggers("app1", app_def, self._noop)
        # Each background trigger should have a fire callback registered
        assert len(daemon.fire_callbacks) == 2
        for tid in bridge.get_app_trigger_ids("app1"):
            assert tid in daemon.fire_callbacks

    @pytest.mark.asyncio
    async def test_fire_callbacks_removed_on_unregister(self):
        daemon = MockTriggerDaemon()
        bridge = AppTriggerBridge(trigger_daemon=daemon)
        app_def = make_app_def(schedule_trigger(cron="0 9 * * *"))

        await bridge.register_app_triggers("app1", app_def, self._noop)
        assert len(daemon.fire_callbacks) == 1

        await bridge.unregister_app_triggers("app1")
        assert len(daemon.fire_callbacks) == 0

    @pytest.mark.asyncio
    async def test_fire_handler_invokes_run_callback(self):
        """Simulate a daemon fire and verify the app's run_callback is called."""
        daemon = MockTriggerDaemon()
        bridge = AppTriggerBridge(trigger_daemon=daemon)
        received = []

        async def capture_callback(input_text: str, metadata: dict):
            received.append({"input": input_text, "metadata": metadata})

        app_def = make_app_def(schedule_trigger(when="every 1h", input="Daily check"))
        await bridge.register_app_triggers("app1", app_def, capture_callback)

        # Get the fire callback and simulate a daemon fire
        tid = bridge.get_app_trigger_ids("app1")[0]
        fire_cb = daemon.fire_callbacks[tid]

        # Simulate TriggerFireEvent
        from dataclasses import dataclass, field
        import time

        @dataclass
        class FakeFireEvent:
            trigger_id: str = tid
            trigger_name: str = "test"
            event_type: str = "temporal.interval"
            payload: dict = field(default_factory=dict)
            fired_at: float = field(default_factory=time.time)

        await fire_cb(None, FakeFireEvent())

        assert len(received) == 1
        assert received[0]["input"] == "Daily check"

    @pytest.mark.asyncio
    async def test_fire_handler_applies_transform(self):
        """Transform template should be applied before invoking callback."""
        daemon = MockTriggerDaemon()
        bridge = AppTriggerBridge(trigger_daemon=daemon)
        received = []

        async def capture(input_text: str, metadata: dict):
            received.append(input_text)

        app_def = make_app_def(
            schedule_trigger(when="every 1h", transform="Task: {{input}}")
        )
        await bridge.register_app_triggers("app1", app_def, capture)

        tid = bridge.get_app_trigger_ids("app1")[0]
        fire_cb = daemon.fire_callbacks[tid]

        from dataclasses import dataclass, field
        import time

        @dataclass
        class FakeFireEvent:
            trigger_id: str = tid
            trigger_name: str = "test"
            event_type: str = "temporal.interval"
            payload: dict = field(default_factory=dict)
            fired_at: float = field(default_factory=time.time)

        await fire_cb(None, FakeFireEvent())

        assert len(received) == 1
        assert received[0].startswith("Task: ")

    @pytest.mark.asyncio
    async def test_fire_handler_respects_filters(self):
        """If filters don't match, callback should NOT be invoked."""
        daemon = MockTriggerDaemon()
        bridge = AppTriggerBridge(trigger_daemon=daemon)
        received = []

        async def capture(input_text: str, metadata: dict):
            received.append(input_text)

        # Filter requires "deploy*" pattern — won't match trigger fire input
        app_def = make_app_def(
            schedule_trigger(when="every 1h", filters=["deploy*"])
        )
        await bridge.register_app_triggers("app1", app_def, capture)

        tid = bridge.get_app_trigger_ids("app1")[0]
        fire_cb = daemon.fire_callbacks[tid]

        from dataclasses import dataclass, field
        import time

        @dataclass
        class FakeFireEvent:
            trigger_id: str = tid
            trigger_name: str = "test"
            event_type: str = "temporal.interval"
            payload: dict = field(default_factory=dict)
            fired_at: float = field(default_factory=time.time)

        await fire_cb(None, FakeFireEvent())

        # Callback should NOT have been invoked due to filter
        assert len(received) == 0

    @pytest.mark.asyncio
    async def test_fire_handler_with_file_change_payload(self):
        """Watch trigger fire should include file path info in input."""
        daemon = MockTriggerDaemon()
        bridge = AppTriggerBridge(trigger_daemon=daemon)
        received = []

        async def capture(input_text: str, metadata: dict):
            received.append(input_text)

        app_def = make_app_def(watch_trigger(paths=["src/"]))
        await bridge.register_app_triggers("app1", app_def, capture)

        tid = bridge.get_app_trigger_ids("app1")[0]
        fire_cb = daemon.fire_callbacks[tid]

        from dataclasses import dataclass, field
        import time

        @dataclass
        class FakeFireEvent:
            trigger_id: str = tid
            trigger_name: str = "test"
            event_type: str = "filesystem.changed"
            payload: dict = field(default_factory=lambda: {"path": "src/main.py"})
            fired_at: float = field(default_factory=time.time)

        await fire_cb(None, FakeFireEvent())

        assert len(received) == 1
        assert "src/main.py" in received[0]

    @staticmethod
    async def _noop(input_text: str, metadata: dict):
        pass


# ─── Daemon APPLICATION Trigger Integration ───────────────────────


class TestDaemonApplicationTrigger:
    """Test that APPLICATION triggers are properly handled by TriggerDaemon."""

    @pytest.mark.asyncio
    async def test_daemon_arms_application_trigger(self):
        """TriggerDaemon should handle APPLICATION type without crashing."""
        from llmos_bridge.triggers.daemon import TriggerDaemon
        from llmos_bridge.triggers.models import (
            TriggerCondition,
            TriggerDefinition as DaemonTriggerDefinition,
            TriggerType as DaemonTriggerType,
        )
        from llmos_bridge.triggers.store import TriggerStore
        from llmos_bridge.events.bus import NullEventBus

        import tempfile
        from pathlib import Path
        store = TriggerStore(Path(tempfile.mktemp(suffix=".db")))
        await store.init()
        bus = NullEventBus()

        daemon = TriggerDaemon(store=store, event_bus=bus)
        await daemon.start()

        try:
            trigger = DaemonTriggerDefinition(
                trigger_id="test-app-trigger",
                name="test-event",
                condition=TriggerCondition(
                    type=DaemonTriggerType.APPLICATION,
                    params={"topic": "test.events"},
                ),
                plan_template={"actions": []},
            )
            await daemon.register(trigger)
            # Should not crash — APPLICATION is handled via EventBus subscription
            loaded = await daemon.get("test-app-trigger")
            assert loaded is not None
        finally:
            await daemon.stop()

    @pytest.mark.asyncio
    async def test_daemon_fire_callback_invoked(self):
        """When a trigger with a fire callback fires, the callback runs."""
        from llmos_bridge.triggers.daemon import TriggerDaemon
        from llmos_bridge.triggers.models import (
            TriggerCondition,
            TriggerDefinition as DaemonTriggerDefinition,
            TriggerFireEvent,
            TriggerType as DaemonTriggerType,
        )
        from llmos_bridge.triggers.store import TriggerStore
        from llmos_bridge.events.bus import NullEventBus

        import tempfile
        from pathlib import Path
        store = TriggerStore(Path(tempfile.mktemp(suffix=".db")))
        await store.init()
        bus = NullEventBus()

        daemon = TriggerDaemon(store=store, event_bus=bus)
        await daemon.start()

        fired = []

        async def my_callback(trigger, fire_event):
            fired.append(fire_event)

        try:
            trigger = DaemonTriggerDefinition(
                trigger_id="cb-trigger",
                name="callback-test",
                condition=TriggerCondition(
                    type=DaemonTriggerType.APPLICATION,
                    params={"topic": "test.topic"},
                ),
                plan_template={"actions": []},
            )
            await daemon.register(trigger)
            daemon.set_fire_callback("cb-trigger", my_callback)

            # Directly call _submit_plan to test the callback path
            fire_event = TriggerFireEvent(
                trigger_id="cb-trigger",
                trigger_name="callback-test",
                event_type="test.fired",
                payload={"key": "value"},
            )
            result = await daemon._submit_plan(trigger, fire_event)
            assert result is not None  # plan_id returned
            assert len(fired) == 1
            assert fired[0].payload == {"key": "value"}
        finally:
            await daemon.stop()

    @pytest.mark.asyncio
    async def test_full_bridge_to_daemon_pipeline(self):
        """End-to-end: YAML trigger -> bridge -> daemon -> fire -> callback -> app."""
        from llmos_bridge.triggers.daemon import TriggerDaemon
        from llmos_bridge.triggers.store import TriggerStore
        from llmos_bridge.events.bus import NullEventBus

        import tempfile
        from pathlib import Path
        store = TriggerStore(Path(tempfile.mktemp(suffix=".db")))
        await store.init()
        bus = NullEventBus()

        daemon = TriggerDaemon(store=store, event_bus=bus)
        await daemon.start()

        received = []

        async def app_run(input_text: str, metadata: dict):
            received.append({"input": input_text, "metadata": metadata})

        try:
            bridge = AppTriggerBridge(trigger_daemon=daemon, event_bus=bus)
            app_def = make_app_def(
                schedule_trigger(when="every 1h", input="Hourly report"),
            )
            ids = await bridge.register_app_triggers("test-app", app_def, app_run)
            assert len(ids) == 1

            # Verify trigger is registered in daemon
            trigger = await daemon.get(ids[0])
            assert trigger is not None

            # Simulate what happens when the daemon's watcher fires:
            # call _on_watcher_fire -> scheduler -> _submit_plan -> callback
            from llmos_bridge.triggers.models import TriggerFireEvent

            fire_event = TriggerFireEvent(
                trigger_id=ids[0],
                trigger_name=trigger.name,
                event_type="temporal.interval",
                payload={},
            )
            result = await daemon._submit_plan(trigger, fire_event)
            assert result is not None

            assert len(received) == 1
            assert received[0]["input"] == "Hourly report"

            # Cleanup
            await bridge.unregister_app_triggers("test-app")
            assert bridge.get_app_trigger_ids("test-app") == []
        finally:
            await daemon.stop()
