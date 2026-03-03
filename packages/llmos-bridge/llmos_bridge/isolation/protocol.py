"""JSON-RPC 2.0 protocol for host ↔ worker communication.

Messages are single JSON lines over stdin/stdout.  stderr is reserved
for worker logging (forwarded to host structlog).

Standard methods:
    initialize   — host → worker: send env vars, worker loads module
    execute      — host → worker: run an action, return result
    get_manifest — host → worker: return ModuleManifest as dict
    health_check — host → worker: ping, returns uptime
    shutdown     — host → worker: graceful exit

Notifications (no response expected):
    worker.ready — worker → host: emitted after process starts
    worker.log   — worker → host: structured log entry
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# JSON-RPC error codes
# ---------------------------------------------------------------------------

# Standard JSON-RPC 2.0 errors.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# LLMOS-specific application errors (-32000 to -32099).
MODULE_LOAD_ERROR = -32001
ACTION_NOT_FOUND_ERROR = -32002
ACTION_EXECUTION_ERROR = -32003
PERMISSION_ERROR = -32004
TIMEOUT_ERROR = -32005


# ---------------------------------------------------------------------------
# Message types
# ---------------------------------------------------------------------------


class JsonRpcError(BaseModel):
    """JSON-RPC 2.0 error object."""

    code: int
    message: str
    data: dict[str, Any] | None = None


class JsonRpcRequest(BaseModel):
    """JSON-RPC 2.0 request (host → worker)."""

    jsonrpc: Literal["2.0"] = "2.0"
    method: str
    params: dict[str, Any] = Field(default_factory=dict)
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])


class JsonRpcResponse(BaseModel):
    """JSON-RPC 2.0 response (worker → host)."""

    jsonrpc: Literal["2.0"] = "2.0"
    result: Any = None
    error: JsonRpcError | None = None
    id: str


class JsonRpcNotification(BaseModel):
    """JSON-RPC 2.0 notification (no id, no response expected)."""

    jsonrpc: Literal["2.0"] = "2.0"
    method: str
    params: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# RPC method constants
# ---------------------------------------------------------------------------

METHOD_INITIALIZE = "initialize"
METHOD_EXECUTE = "execute"
METHOD_GET_MANIFEST = "get_manifest"
METHOD_HEALTH_CHECK = "health_check"
METHOD_SHUTDOWN = "shutdown"

NOTIFICATION_WORKER_READY = "worker.ready"
NOTIFICATION_WORKER_LOG = "worker.log"

# Binary data sentinel key — screenshot bytes are base64-encoded under
# this key in params, decoded back to bytes by the worker.
BINARY_SCREENSHOT_KEY = "_screenshot_bytes_b64"
