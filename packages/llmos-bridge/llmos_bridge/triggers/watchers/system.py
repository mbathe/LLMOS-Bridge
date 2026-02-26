"""System trigger watchers — filesystem, process, resource.

FileSystemWatcher   — fires on file/directory changes (inotify via watchfiles)
ProcessWatcher      — fires when a named process starts or stops
ResourceWatcher     — fires when CPU/RAM/disk exceeds a threshold

External dependencies
---------------------
FileSystemWatcher  requires ``watchfiles`` (optional) — ``pip install watchfiles``
ProcessWatcher     requires ``psutil`` (already required for os_exec module)
ResourceWatcher    requires ``psutil`` (already required)
"""

from __future__ import annotations

import asyncio
import fnmatch
import time
from typing import Any

from llmos_bridge.logging import get_logger
from llmos_bridge.triggers.models import TriggerCondition
from llmos_bridge.triggers.watchers.base import BaseWatcher, FireCallback

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# FileSystemWatcher
# ---------------------------------------------------------------------------


class FileSystemWatcher(BaseWatcher):
    """Fires when files under ``path`` are created, modified, or deleted.

    Condition params::

        {
            "path": "/home/user/documents",  # required: absolute path to watch
            "recursive": true,               # watch subdirectories (default False)
            "events": ["created", "modified", "deleted"]  # subset to watch
        }

    Requires ``watchfiles``::

        pip install watchfiles

    The payload emitted on fire::

        {
            "path":   "/home/user/documents/report.docx",
            "change": "modified",    # "created" | "modified" | "deleted"
            "watch_root": "/home/user/documents",
        }
    """

    _CHANGE_MAP = {1: "added", 2: "modified", 3: "deleted"}

    def __init__(
        self,
        trigger_id: str,
        condition: TriggerCondition,
        fire_callback: FireCallback,
    ) -> None:
        super().__init__(trigger_id, condition, fire_callback)
        params = condition.params
        self._path: str = params["path"]
        self._recursive: bool = bool(params.get("recursive", False))
        raw_events: list[str] = params.get("events", ["created", "modified", "deleted"])
        self._watch_events = set(raw_events)

    async def _run(self) -> None:
        try:
            from watchfiles import awatch, Change  # type: ignore[import]
        except ImportError:
            self.error = "watchfiles package not installed. Run: pip install watchfiles"
            log.error("fs_watcher_missing_dep", trigger_id=self._trigger_id)
            return

        log.debug("fs_watcher_started", trigger_id=self._trigger_id, path=self._path)
        try:
            async for changes in awatch(self._path, stop_event=self._stop_event, recursive=self._recursive):
                if self._stopped:
                    return
                for change_type, file_path in changes:
                    change_name = self._CHANGE_MAP.get(change_type.value, "modified")
                    if change_name in self._watch_events or "modified" in self._watch_events:
                        await self._fire(
                            "filesystem.changed",
                            {
                                "path": file_path,
                                "change": change_name,
                                "watch_root": self._path,
                            },
                        )
        except Exception as exc:
            if not self._stopped:
                self.error = str(exc)
                log.error("fs_watcher_error", trigger_id=self._trigger_id, error=str(exc))


# ---------------------------------------------------------------------------
# ProcessWatcher
# ---------------------------------------------------------------------------


