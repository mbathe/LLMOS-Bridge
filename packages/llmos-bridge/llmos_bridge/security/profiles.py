"""Security layer — Permission profiles.

Four built-in profiles (from least to most permissive):

    readonly      Read-only access to filesystem and system info.
    local_worker  Default. Can read/write files, run safe commands, call APIs.
    power_user    Includes browser, GUI automation, database writes.
    unrestricted  Full access. All actions allowed. Use with caution.

Each profile is expressed as a set of allowed ``module.action`` patterns.
A ``*`` wildcard matches any single segment.
``module.*`` allows all actions in a module.
``*.*`` allows everything (unrestricted).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from fnmatch import fnmatch


class PermissionProfile(str, Enum):
    READONLY = "readonly"
    LOCAL_WORKER = "local_worker"
    POWER_USER = "power_user"
    UNRESTRICTED = "unrestricted"


@dataclass(frozen=True)
class PermissionProfileConfig:
    """Resolved permission configuration for a profile."""

    profile: PermissionProfile
    allowed_patterns: frozenset[str]
    denied_patterns: frozenset[str] = field(default_factory=frozenset)
    max_plan_actions: int = 50
    allow_env_templates: bool = True
    allow_approval_bypass: bool = False

    def is_allowed(self, module_id: str, action_name: str) -> bool:
        """Return True if ``module_id.action_name`` is permitted."""
        key = f"{module_id}.{action_name}"
        for pattern in self.denied_patterns:
            if fnmatch(key, pattern):
                return False
        for pattern in self.allowed_patterns:
            if fnmatch(key, pattern):
                return True
        return False


# ---------------------------------------------------------------------------
# Built-in profile definitions
# ---------------------------------------------------------------------------

_READONLY_ALLOWED: frozenset[str] = frozenset(
    [
        "filesystem.read_file",
        "filesystem.list_directory",
        "filesystem.search_files",
        "filesystem.get_file_info",
        "filesystem.compute_checksum",
        "os_exec.list_processes",
        "os_exec.get_process_info",
        "os_exec.get_system_info",
        "os_exec.get_env_var",
        "database.connect",
        "database.disconnect",
        "database.fetch_results",
        "database.list_tables",
        "database.get_table_schema",
        # db_gateway — read-only operations
        "db_gateway.connect",
        "db_gateway.disconnect",
        "db_gateway.introspect",
        "db_gateway.find",
        "db_gateway.find_one",
        "db_gateway.count",
        "db_gateway.search",
        "db_gateway.aggregate",
    ]
)

_LOCAL_WORKER_ALLOWED: frozenset[str] = _READONLY_ALLOWED | frozenset(
    [
        "filesystem.write_file",
        "filesystem.append_file",
        "filesystem.copy_file",
        "filesystem.move_file",
        "filesystem.create_directory",
        "filesystem.create_archive",
        "filesystem.extract_archive",
        "filesystem.watch_path",
        "os_exec.run_command",
        "os_exec.open_application",
        "os_exec.set_env_var",
        "excel.*",
        "word.*",
        "api_http.http_get",
        "api_http.http_post",
        "api_http.http_put",
        "api_http.http_patch",
        "api_http.http_delete",
        "api_http.download_file",
        "api_http.webhook_trigger",
        "database.execute_query",
        "database.insert_record",
        "database.update_record",
        "database.create_table",
        # db_gateway — write operations
        "db_gateway.create",
        "db_gateway.create_many",
        "db_gateway.update",
    ]
)

_LOCAL_WORKER_DENIED: frozenset[str] = frozenset(
    [
        "filesystem.delete_file",
        "os_exec.kill_process",
        "database.delete_record",
        "db_gateway.delete",
        "api_http.send_email",
    ]
)

_POWER_USER_ALLOWED: frozenset[str] = _LOCAL_WORKER_ALLOWED | frozenset(
    [
        "filesystem.delete_file",
        "os_exec.kill_process",
        "os_exec.close_application",
        "browser.*",
        "gui.*",
        "database.*",
        "db_gateway.*",
        "api_http.send_email",
        "iot.*",
        "vision.*",
        "computer_control.*",
        "window_tracker.*",
    ]
)

_UNRESTRICTED_ALLOWED: frozenset[str] = frozenset(["*.*"])


BUILTIN_PROFILES: dict[PermissionProfile, PermissionProfileConfig] = {
    PermissionProfile.READONLY: PermissionProfileConfig(
        profile=PermissionProfile.READONLY,
        allowed_patterns=_READONLY_ALLOWED,
        max_plan_actions=20,
        allow_env_templates=False,
        allow_approval_bypass=False,
    ),
    PermissionProfile.LOCAL_WORKER: PermissionProfileConfig(
        profile=PermissionProfile.LOCAL_WORKER,
        allowed_patterns=_LOCAL_WORKER_ALLOWED,
        denied_patterns=_LOCAL_WORKER_DENIED,
        max_plan_actions=50,
        allow_env_templates=True,
        allow_approval_bypass=False,
    ),
    PermissionProfile.POWER_USER: PermissionProfileConfig(
        profile=PermissionProfile.POWER_USER,
        allowed_patterns=_POWER_USER_ALLOWED,
        max_plan_actions=200,
        allow_env_templates=True,
        allow_approval_bypass=False,
    ),
    PermissionProfile.UNRESTRICTED: PermissionProfileConfig(
        profile=PermissionProfile.UNRESTRICTED,
        allowed_patterns=_UNRESTRICTED_ALLOWED,
        max_plan_actions=500,
        allow_env_templates=True,
        allow_approval_bypass=True,
    ),
}


def get_profile_config(profile: PermissionProfile) -> PermissionProfileConfig:
    return BUILTIN_PROFILES[profile]
