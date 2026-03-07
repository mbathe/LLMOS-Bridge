"""Memory module — pluggable, multi-backend memory system.

Provides a BaseModule with pluggable memory backends (sub-modules).
Built-in backends: kv, vector, file, cognitive.
Users can register custom backends at runtime.
"""

from llmos_bridge.modules.memory.module import MemoryModule

__all__ = ["MemoryModule"]
