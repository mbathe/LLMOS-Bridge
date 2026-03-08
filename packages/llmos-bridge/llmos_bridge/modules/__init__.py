"""Module layer — BaseModule interface, registry, manifest system, and cache decorators."""

from llmos_bridge.cache import cacheable, invalidates_cache
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
    # Cache decorators — re-exported for convenience in community modules
    "cacheable",
    "invalidates_cache",
]
