"""AppTriggerBridge — bridges YAML app triggers to the daemon's TriggerDaemon.

When an app has background triggers (schedule, watch, event) and the daemon is
running with a TriggerDaemon, this bridge:

1. Converts YAML TriggerDefinition (apps/models.py) → daemon TriggerDefinition
   (triggers/models.py)
2. Registers them with the TriggerDaemon
3. Sets up fire callbacks that invoke AppRuntime.run()
4. Manages lifecycle (register on app start, deactivate on app stop)

For apps running **without** the daemon (standalone CLI), the existing
lightweight TriggerManager is used as a fallback.

Architecture
------------
External triggers (schedule, watch, event) → TriggerDaemon infrastructure:
    - Real cron scheduling via croniter (CronWatcher)
    - Real filesystem watching via watchfiles/inotify (FileSystemWatcher)
    - Priority scheduling, throttling, conflict resolution
    - Health monitoring, persistence across daemon restarts

Entry-point triggers (cli, http, webhook) are NOT registered with the daemon.
They are handled by their respective servers (CLI REPL, FastAPI routes).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Callable, Awaitable

from llmos_bridge.apps.models import (
    AppDefinition,
    TriggerDefinition as YAMLTriggerDefinition,
    TriggerType as YAMLTriggerType,
)
from llmos_bridge.apps.expression import ExpressionEngine, ExpressionContext

logger = logging.getLogger(__name__)


# Map YAML trigger types to daemon watcher types
_YAML_TO_DAEMON_TYPE = {
    YAMLTriggerType.schedule: "temporal",
    YAMLTriggerType.watch: "filesystem",
    YAMLTriggerType.event: "application",
}


def _parse_duration(s: str) -> float:
    """Parse a duration string like '30s', '5m', '1h' to seconds."""
    s = s.strip().lower()
    if not s:
        return 0
    try:
        if s.endswith("ms"):
            return float(s[:-2]) / 1000
        if s.endswith("s"):
            return float(s[:-1])
        if s.endswith("m"):
            return float(s[:-1]) * 60
        if s.endswith("h"):
            return float(s[:-1]) * 3600
        if s.endswith("d"):
            return float(s[:-1]) * 86400
        return float(s)
    except ValueError:
        return 0


class AppTriggerBridge:
    """Bridges YAML app triggers to the daemon's TriggerDaemon.

    Usage::

        bridge = AppTriggerBridge(trigger_daemon, event_bus)

        # When an app is started/set to "running":
        await bridge.register_app_triggers(app_id, app_def, run_callback)

        # When an app is stopped:
        await bridge.unregister_app_triggers(app_id)
    """

    def __init__(
        self,
        trigger_daemon: Any,  # TriggerDaemon (avoid circular import)
        event_bus: Any = None,
    ) -> None:
        self._daemon = trigger_daemon
        self._event_bus = event_bus
        # app_id → list of daemon trigger_ids we registered
        self._app_triggers: dict[str, list[str]] = {}
        # daemon_trigger_id → (run_callback, yaml_trigger) for fire routing
        self._callbacks: dict[str, tuple[Callable, YAMLTriggerDefinition]] = {}
        self._expression = ExpressionEngine()

    async def register_app_triggers(
        self,
        app_id: str,
        app_def: AppDefinition,
        run_callback: Callable[[str, dict[str, Any]], Awaitable[Any]],
    ) -> list[str]:
        """Convert YAML background triggers to daemon triggers and register them.

        Args:
            app_id: Unique app identifier
            app_def: The compiled AppDefinition
            run_callback: async fn(input_text, metadata) called when trigger fires

        Returns:
            List of daemon trigger_ids that were registered
        """
        if app_id in self._app_triggers:
            # Already registered — skip
            return self._app_triggers[app_id]

        registered_ids: list[str] = []

        for yaml_trigger in app_def.triggers:
            if yaml_trigger.type not in (
                YAMLTriggerType.schedule,
                YAMLTriggerType.watch,
                YAMLTriggerType.event,
            ):
                continue  # cli/http/webhook are entry points, not daemon triggers

            try:
                daemon_trigger = self._convert_trigger(
                    app_id, app_def, yaml_trigger, run_callback
                )
                await self._daemon.register(daemon_trigger)
                registered_ids.append(daemon_trigger.trigger_id)

                # Store callback and register with daemon for fire routing
                self._callbacks[daemon_trigger.trigger_id] = (run_callback, yaml_trigger)
                self._daemon.set_fire_callback(
                    daemon_trigger.trigger_id,
                    self._make_fire_handler(daemon_trigger.trigger_id),
                )

                logger.info(
                    "Registered daemon trigger: %s (%s) for app %s",
                    daemon_trigger.name,
                    yaml_trigger.type.value,
                    app_id,
                )
            except Exception:
                logger.exception(
                    "Failed to register trigger for app %s: %s",
                    app_id,
                    yaml_trigger.type.value,
                )

        self._app_triggers[app_id] = registered_ids
        return registered_ids

    async def unregister_app_triggers(self, app_id: str) -> None:
        """Deactivate and delete all daemon triggers for an app."""
        trigger_ids = self._app_triggers.pop(app_id, [])
        for tid in trigger_ids:
            try:
                self._callbacks.pop(tid, None)
                self._daemon.remove_fire_callback(tid)
                await self._daemon.delete(tid)
                logger.info("Deleted daemon trigger %s for app %s", tid, app_id)
            except Exception:
                logger.debug("Failed to delete trigger %s", tid, exc_info=True)

    def get_app_trigger_ids(self, app_id: str) -> list[str]:
        """Get daemon trigger IDs for an app."""
        return list(self._app_triggers.get(app_id, []))

    # ─── Fire handler ─────────────────────────────────────────────────

    def _make_fire_handler(self, daemon_trigger_id: str) -> Callable:
        """Create an async callback for TriggerDaemon to invoke on fire.

        This is the critical integration point: when the daemon's watcher
        detects a condition (cron tick, file change, event), it calls this
        handler instead of submitting an IML plan.  The handler applies
        the YAML trigger's transform/filters and invokes the app's
        run_callback to start a new AppRuntime execution.
        """
        async def _handler(trigger_def: Any, fire_event: Any) -> None:
            cb_info = self._callbacks.get(daemon_trigger_id)
            if cb_info is None:
                logger.warning("No callback for trigger %s", daemon_trigger_id)
                return

            run_callback, yaml_trigger = cb_info
            payload = fire_event.payload if hasattr(fire_event, "payload") else {}

            # Build input text from trigger config or fire event
            input_text = yaml_trigger.input or ""
            if not input_text:
                event_type = fire_event.event_type if hasattr(fire_event, "event_type") else ""
                input_text = f"Trigger fired: {event_type}"
                if payload:
                    # Include meaningful payload info
                    if "path" in payload:
                        input_text = f"File changed: {payload['path']}"
                    elif "data" in payload:
                        input_text = str(payload["data"])

            # Apply transform
            metadata = {
                "trigger_id": daemon_trigger_id,
                "event_type": getattr(fire_event, "event_type", ""),
                "fired_at": getattr(fire_event, "fired_at", 0),
                **payload,
            }
            input_text = self.apply_transform(yaml_trigger, input_text, metadata)

            # Check filters
            if not self.check_filters(yaml_trigger, input_text, metadata):
                logger.debug("App trigger filtered: %s", daemon_trigger_id)
                return

            # Invoke the app's run callback
            await run_callback(input_text, metadata)

        return _handler

    # ─── Conversion ──────────────────────────────────────────────────

    def _convert_trigger(
        self,
        app_id: str,
        app_def: AppDefinition,
        yaml_trigger: YAMLTriggerDefinition,
        run_callback: Callable[[str, dict[str, Any]], Awaitable[Any]],
    ) -> Any:
        """Convert a YAML trigger to a daemon TriggerDefinition."""
        from llmos_bridge.triggers.models import (
            TriggerCondition,
            TriggerDefinition as DaemonTriggerDefinition,
            TriggerPriority,
            TriggerType as DaemonTriggerType,
        )

        trigger_id = f"app:{app_id}:{yaml_trigger.id or yaml_trigger.type.value}:{uuid.uuid4().hex[:8]}"
        name = f"{app_def.app.name}/{yaml_trigger.id or yaml_trigger.type.value}"

        # Build condition based on trigger type
        condition = self._build_condition(yaml_trigger)

        # Build plan template that calls back into AppRuntime
        plan_template = self._build_plan_template(
            app_id, app_def, yaml_trigger, run_callback
        )

        return DaemonTriggerDefinition(
            trigger_id=trigger_id,
            name=name,
            description=f"Auto-registered from app '{app_def.app.name}' trigger",
            condition=condition,
            plan_template=plan_template,
            plan_id_prefix=f"app-{app_id}",
            priority=TriggerPriority.NORMAL,
            enabled=True,
            created_by="app",
            tags=["app-trigger", f"app:{app_id}"],
        )

    def _build_condition(self, yaml_trigger: YAMLTriggerDefinition) -> Any:
        """Build a daemon TriggerCondition from YAML trigger."""
        from llmos_bridge.triggers.models import (
            TriggerCondition,
            TriggerType as DaemonTriggerType,
        )

        if yaml_trigger.type == YAMLTriggerType.schedule:
            return self._build_schedule_condition(yaml_trigger)
        elif yaml_trigger.type == YAMLTriggerType.watch:
            return self._build_watch_condition(yaml_trigger)
        elif yaml_trigger.type == YAMLTriggerType.event:
            return self._build_event_condition(yaml_trigger)
        else:
            raise ValueError(f"Cannot convert trigger type to daemon: {yaml_trigger.type}")

    def _build_schedule_condition(self, t: YAMLTriggerDefinition) -> Any:
        """Convert YAML schedule trigger to daemon TEMPORAL condition."""
        from llmos_bridge.triggers.models import (
            TriggerCondition,
            TriggerType as DaemonTriggerType,
        )

        params: dict[str, Any] = {}

        if t.cron:
            # Real cron expression → CronWatcher
            params["schedule"] = t.cron
        elif t.when:
            # Natural language: "every 5m" → IntervalWatcher
            interval = self._parse_natural_schedule(t.when)
            if interval > 0:
                params["interval_seconds"] = interval
            else:
                # Fallback: try as cron
                params["schedule"] = t.when
        else:
            params["interval_seconds"] = 3600  # Default: hourly

        return TriggerCondition(type=DaemonTriggerType.TEMPORAL, params=params)

    def _build_watch_condition(self, t: YAMLTriggerDefinition) -> Any:
        """Convert YAML watch trigger to daemon FILESYSTEM condition."""
        from llmos_bridge.triggers.models import (
            TriggerCondition,
            TriggerType as DaemonTriggerType,
        )

        # Daemon FileSystemWatcher watches a single path
        # For multiple paths, we'll use the first one (or workspace root)
        watch_path = t.paths[0] if t.paths else "."

        return TriggerCondition(
            type=DaemonTriggerType.FILESYSTEM,
            params={
                "path": watch_path,
                "recursive": True,
                "events": ["created", "modified", "deleted"],
            },
        )

    def _build_event_condition(self, t: YAMLTriggerDefinition) -> Any:
        """Convert YAML event trigger to daemon APPLICATION condition.

        Note: Event triggers use the EventBus directly rather than the
        daemon's watcher system. We register them as APPLICATION type
        with topic info so the daemon tracks their lifecycle.
        """
        from llmos_bridge.triggers.models import (
            TriggerCondition,
            TriggerType as DaemonTriggerType,
        )

        return TriggerCondition(
            type=DaemonTriggerType.APPLICATION,
            params={
                "topic": t.topic,
                "source": "event_bus",
            },
        )

    def _build_plan_template(
        self,
        app_id: str,
        app_def: AppDefinition,
        yaml_trigger: YAMLTriggerDefinition,
        run_callback: Callable,
    ) -> dict[str, Any]:
        """Build the plan template that carries trigger metadata.

        The actual execution happens via the fire callback mechanism,
        not through IML plan submission. The plan_template stores
        metadata for the daemon's tracking.
        """
        return {
            "plan_id": "",  # Filled at fire time
            "protocol_version": "2.0",
            "execution_mode": "reactive",
            "description": f"App trigger: {app_def.app.name}/{yaml_trigger.type.value}",
            "metadata": {
                "app_id": app_id,
                "app_name": app_def.app.name,
                "trigger_type": yaml_trigger.type.value,
                "trigger_id": yaml_trigger.id,
                "transform": yaml_trigger.transform,
                "filters": yaml_trigger.filters,
                "static_input": yaml_trigger.input,
            },
            "actions": [],  # No IML actions — execution is via callback
        }

    @staticmethod
    def _parse_natural_schedule(expr: str) -> float:
        """Parse natural language schedule like 'every 5m' to seconds."""
        expr = expr.strip().lower()
        if expr.startswith("every "):
            return _parse_duration(expr[6:].strip())
        # Try parsing the whole thing as a duration
        return _parse_duration(expr)

    # ─── Transform / Filter helpers ──────────────────────────────────

    def apply_transform(
        self,
        yaml_trigger: YAMLTriggerDefinition,
        input_text: str,
        metadata: dict[str, Any],
    ) -> str:
        """Apply the trigger's transform template using ExpressionEngine."""
        if not yaml_trigger.transform:
            return input_text

        ctx = ExpressionContext(
            variables={
                "input": input_text,
                "payload": input_text,
                "trigger": {
                    "input": input_text,
                    "type": yaml_trigger.type.value,
                    **metadata,
                },
                **metadata,
            }
        )
        result = self._expression.resolve(yaml_trigger.transform, ctx)
        return str(result) if result is not None else input_text

    def check_filters(
        self,
        yaml_trigger: YAMLTriggerDefinition,
        input_text: str,
        metadata: dict[str, Any],
    ) -> bool:
        """Check if input passes trigger filters.

        Filters can be:
        - Expression conditions: contain ``{{`` or comparison operators
        - Glob patterns: matched against input text via fnmatch
        """
        if not yaml_trigger.filters:
            return True

        import fnmatch

        ctx = ExpressionContext(
            variables={
                "input": input_text,
                "payload": input_text,
                "trigger": {
                    "input": input_text,
                    "type": yaml_trigger.type.value,
                    **metadata,
                },
                **metadata,
            }
        )

        for f in yaml_trigger.filters:
            # Detect if this is an expression or a glob pattern
            is_expression = "{{" in f or "==" in f or "!=" in f or ">=" in f or "<=" in f
            if is_expression:
                try:
                    if self._expression.evaluate_condition(f, ctx):
                        return True
                    continue
                except Exception:
                    pass
            # Glob pattern matching
            if fnmatch.fnmatch(input_text, f):
                return True

        return False
