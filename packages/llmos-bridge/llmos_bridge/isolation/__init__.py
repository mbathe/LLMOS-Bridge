"""Module isolation layer — Subprocess workers with JSON-RPC.

Provides dependency isolation for modules with conflicting requirements.
Every module that declares external dependencies gets full process + venv
isolation by default (subprocess tier).  Lightweight modules can opt into
in_process mode as a performance optimisation.

Architecture (Grafana/HashiCorp pattern):

    Host process (FastAPI daemon)
      ├── In-process modules  (filesystem, os_exec — no external deps)
      └── IsolatedModuleProxy ──stdin/stdout──> Worker subprocess
            (BaseModule impl)                   (own venv, own deps)

Usage::

    from llmos_bridge.isolation import IsolatedModuleProxy, VenvManager

    venv_mgr = VenvManager()
    proxy = IsolatedModuleProxy(
        module_id="vision",
        module_class_path="llmos_bridge.modules.perception_vision.omniparser.module:OmniParserModule",
        venv_manager=venv_mgr,
        requirements=["torch>=2.2", "transformers>=5.0"],
    )
    registry.register_instance(proxy)   # transparent to callers
"""

from llmos_bridge.isolation.health import HealthMonitor
from llmos_bridge.isolation.protocol import (
    JsonRpcError,
    JsonRpcNotification,
    JsonRpcRequest,
    JsonRpcResponse,
)
from llmos_bridge.isolation.proxy import IsolatedModuleProxy
from llmos_bridge.isolation.venv_manager import VenvManager

__all__ = [
    "HealthMonitor",
    "IsolatedModuleProxy",
    "JsonRpcError",
    "JsonRpcNotification",
    "JsonRpcRequest",
    "JsonRpcResponse",
    "VenvManager",
]
