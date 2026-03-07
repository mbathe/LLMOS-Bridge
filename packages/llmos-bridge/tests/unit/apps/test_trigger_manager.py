"""Tests for TriggerManager — trigger lifecycle, firing, filtering."""

import asyncio
import pytest

from llmos_bridge.apps.models import (
    AppConfig, AppDefinition, TriggerDefinition, TriggerType, TriggerMode,
)
from llmos_bridge.apps.trigger_manager import TriggerManager, TriggerEvent, _parse_duration


# ─── Helpers ──────────────────────────────────────────────────────────


def make_app_def(*triggers: TriggerDefinition) -> AppDefinition:
    return AppDefinition(
        app=AppConfig(name="test-app", version="1.0"),
        triggers=list(triggers),
    )


def cli_trigger(**kwargs) -> TriggerDefinition:
    return TriggerDefinition(type=TriggerType.cli, **kwargs)


def http_trigger(**kwargs) -> TriggerDefinition:
    return TriggerDefinition(type=TriggerType.http, **kwargs)


def schedule_trigger(**kwargs) -> TriggerDefinition:
    return TriggerDefinition(type=TriggerType.schedule, **kwargs)


def watch_trigger(**kwargs) -> TriggerDefinition:
    return TriggerDefinition(type=TriggerType.watch, **kwargs)


def event_trigger(**kwargs) -> TriggerDefinition:
    return TriggerDefinition(type=TriggerType.event, **kwargs)


# ─── Parse Duration ──────────────────────────────────────────────────


class TestParseDuration:
    def test_seconds(self):
        assert _parse_duration("30s") == 30.0

    def test_minutes(self):
        assert _parse_duration("5m") == 300.0

    def test_hours(self):
        assert _parse_duration("1h") == 3600.0

    def test_milliseconds(self):
        assert _parse_duration("500ms") == 0.5

    def test_days(self):
        assert _parse_duration("1d") == 86400.0

    def test_empty(self):
        assert _parse_duration("") == 0

    def test_invalid(self):
        assert _parse_duration("abc") == 0

    def test_plain_number(self):
        assert _parse_duration("10") == 10.0


# ─── Trigger Accessors ───────────────────────────────────────────────


class TestTriggerAccessors:
    def test_triggers_property(self):
        t1 = cli_trigger(id="cli1")
        t2 = http_trigger(id="http1")
        mgr = TriggerManager(make_app_def(t1, t2))
        assert len(mgr.triggers) == 2

    def test_get_trigger_by_id(self):
        t = cli_trigger(id="my-cli")
        mgr = TriggerManager(make_app_def(t))
        assert mgr.get_trigger("my-cli") is not None
        assert mgr.get_trigger("nonexistent") is None

    def test_get_trigger_by_type(self):
        t = cli_trigger()
        mgr = TriggerManager(make_app_def(t))
        assert mgr.get_trigger("cli") is not None

    def test_get_cli_trigger(self):
        t = cli_trigger(id="main")
        mgr = TriggerManager(make_app_def(t, http_trigger()))
        cli = mgr.get_cli_trigger()
        assert cli is not None
        assert cli.id == "main"

    def test_get_cli_trigger_none(self):
        mgr = TriggerManager(make_app_def(http_trigger()))
        assert mgr.get_cli_trigger() is None

    def test_get_http_triggers(self):
        mgr = TriggerManager(make_app_def(
            cli_trigger(), http_trigger(id="h1"), http_trigger(id="h2"),
        ))
        http = mgr.get_http_triggers()
        assert len(http) == 2

    def test_get_schedule_triggers(self):
        mgr = TriggerManager(make_app_def(
            cli_trigger(), schedule_trigger(cron="every 5m"),
        ))
        assert len(mgr.get_schedule_triggers()) == 1


# ─── Fire Triggers ───────────────────────────────────────────────────


class TestFireTrigger:
    @pytest.mark.asyncio
    async def test_fire_returns_event(self):
        t = cli_trigger(id="cli1")
        mgr = TriggerManager(make_app_def(t))
        result = await mgr.fire(t, "hello")
        assert isinstance(result, TriggerEvent)
        assert result.input_text == "hello"
        assert result.trigger_id == "cli1"

    @pytest.mark.asyncio
    async def test_fire_calls_callback(self):
        received = []

        async def on_trigger(event: TriggerEvent):
            received.append(event)
            return "ok"

        t = cli_trigger(id="cli1")
        mgr = TriggerManager(make_app_def(t), on_trigger=on_trigger)
        result = await mgr.fire(t, "world")
        assert result == "ok"
        assert len(received) == 1
        assert received[0].input_text == "world"

    @pytest.mark.asyncio
    async def test_fire_with_metadata(self):
        t = cli_trigger()
        mgr = TriggerManager(make_app_def(t))
        result = await mgr.fire(t, "test", source="api")
        assert result.metadata["source"] == "api"


