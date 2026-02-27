"""Security scanners — registry for managing scanner lifecycle.

Follows the ``ThreatCategoryRegistry`` pattern with register/unregister/
enable/disable/list operations and an on_change callback.

Usage::

    registry = ScannerRegistry()
    registry.register(HeuristicScanner())
    registry.register(LLMGuardScanner())

    # Disable at runtime
    registry.disable("llm_guard")

    # List enabled, sorted by priority
    for scanner in registry.list_enabled():
        result = await scanner.scan(text)
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from llmos_bridge.logging import get_logger
from llmos_bridge.security.scanners.base import InputScanner

log = get_logger(__name__)


class ScannerRegistry:
    """Registry of all input scanners (built-in + community plugins).

    Thread-safe for read operations.  Write operations (register/unregister)
    are expected to happen at startup or via the REST API with low contention.
    """

    def __init__(self) -> None:
        self._scanners: dict[str, InputScanner] = {}
        self._enabled: dict[str, bool] = {}
        self._on_change: Callable[[], None] | None = None

    def set_on_change(self, callback: Callable[[], None] | None) -> None:
        """Set a callback invoked on every mutation."""
        self._on_change = callback

    def _notify(self) -> None:
        if self._on_change is not None:
            self._on_change()

    def register(self, scanner: InputScanner) -> None:
        """Register a scanner instance.  Overwrites if scanner_id exists."""
        self._scanners[scanner.scanner_id] = scanner
        self._enabled.setdefault(scanner.scanner_id, True)
        log.info(
            "scanner_registered",
            scanner_id=scanner.scanner_id,
            priority=scanner.priority,
        )
        self._notify()

    def unregister(self, scanner_id: str) -> bool:
        """Remove a scanner.  Returns True if removed."""
        removed = self._scanners.pop(scanner_id, None) is not None
        self._enabled.pop(scanner_id, None)
        if removed:
            log.info("scanner_unregistered", scanner_id=scanner_id)
            self._notify()
        return removed

    def get(self, scanner_id: str) -> InputScanner | None:
        return self._scanners.get(scanner_id)

    def enable(self, scanner_id: str) -> bool:
        """Enable a scanner.  Returns True if found."""
        if scanner_id in self._scanners:
            self._enabled[scanner_id] = True
            self._notify()
            return True
        return False

    def disable(self, scanner_id: str) -> bool:
        """Disable a scanner.  Returns True if found."""
        if scanner_id in self._scanners:
            self._enabled[scanner_id] = False
            self._notify()
            return True
        return False

    def is_enabled(self, scanner_id: str) -> bool:
        return self._enabled.get(scanner_id, False)

    def list_all(self) -> list[InputScanner]:
        """All registered scanners, sorted by priority (ascending)."""
        return sorted(self._scanners.values(), key=lambda s: s.priority)

    def list_enabled(self) -> list[InputScanner]:
        """Enabled scanners only, sorted by priority (ascending)."""
        return sorted(
            (
                s
                for s in self._scanners.values()
                if self._enabled.get(s.scanner_id, False)
            ),
            key=lambda s: s.priority,
        )

    def to_dict_list(self) -> list[dict[str, Any]]:
        """Serialise all scanners for REST API."""
        result = []
        for scanner in self.list_all():
            info = scanner.status()
            info["enabled"] = self._enabled.get(scanner.scanner_id, False)
            result.append(info)
        return result

    async def close_all(self) -> None:
        """Release resources for all registered scanners."""
        for scanner in self._scanners.values():
            try:
                await scanner.close()
            except Exception as exc:
                log.warning(
                    "scanner_close_failed",
                    scanner_id=scanner.scanner_id,
                    error=str(exc),
                )