class ProcessWatcher(BaseWatcher):
    """Fires when a process matching ``name`` starts or stops.

    Condition params::

        {
            "name": "firefox",    # process name (fnmatch pattern)
            "event": "started"    # "started" | "stopped" | "crashed"
        }

    Polls ``psutil.process_iter()`` every ``poll_interval_seconds`` (default 2).

    Payload on fire::

        {
            "pid":  1234,
            "name": "firefox",
            "event": "started",
        }
    """

    def __init__(
        self,
        trigger_id: str,
        condition: TriggerCondition,
        fire_callback: FireCallback,
    ) -> None:
        super().__init__(trigger_id, condition, fire_callback)
        params = condition.params
        self._name_pattern: str = params["name"]
        self._watch_event: str = params.get("event", "started")
        self._poll_interval: float = float(params.get("poll_interval_seconds", 2.0))
        self._known_pids: set[int] = set()

    async def _run(self) -> None:
        try:
            import psutil  # type: ignore[import]
        except ImportError:
            self.error = "psutil not installed"
            log.error("process_watcher_missing_dep", trigger_id=self._trigger_id)
            return

        log.debug("process_watcher_started", trigger_id=self._trigger_id, name=self._name_pattern)
        # Seed initial state — don't fire for processes that already exist
        self._known_pids = self._current_matching_pids(psutil)

        while not self._stopped:
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self._poll_interval
                )
                return
            except asyncio.TimeoutError:
                pass

            current_pids = self._current_matching_pids(psutil)
            appeared = current_pids - self._known_pids
            disappeared = self._known_pids - current_pids

            if self._watch_event in ("started",) and appeared:
                for pid in appeared:
                    await self._fire(
                        "process.started",
                        {"pid": pid, "name": self._name_pattern, "event": "started"},
                    )
            if self._watch_event in ("stopped", "crashed") and disappeared:
                for pid in disappeared:
                    await self._fire(
                        "process.stopped",
                        {"pid": pid, "name": self._name_pattern, "event": "stopped"},
                    )
            self._known_pids = current_pids

    def _current_matching_pids(self, psutil: Any) -> set[int]:
        pids: set[int] = set()
        try:
            for proc in psutil.process_iter(["pid", "name"]):
                try:
                    if fnmatch.fnmatch(proc.info["name"] or "", self._name_pattern):
                        pids.add(proc.info["pid"])
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except Exception:
            pass
        return pids


# ---------------------------------------------------------------------------
# ResourceWatcher
# ---------------------------------------------------------------------------


class ResourceWatcher(BaseWatcher):
    """Fires when a system resource metric exceeds a threshold.

    Condition params::

        {
            "metric":           "cpu_percent",  # "cpu_percent" | "memory_percent" | "disk_percent"
            "threshold":        90.0,           # trigger when metric > threshold
            "duration_seconds": 30.0,           # metric must stay high for this long (default 0)
            "disk_path":        "/",            # for disk_percent only
            "poll_interval_seconds": 5.0        # how often to sample (default 5)
        }

    Payload on fire::

        {
            "metric": "cpu_percent",
            "value":  95.3,
            "threshold": 90.0,
            "duration_seconds": 30.0,
        }
    """

    def __init__(
        self,
        trigger_id: str,
        condition: TriggerCondition,
        fire_callback: FireCallback,
    ) -> None:
        super().__init__(trigger_id, condition, fire_callback)
        params = condition.params
        self._metric: str = params.get("metric", "cpu_percent")
        self._threshold: float = float(params.get("threshold", 80.0))
        self._duration: float = float(params.get("duration_seconds", 0.0))
        self._disk_path: str = params.get("disk_path", "/")
        self._poll: float = float(params.get("poll_interval_seconds", 5.0))
        self._above_since: float | None = None  # timestamp when metric first crossed threshold

    async def _run(self) -> None:
        try:
            import psutil  # type: ignore[import]
        except ImportError:
            self.error = "psutil not installed"
            log.error("resource_watcher_missing_dep", trigger_id=self._trigger_id)
            return

        log.debug("resource_watcher_started", trigger_id=self._trigger_id, metric=self._metric, threshold=self._threshold)
        while not self._stopped:
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._poll)
                return
            except asyncio.TimeoutError:
                pass

            value = self._sample(psutil)
            if value is None:
                continue

            if value > self._threshold:
                if self._above_since is None:
                    self._above_since = time.time()
                elapsed = time.time() - self._above_since
                if elapsed >= self._duration:
                    await self._fire(
                        "resource.threshold_exceeded",
                        {
                            "metric": self._metric,
                            "value": value,
                            "threshold": self._threshold,
                            "duration_seconds": elapsed,
                        },
                    )
                    self._above_since = None  # re-arm after one fire
            else:
                self._above_since = None  # reset if metric drops below threshold

    def _sample(self, psutil: Any) -> float | None:
        try:
            if self._metric == "cpu_percent":
                return psutil.cpu_percent(interval=None)
            if self._metric == "memory_percent":
                return psutil.virtual_memory().percent
            if self._metric == "disk_percent":
                return psutil.disk_usage(self._disk_path).percent
        except Exception as exc:
            log.warning("resource_watcher_sample_error", metric=self._metric, error=str(exc))
        return None