# ─── Transform ───────────────────────────────────────────────────────


class TestTransform:
    @pytest.mark.asyncio
    async def test_transform_template(self):
        t = cli_trigger(transform="Task: {{input}}")
        mgr = TriggerManager(make_app_def(t))
        result = await mgr.fire(t, "fix bugs")
        assert result.input_text == "Task: fix bugs"

    @pytest.mark.asyncio
    async def test_no_transform(self):
        t = cli_trigger()
        mgr = TriggerManager(make_app_def(t))
        result = await mgr.fire(t, "raw input")
        assert result.input_text == "raw input"

    @pytest.mark.asyncio
    async def test_transform_with_metadata(self):
        t = http_trigger(transform="From {{source}}: {{payload}}")
        mgr = TriggerManager(make_app_def(t))
        result = await mgr.fire(t, "data", source="webhook")
        assert result.input_text == "From webhook: data"


# ─── Filters ─────────────────────────────────────────────────────────


class TestFilters:
    @pytest.mark.asyncio
    async def test_no_filters_passes(self):
        t = cli_trigger()
        mgr = TriggerManager(make_app_def(t))
        result = await mgr.fire(t, "anything")
        assert result is not None

    @pytest.mark.asyncio
    async def test_filter_matches(self):
        t = cli_trigger(filters=["fix*", "bug*"])
        mgr = TriggerManager(make_app_def(t))
        result = await mgr.fire(t, "fix the issue")
        assert result is not None

    @pytest.mark.asyncio
    async def test_filter_no_match(self):
        t = cli_trigger(filters=["deploy*"])
        mgr = TriggerManager(make_app_def(t))
        result = await mgr.fire(t, "fix the issue")
        assert result is None


# ─── Schedule Parsing ────────────────────────────────────────────────


class TestScheduleParsing:
    def test_every_5m(self):
        interval = TriggerManager._parse_cron_interval("every 5m")
        assert interval == 300.0

    def test_every_30s(self):
        interval = TriggerManager._parse_cron_interval("every 30s")
        assert interval == 30.0

    def test_cron_every_5_minutes(self):
        interval = TriggerManager._parse_cron_interval("*/5 * * * *")
        assert interval == 300.0

    def test_cron_hourly(self):
        interval = TriggerManager._parse_cron_interval("0 * * * *")
        assert interval == 3600.0

    def test_cron_daily(self):
        interval = TriggerManager._parse_cron_interval("0 0 * * *")
        assert interval == 86400.0

    def test_invalid_cron(self):
        interval = TriggerManager._parse_cron_interval("invalid")
        assert interval == 0


# ─── Start / Stop ────────────────────────────────────────────────────


class TestStartStop:
    @pytest.mark.asyncio
    async def test_start_stop_lifecycle(self):
        mgr = TriggerManager(make_app_def(cli_trigger()))
        assert not mgr.running
        await mgr.start()
        assert mgr.running
        await mgr.stop()
        assert not mgr.running

    @pytest.mark.asyncio
    async def test_start_idempotent(self):
        mgr = TriggerManager(make_app_def(cli_trigger()))
        await mgr.start()
        await mgr.start()  # should not error
        assert mgr.running
        await mgr.stop()

    @pytest.mark.asyncio
    async def test_stop_without_start(self):
        mgr = TriggerManager(make_app_def(cli_trigger()))
        await mgr.stop()  # should not error
        assert not mgr.running


# ─── Event Trigger ───────────────────────────────────────────────────


class TestEventTrigger:
    @pytest.mark.asyncio
    async def test_event_subscription_no_bus(self):
        """Event trigger without bus logs warning but doesn't crash."""
        t = event_trigger(topic="llmos.actions")
        mgr = TriggerManager(make_app_def(t))
        await mgr.start()
        await mgr.stop()

    @pytest.mark.asyncio
    async def test_event_subscription_with_bus(self):
        """Event trigger with bus subscribes to topic."""
        subscriptions = {}

        class MockBus:
            async def subscribe(self, topic, handler):
                subscriptions[topic] = handler

        t = event_trigger(topic="llmos.actions")
        mgr = TriggerManager(make_app_def(t), event_bus=MockBus())
        await mgr.start()
        assert "llmos.actions" in subscriptions
        await mgr.stop()
