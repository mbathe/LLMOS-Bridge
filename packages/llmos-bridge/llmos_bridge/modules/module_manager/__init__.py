"""Module Manager — system module for runtime module governance.

Provides IML-callable actions for listing, enabling, disabling, pausing,
resuming, and introspecting all registered modules.  This is the central
control plane for the module system.
"""

from llmos_bridge.modules.module_manager.module import ModuleManagerModule

__all__ = ["ModuleManagerModule"]
