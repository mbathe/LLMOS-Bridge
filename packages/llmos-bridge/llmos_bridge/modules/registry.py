"""Module layer — Module registry.

The registry is the single point of truth for all loaded modules.
It handles:
  - Module registration and discovery
  - Platform compatibility checks via :class:`PlatformGuard`
  - Graceful degradation when a module cannot load (missing deps, wrong platform)
  - Lazy loading (modules are instantiated on first access)

Platform guard integration:
  When ``platform_guard`` is provided (or auto-detected), the registry checks
  module platform compatibility at registration time.  Incompatible modules
  are added to ``_platform_excluded`` rather than ``_failed`` so they can be
  reported separately in the health endpoint.  This distinction matters:
    - ``_failed``: module failed to load due to a runtime error (missing dep,
      import error, etc.)
    - ``_platform_excluded``: module is intentionally unavailable on this
      platform (e.g. IoT on Windows)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Type

from llmos_bridge.exceptions import ModuleLoadError, ModuleNotFoundError
from llmos_bridge.logging import get_logger
from llmos_bridge.modules.manifest import ModuleManifest

if TYPE_CHECKING:
    from llmos_bridge.modules.base import BaseModule
    from llmos_bridge.modules.platform import PlatformGuard

log = get_logger(__name__)


class ModuleRegistry:
    """Runtime registry for LLMOS Bridge modules.

    Usage::

        from llmos_bridge.modules.platform import PlatformGuard
        registry = ModuleRegistry(platform_guard=PlatformGuard())
        registry.register(FilesystemModule)
        registry.register(IoTModule)   # auto-excluded on non-Pi platforms

        module = registry.get("filesystem")
        result = await module.execute("read_file", {"path": "/tmp/test.txt"})
    """

    def __init__(self, platform_guard: "PlatformGuard | None" = None) -> None:
        self._classes: dict[str, Type["BaseModule"]] = {}
        self._instances: dict[str, "BaseModule"] = {}
        # Modules that failed due to runtime errors (missing deps, etc.)
        self._failed: dict[str, str] = {}
        # Modules excluded because the current platform is not supported.
        self._platform_excluded: dict[str, str] = {}
        self._guard = platform_guard

    def register(self, module_class: Type["BaseModule"]) -> None:
        """Register a module class.  Instantiation is deferred until first use.

        If a ``platform_guard`` is configured, modules incompatible with the
        current platform are recorded in ``_platform_excluded`` rather than
        ``_classes`` so they never attempt to load.
        """
        module_id = module_class.MODULE_ID
        if not module_id:
            raise ValueError(f"Module class {module_class.__name__} has no MODULE_ID.")

        # Platform compatibility check.
        if self._guard is not None and not self._guard.is_compatible(module_class):
            reason = (
                f"Platform '{self._guard.platform_info.os_type.value}' is not in "
                f"SUPPORTED_PLATFORMS {[p.value for p in module_class.SUPPORTED_PLATFORMS]}."
            )
            self._platform_excluded[module_id] = reason
            log.info(
                "module_platform_excluded",
                module_id=module_id,
                reason=reason,
            )
            return

        if module_id in self._classes:
            log.warning("module_already_registered", module_id=module_id)

        self._classes[module_id] = module_class
        log.debug("module_registered", module_id=module_id, version=module_class.VERSION)

    def get(self, module_id: str) -> "BaseModule":
        """Return the module instance for *module_id*.

        Raises:
            ModuleNotFoundError: No module with this ID is registered.
            ModuleLoadError:     The module failed to instantiate or is excluded.
        """
        if module_id in self._platform_excluded:
            raise ModuleLoadError(
                module_id=module_id,
                reason=self._platform_excluded[module_id],
            )
        if module_id in self._failed:
            raise ModuleLoadError(module_id=module_id, reason=self._failed[module_id])

        if module_id not in self._instances:
            self._instances[module_id] = self._instantiate(module_id)

        return self._instances[module_id]

    def _instantiate(self, module_id: str) -> "BaseModule":
        if module_id not in self._classes:
            raise ModuleNotFoundError(module_id=module_id)

        module_class = self._classes[module_id]
        try:
            instance = module_class()
            log.info("module_loaded", module_id=module_id, version=module_class.VERSION)
            return instance
        except Exception as exc:
            reason = str(exc)
            self._failed[module_id] = reason
            log.error("module_load_failed", module_id=module_id, reason=reason)
            raise ModuleLoadError(module_id=module_id, reason=reason) from exc

    def register_instance(self, instance: "BaseModule") -> None:
        """Register a pre-constructed module instance directly.

        Useful for modules that need dependency injection before registration
        (e.g. SecurityModule, RecordingModule).
        """
        module_id = instance.MODULE_ID
        if not module_id:
            raise ValueError(f"Module instance {type(instance).__name__} has no MODULE_ID.")
        self._classes[module_id] = type(instance)
        self._instances[module_id] = instance
        log.debug("module_instance_registered", module_id=module_id, version=instance.VERSION)

    def is_available(self, module_id: str) -> bool:
        """Return True if the module is registered and can be instantiated."""
        if module_id in self._failed or module_id in self._platform_excluded:
            return False
        if module_id not in self._classes:
            return False
        try:
            self.get(module_id)
            return True
        except (ModuleNotFoundError, ModuleLoadError):
            return False

    def list_modules(self) -> list[str]:
        """Return IDs of all registered modules (including failed and excluded)."""
        all_ids = set(self._classes.keys()) | set(self._platform_excluded.keys())
        return sorted(all_ids)

    def list_available(self) -> list[str]:
        """Return IDs of modules that loaded successfully."""
        return [
            mid for mid in self._classes
            if mid not in self._failed and mid not in self._platform_excluded
        ]

    def list_failed(self) -> dict[str, str]:
        """Return module_id → reason for modules that failed to load at runtime."""
        return dict(self._failed)

    def list_platform_excluded(self) -> dict[str, str]:
        """Return module_id → reason for modules excluded due to platform mismatch."""
        return dict(self._platform_excluded)

    def get_manifest(self, module_id: str) -> ModuleManifest:
        return self.get(module_id).get_manifest()

    def all_manifests(self) -> list[ModuleManifest]:
        manifests = []
        for module_id in self.list_available():
            try:
                manifests.append(self.get_manifest(module_id))
            except Exception as exc:
                log.warning("manifest_fetch_failed", module_id=module_id, error=str(exc))
        return manifests

    def unregister(self, module_id: str) -> None:
        """Remove a module from the registry (used in tests)."""
        self._classes.pop(module_id, None)
        self._instances.pop(module_id, None)
        self._failed.pop(module_id, None)
        self._platform_excluded.pop(module_id, None)

    def status_report(self) -> dict[str, dict[str, str | list[str]]]:
        """Return a structured status report for the health endpoint.

        Schema::

            {
                "available": ["filesystem", "os_exec"],
                "failed": {"browser": "playwright not installed"},
                "platform_excluded": {"iot": "Platform 'linux' not in [raspberry_pi]"}
            }
        """
        return {
            "available": self.list_available(),
            "failed": self.list_failed(),
            "platform_excluded": self.list_platform_excluded(),
        }
