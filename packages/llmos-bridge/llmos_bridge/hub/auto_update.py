"""Auto-update checker — periodic background task for detecting module updates.

Runs as an asyncio background task, polling the hub at a configurable
interval. When updates are found, publishes to the EventBus on the
``llmos.modules`` topic.
"""

from __future__ import annotations

import asyncio
from typing import Any, TYPE_CHECKING

from llmos_bridge.logging import get_logger

if TYPE_CHECKING:
    from llmos_bridge.events.bus import EventBus
    from llmos_bridge.hub.client import HubClient
    from llmos_bridge.hub.index import ModuleIndex

log = get_logger(__name__)


class AutoUpdateChecker:
    """Periodically checks the hub for module updates."""

    def __init__(
        self,
        hub_client: HubClient,
        module_index: ModuleIndex,
        *,
        event_bus: EventBus | None = None,
        check_interval: float = 3600.0,
    ) -> None:
        self._hub_client = hub_client
        self._module_index = module_index
        self._event_bus = event_bus
        self._check_interval = max(300.0, min(check_interval, 86400.0))
        self._task: asyncio.Task | None = None
        self._available_updates: list[dict] = []

    @property
    def available_updates(self) -> list[dict]:
        """Return the last known list of available updates."""
        return list(self._available_updates)

    async def start(self) -> None:
        """Start the background update checker task."""
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run_loop())
        log.info("auto_update_checker_started", interval=self._check_interval)

    async def stop(self) -> None:
        """Stop the background task."""
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
            log.info("auto_update_checker_stopped")

    async def check_now(self) -> list[dict]:
        """Run an immediate update check (does not wait for the next interval)."""
        try:
            installed = await self._get_installed_versions()
            if not installed:
                return []

            updates = await self._hub_client.check_updates(installed)
            self._available_updates = updates

            if updates and self._event_bus is not None:
                await self._event_bus.publish(
                    "llmos.modules",
                    {
                        "event": "updates_available",
                        "count": len(updates),
                        "updates": updates,
                    },
                )

            if updates:
                log.info("updates_available", count=len(updates))
            return updates

        except Exception as exc:
            log.warning("update_check_failed", error=str(exc))
            return []

    async def _run_loop(self) -> None:
        """Background loop — check periodically."""
        try:
            while True:
                await self.check_now()
                await asyncio.sleep(self._check_interval)
        except asyncio.CancelledError:
            pass

    async def _get_installed_versions(self) -> dict[str, str]:
        """Get all installed community modules as {module_id: version}."""
        modules = await self._module_index.list_all()
        return {m.module_id: m.version for m in modules if m.enabled}
