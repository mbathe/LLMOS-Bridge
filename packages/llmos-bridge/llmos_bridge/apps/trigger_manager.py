"""TriggerManager — manages triggers that start LLMOS applications.

Handles trigger types:
- cli: Interactive terminal input
- http: HTTP endpoint (webhook)
- schedule: Cron-based scheduling
- watch: File/directory watching
- event: EventBus subscription

When the daemon is running, background triggers (schedule, watch, event) are
delegated to the daemon's TriggerDaemon via AppTriggerBridge for real cron
scheduling, inotify filesystem watching, and full priority/throttle/conflict
management. This class serves as the standalone fallback.

Usage:
    manager = TriggerManager(app_def, runtime)
    await manager.start()   # starts all triggers
    await manager.stop()    # stops all triggers
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Awaitable

from .expression import ExpressionEngine, ExpressionContext
from .models import AppDefinition, TriggerDefinition, TriggerType, TriggerMode

logger = logging.getLogger(__name__)


@dataclass
class TriggerEvent:
    """An event produced by a trigger."""
    trigger_id: str
    trigger_type: str
    input_text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


class TriggerManager:
    """Manages all triggers for an application.

    Each trigger type is handled by a specific start/stop method.
    When a trigger fires, the callback is invoked with a TriggerEvent.
    """

    def __init__(
        self,
        app_def: AppDefinition,
        *,
        on_trigger: Callable[[TriggerEvent], Awaitable[Any]] | None = None,
        event_bus: Any = None,
        workspace: str = "",
    ):
        self._app_def = app_def
        self._on_trigger = on_trigger
        self._event_bus = event_bus
        self._workspace = workspace or str(Path.cwd())
        self._tasks: list[asyncio.Task] = []
        self._running = False
        self._watchers: list[asyncio.Task] = []
        self._expression = ExpressionEngine()

    @property
    def triggers(self) -> list[TriggerDefinition]:
        return self._app_def.triggers

    @property
    def running(self) -> bool:
        return self._running

    async def start(self) -> None:
        """Start all triggers."""
        if self._running:
            return
        self._running = True

        for trigger in self.triggers:
            tid = trigger.id or trigger.type.value
            try:
                if trigger.type == TriggerType.schedule:
                    task = asyncio.create_task(self._run_schedule(trigger))
                    self._tasks.append(task)
                elif trigger.type == TriggerType.watch:
                    task = asyncio.create_task(self._run_watcher(trigger))
                    self._tasks.append(task)
                elif trigger.type == TriggerType.event:
                    await self._subscribe_event(trigger)
                # cli and http triggers are handled externally (by CLI or API server)
                logger.info("Trigger started: %s (%s)", tid, trigger.type.value)
            except Exception:
                logger.exception("Failed to start trigger: %s", tid)

    async def stop(self) -> None:
        """Stop all triggers."""
        self._running = False
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def fire(self, trigger: TriggerDefinition, input_text: str, **metadata: Any) -> Any:
        """Fire a trigger manually (e.g., from CLI or HTTP handler)."""
        event = TriggerEvent(
            trigger_id=trigger.id or trigger.type.value,
            trigger_type=trigger.type.value,
            input_text=self._apply_transform(trigger, input_text, metadata),
            metadata=metadata,
        )

        if not self._check_filters(trigger, event):
            logger.debug("Trigger filtered: %s", event.trigger_id)
            return None

        if self._on_trigger:
            return await self._on_trigger(event)
        return event

    def get_trigger(self, trigger_id: str) -> TriggerDefinition | None:
        """Get a trigger by ID or type."""
        for t in self.triggers:
            if (t.id and t.id == trigger_id) or t.type.value == trigger_id:
                return t
        return None

    def get_cli_trigger(self) -> TriggerDefinition | None:
        """Get the CLI trigger (if any)."""
        for t in self.triggers:
            if t.type == TriggerType.cli:
                return t
        return None

    def get_http_triggers(self) -> list[TriggerDefinition]:
        """Get all HTTP/webhook triggers."""
        return [t for t in self.triggers if t.type in (TriggerType.http, TriggerType.webhook)]

    def get_schedule_triggers(self) -> list[TriggerDefinition]:
        """Get all schedule triggers."""
        return [t for t in self.triggers if t.type == TriggerType.schedule]

    # ─── Schedule ──────────────────────────────────────────────────────

    async def _run_schedule(self, trigger: TriggerDefinition) -> None:
        """Run a cron-like schedule trigger."""
        interval = self._parse_cron_interval(trigger.cron or trigger.when)
        if interval <= 0:
            logger.warning("Invalid schedule for trigger %s", trigger.id)
            return

        while self._running:
            try:
                await asyncio.sleep(interval)
                if not self._running:
                    break
                input_text = trigger.input or f"Scheduled run: {trigger.cron or trigger.when}"
                await self.fire(trigger, input_text)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Schedule trigger error: %s", trigger.id)

    @staticmethod
    def _parse_cron_interval(cron_expr: str) -> float:
        """Parse a simple interval from cron-like or natural language expression.

        Supports:
        - "every 5m", "every 30s", "every 1h", "every 2d"
        - "*/5 * * * *" (every 5 minutes)
        - "0 */2 * * *" (every 2 hours)
        - "0 * * * *" (hourly)
        - "0 0 * * *" (daily)
        - "0 0 * * 0" (weekly)
        - "0 0 1 * *" (monthly, approximated as 30 days)
        """
        expr = cron_expr.strip().lower()

        # Natural language: "every Ns/m/h/d"
        if expr.startswith("every "):
            return _parse_duration(expr[6:].strip())

        # Simple cron patterns: minute hour day_of_month month day_of_week
        parts = expr.split()
        if len(parts) >= 5:
            minute_field = parts[0]
            hour_field = parts[1]
            dom_field = parts[2]
            dow_field = parts[4] if len(parts) > 4 else "*"

            # */N in minute field: every N minutes
            if minute_field.startswith("*/"):
                try:
                    return int(minute_field[2:]) * 60
                except ValueError:
                    pass

            # */N in hour field with fixed minute: every N hours
            if hour_field.startswith("*/"):
                try:
                    return int(hour_field[2:]) * 3600
                except ValueError:
                    pass

            # Fixed minute, wildcard hour: hourly
            if minute_field.isdigit() and hour_field == "*":
                return 3600

            # Fixed minute+hour, wildcard day, specific weekday: weekly
            if minute_field.isdigit() and hour_field.isdigit() and dom_field == "*" and dow_field not in ("*", "?"):
                return 604800  # 7 days

            # Fixed minute+hour, specific day of month: monthly (~30 days)
            if minute_field.isdigit() and hour_field.isdigit() and dom_field.isdigit():
                return 2592000  # 30 days

            # Fixed minute+hour, wildcard everything: daily
            if minute_field.isdigit() and hour_field.isdigit() and dom_field == "*":
                return 86400

        return 0

    # ─── File Watcher ──────────────────────────────────────────────────

    async def _run_watcher(self, trigger: TriggerDefinition) -> None:
        """Watch files/directories for changes.

        Uses the workspace path as the base for glob patterns.
        """
        debounce = _parse_duration(trigger.debounce)
        if debounce <= 0:
            debounce = 2.0

        # Track modification times
        last_mtimes: dict[str, float] = {}
        watch_paths = trigger.paths or []
        base = Path(self._workspace)

        while self._running:
            try:
                await asyncio.sleep(debounce)
                if not self._running:
                    break

                changed_files = []
                for pattern in watch_paths:
                    for path in base.glob(pattern):
                        path_str = str(path)
                        try:
                            mtime = path.stat().st_mtime
                        except OSError:
                            continue
                        if path_str in last_mtimes and mtime > last_mtimes[path_str]:
                            changed_files.append(path_str)
                        last_mtimes[path_str] = mtime

                if changed_files:
                    input_text = f"Files changed: {', '.join(changed_files)}"
                    await self.fire(trigger, input_text, changed_files=changed_files)

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Watch trigger error: %s", trigger.id)

    # ─── Event Subscription ───────────────────────────────────────────

    async def _subscribe_event(self, trigger: TriggerDefinition) -> None:
        """Subscribe to EventBus topics."""
        if not self._event_bus:
            logger.warning("No event bus available for event trigger: %s", trigger.id)
            return

        topic = trigger.topic
        if not topic:
            return

        async def handler(event_data: dict[str, Any]) -> None:
            input_text = str(event_data.get("data", event_data))
            await self.fire(trigger, input_text, event=event_data)

        if hasattr(self._event_bus, "subscribe"):
            await self._event_bus.subscribe(topic, handler)

    # ─── Helpers ───────────────────────────────────────────────────────

    def _apply_transform(
        self, trigger: TriggerDefinition, input_text: str, metadata: dict[str, Any]
    ) -> str:
        """Apply transform template using ExpressionEngine."""
        if not trigger.transform:
            return input_text
        try:
            ctx = ExpressionContext(
                variables={
                    "input": input_text,
                    "payload": input_text,
                    "trigger": {
                        "input": input_text,
                        "type": trigger.type.value,
                        **metadata,
                    },
                    **metadata,
                }
            )
            result = self._expression.resolve(trigger.transform, ctx)
            return str(result) if result is not None else input_text
        except Exception:
            logger.debug("Transform failed, using fallback", exc_info=True)
            # Fallback: simple str.replace for backwards compatibility
            result = trigger.transform
            result = result.replace("{{payload}}", input_text)
            result = result.replace("{{input}}", input_text)
            for key, val in metadata.items():
                result = result.replace(f"{{{{{key}}}}}", str(val))
            return result

    def _check_filters(self, trigger: TriggerDefinition, event: TriggerEvent) -> bool:
        """Check if the event passes trigger filters.

        Filters can be:
        - Expression conditions (evaluated via ExpressionEngine)
        - Glob patterns on input text (fnmatch fallback)
        """
        if not trigger.filters:
            return True

        ctx = ExpressionContext(
            variables={
                "input": event.input_text,
                "payload": event.input_text,
                "trigger": {
                    "input": event.input_text,
                    "type": event.trigger_type,
                    **event.metadata,
                },
                **event.metadata,
            }
        )

        for f in trigger.filters:
            try:
                # Try as expression first
                if "{{" in f or "==" in f or "!=" in f:
                    if self._expression.evaluate_condition(f, ctx):
                        return True
                    continue
            except Exception:
                pass
            # Fallback: fnmatch glob pattern
            if fnmatch.fnmatch(event.input_text, f):
                return True

        return False


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
