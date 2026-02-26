"""Module layer — BaseModule interface, registry, and manifest system."""

from llmos_bridge.modules.base import ActionResult, BaseModule, ExecutionContext
from llmos_bridge.modules.manifest import ActionSpec, ModuleManifest
from llmos_bridge.modules.registry import ModuleRegistry

__all__ = [
    "BaseModule",
    "ExecutionContext",
    "ActionResult",
    "ModuleManifest",
    "ActionSpec",
    "ModuleRegistry",
]
