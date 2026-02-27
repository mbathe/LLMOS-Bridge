"""Security layer — Permission constants, risk levels, and data models.

Defines the permission system's core types:
  - ``Permission``       — well-known resource permission identifiers (string constants)
  - ``RiskLevel``        — LOW / MEDIUM / HIGH / CRITICAL classification
  - ``DataClassification`` — data sensitivity classification
  - ``PermissionScope``  — SESSION (cleared on restart) / PERMANENT (persists)
  - ``PermissionGrant``  — frozen record of a granted permission
  - ``PERMISSION_RISK``  — default risk level for each well-known permission

Community modules can define custom permission strings using any dotted-name
convention (e.g. ``"mymodule.special_resource"``).  The built-in ``Permission``
class only provides constants for discoverability and IDE auto-complete.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class RiskLevel(str, Enum):
    """Risk classification for actions and permissions."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class DataClassification(str, Enum):
    """Data sensitivity classification (inspired by ISO 27001)."""

    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class PermissionScope(str, Enum):
    """Scope of a permission grant."""

    SESSION = "session"  # Cleared on daemon restart
    PERMANENT = "permanent"  # Persists across restarts


# ---------------------------------------------------------------------------
# Permission grant record
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PermissionGrant:
    """Immutable record of a granted permission."""

    permission: str
    module_id: str
    scope: PermissionScope
    granted_at: float = field(default_factory=time.time)
    granted_by: str = "user"
    reason: str = ""
    expires_at: float | None = None

    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return time.time() > self.expires_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "permission": self.permission,
            "module_id": self.module_id,
            "scope": self.scope.value,
            "granted_at": self.granted_at,
            "granted_by": self.granted_by,
            "reason": self.reason,
            "expires_at": self.expires_at,
        }


# ---------------------------------------------------------------------------
# Well-known permission identifiers
# ---------------------------------------------------------------------------


class Permission:
    """Well-known permission identifiers.

    These are plain string constants — **not** an enum — so that community
    modules can freely define custom permission strings without subclassing.

    Naming convention: ``category.resource[.sub]``

    Usage in decorators::

        @requires_permission(Permission.FILESYSTEM_WRITE, reason="Writes to disk")
        async def _action_write_file(self, params): ...

    Community modules::

        @requires_permission("my_plugin.special_sensor", reason="Reads sensor data")
        async def _action_read_sensor(self, params): ...
    """

    # -- Filesystem ---------------------------------------------------------
    FILESYSTEM_READ = "filesystem.read"
    FILESYSTEM_WRITE = "filesystem.write"
    FILESYSTEM_DELETE = "filesystem.delete"
    FILESYSTEM_SENSITIVE = "filesystem.sensitive"

    # -- Device / Peripherals -----------------------------------------------
    CAMERA = "device.camera"
    MICROPHONE = "device.microphone"
    SCREEN_CAPTURE = "device.screen"
    KEYBOARD = "device.keyboard"

    # -- Network ------------------------------------------------------------
    NETWORK_READ = "network.read"
    NETWORK_SEND = "network.send"
    NETWORK_EXTERNAL = "network.external"

    # -- Data ---------------------------------------------------------------
    DATABASE_READ = "data.database.read"
    DATABASE_WRITE = "data.database.write"
    DATABASE_DELETE = "data.database.delete"
    CREDENTIALS = "data.credentials"
    PERSONAL_DATA = "data.personal"

    # -- Operating System ---------------------------------------------------
    PROCESS_EXECUTE = "os.process.execute"
    PROCESS_KILL = "os.process.kill"
    ADMIN = "os.admin"

    # -- Applications -------------------------------------------------------
    BROWSER = "app.browser"
    EMAIL_READ = "app.email.read"
    EMAIL_SEND = "app.email.send"

    # -- IoT ----------------------------------------------------------------
    GPIO_READ = "iot.gpio.read"
    GPIO_WRITE = "iot.gpio.write"
    SENSOR = "iot.sensor"
    ACTUATOR = "iot.actuator"


# ---------------------------------------------------------------------------
# Default risk levels for well-known permissions
# ---------------------------------------------------------------------------

PERMISSION_RISK: dict[str, RiskLevel] = {
    # Filesystem
    Permission.FILESYSTEM_READ: RiskLevel.LOW,
    Permission.FILESYSTEM_WRITE: RiskLevel.MEDIUM,
    Permission.FILESYSTEM_DELETE: RiskLevel.HIGH,
    Permission.FILESYSTEM_SENSITIVE: RiskLevel.CRITICAL,
    # Device
    Permission.CAMERA: RiskLevel.HIGH,
    Permission.MICROPHONE: RiskLevel.HIGH,
    Permission.SCREEN_CAPTURE: RiskLevel.MEDIUM,
    Permission.KEYBOARD: RiskLevel.CRITICAL,
    # Network
    Permission.NETWORK_READ: RiskLevel.LOW,
    Permission.NETWORK_SEND: RiskLevel.MEDIUM,
    Permission.NETWORK_EXTERNAL: RiskLevel.MEDIUM,
    # Data
    Permission.DATABASE_READ: RiskLevel.LOW,
    Permission.DATABASE_WRITE: RiskLevel.MEDIUM,
    Permission.DATABASE_DELETE: RiskLevel.HIGH,
    Permission.CREDENTIALS: RiskLevel.CRITICAL,
    Permission.PERSONAL_DATA: RiskLevel.HIGH,
    # OS
    Permission.PROCESS_EXECUTE: RiskLevel.MEDIUM,
    Permission.PROCESS_KILL: RiskLevel.HIGH,
    Permission.ADMIN: RiskLevel.CRITICAL,
    # Apps
    Permission.BROWSER: RiskLevel.MEDIUM,
    Permission.EMAIL_READ: RiskLevel.MEDIUM,
    Permission.EMAIL_SEND: RiskLevel.HIGH,
    # IoT
    Permission.GPIO_READ: RiskLevel.LOW,
    Permission.GPIO_WRITE: RiskLevel.MEDIUM,
    Permission.SENSOR: RiskLevel.LOW,
    Permission.ACTUATOR: RiskLevel.HIGH,
}
