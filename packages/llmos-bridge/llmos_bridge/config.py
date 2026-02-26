"""LLMOS Bridge — Daemon configuration.

Configuration is loaded from (in order of increasing priority):
    1. Built-in defaults (this file)
    2. System config: /etc/llmos-bridge/config.yaml
    3. User config:   ~/.llmos/config.yaml
    4. Environment variables prefixed with LLMOS_

All settings are immutable after load.  Call ``Settings.load()`` once at
daemon startup and inject the instance through FastAPI dependencies.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# ---------------------------------------------------------------------------
# Sub-configuration blocks
# ---------------------------------------------------------------------------


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = Field(default=40000, ge=1024, le=65535)
    workers: int = Field(default=1, ge=1, le=16)
    reload: bool = False
    log_level: Literal["debug", "info", "warning", "error"] = "info"
    sync_plan_timeout: Annotated[int, Field(ge=10, le=3600)] = Field(
        default=300,
        description=(
            "Maximum seconds to wait for a plan when async_execution=false. "
            "Increase for plans with long-running actions."
        ),
    )


class SecurityConfig(BaseModel):
    permission_profile: Literal["readonly", "local_worker", "power_user", "unrestricted"] = (
        "local_worker"
    )
    api_token: str | None = Field(
        default=None,
        description="Bearer token required on all API requests. None = no auth (local only).",
    )
    require_approval_for: list[str] = Field(
        default_factory=lambda: [
            "filesystem.delete_file",
            "filesystem.delete_directory",
            "os_exec.run_command",
            "os_exec.kill_process",
            "database.execute_query",
        ],
        description="List of 'module.action' pairs that always require user approval.",
    )
    max_plan_actions: Annotated[int, Field(ge=1, le=500)] = 50
    max_concurrent_plans: Annotated[int, Field(ge=1, le=20)] = 5
    sandbox_paths: list[str] = Field(
        default_factory=list,
        description=(
            "When non-empty, filesystem actions are restricted to these directories. "
            "Absolute paths only."
        ),
    )
    approval_timeout_seconds: Annotated[int, Field(ge=10, le=3600)] = Field(
        default=300,
        description="Default timeout (seconds) for pending approval requests.",
    )
    approval_timeout_behavior: Literal["reject", "skip"] = Field(
        default="reject",
        description="What to do when an approval request times out: reject (fail) or skip.",
    )


class ModuleConfig(BaseModel):
    enabled: list[str] = Field(
        default_factory=lambda: ["filesystem", "os_exec", "api_http", "excel", "word", "powerpoint", "triggers"],
        description="List of module IDs to load at startup.",
    )
    disabled: list[str] = Field(
        default_factory=list,
        description="Explicitly disabled modules (overrides 'enabled').",
    )


class MemoryConfig(BaseModel):
    state_db_path: Path = Path("~/.llmos/state.db")
    vector_db_path: Path = Path("~/.llmos/vector")
    vector_enabled: bool = False
    max_history_entries: int = Field(default=1000, ge=100, le=100_000)


class PerceptionConfig(BaseModel):
    enabled: bool = False
    ocr_enabled: bool = False
    screenshot_format: Literal["png", "jpeg"] = "png"
    screenshot_quality: Annotated[int, Field(ge=1, le=100)] = 85


class NodeConfig(BaseModel):
    """Distributed mode configuration.

    In ``standalone`` mode (default) LLMOS Bridge behaves exactly as before —
    no network discovery, no remote nodes, zero extra dependencies.

    Phase 4 will add ``node`` and ``orchestrator`` modes.  The fields below
    are intentionally minimal: only the concepts that need to exist in the
    interface from the start are declared here.  Implementation is deferred.
    """

    mode: Literal["standalone", "node", "orchestrator"] = Field(
        default="standalone",
        description=(
            "standalone — single PC, no networking (default). "
            "node       — Phase 4: this instance is a managed remote node. "
            "orchestrator — Phase 4: this instance coordinates a network of nodes."
        ),
    )
    node_id: str = Field(
        default="local",
        description=(
            "Unique identifier for this node within a distributed network. "
            "In standalone mode this is always 'local' and has no effect."
        ),
    )
    location: str = Field(
        default="",
        description="Human-readable location label (e.g. 'Usine Lyon — Four 1'). Optional.",
    )


class TriggerConfig(BaseModel):
    """Configuration for the TriggerDaemon reactive automation subsystem."""

    enabled: bool = Field(
        default=False,
        description="Enable TriggerDaemon (reactive automation). Disabled by default.",
    )
    db_path: Path = Field(
        default=Path("~/.llmos/triggers.db"),
        description="SQLite database path for trigger persistence.",
    )
    max_concurrent_plans: Annotated[int, Field(ge=1, le=50)] = 5
    max_chain_depth: Annotated[int, Field(ge=1, le=20)] = 5
    enabled_types: list[str] = Field(
        default_factory=lambda: ["temporal", "filesystem", "process", "resource", "composite"],
        description="Allowed trigger types. Remove 'iot' on machines without hardware.",
    )


class RecordingConfig(BaseModel):
    """Configuration for the Shadow Recorder (LLMOS-native workflow recording)."""

    enabled: bool = Field(
        default=False,
        description="Enable WorkflowRecorder (Shadow Recorder). Disabled by default.",
    )
    db_path: Path = Field(
        default=Path("~/.llmos/recordings.db"),
        description="SQLite database path for recording persistence.",
    )


class LoggingConfig(BaseModel):
    level: Literal["debug", "info", "warning", "error", "critical"] = "info"
    format: Literal["json", "console"] = "console"
    file: Path | None = None
    audit_file: Path | None = Path("~/.llmos/audit.log")


# ---------------------------------------------------------------------------
# Root settings
# ---------------------------------------------------------------------------


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LLMOS_",
        env_nested_delimiter="__",
        case_sensitive=False,
        extra="ignore",
    )

    server: ServerConfig = Field(default_factory=ServerConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    modules: ModuleConfig = Field(default_factory=ModuleConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    perception: PerceptionConfig = Field(default_factory=PerceptionConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    triggers: TriggerConfig = Field(default_factory=TriggerConfig)
    recording: RecordingConfig = Field(default_factory=RecordingConfig)
    node: NodeConfig = Field(default_factory=NodeConfig)

    @field_validator("memory", mode="before")
    @classmethod
    def expand_memory_paths(cls, v: object) -> object:
        if isinstance(v, dict):
            for key in ("state_db_path", "vector_db_path"):
                if key in v and isinstance(v[key], str):
                    v[key] = Path(v[key]).expanduser()
        return v

    @classmethod
    def load(cls, config_file: Path | None = None) -> "Settings":
        """Load settings from file + environment variables."""
        data: dict[str, object] = {}

        candidates = [
            Path("/etc/llmos-bridge/config.yaml"),
            Path.home() / ".llmos" / "config.yaml",
        ]
        if config_file:
            candidates.append(config_file)

        for path in candidates:
            if path.exists():
                import yaml  # lazy import — optional dependency

                with path.open() as f:
                    loaded = yaml.safe_load(f) or {}
                    data.update(loaded)

        return cls(**data)

    def active_modules(self) -> list[str]:
        """Return the effective list of enabled module IDs."""
        return [m for m in self.modules.enabled if m not in self.modules.disabled]


# Module-level singleton — replaced by ``Settings.load()`` at daemon startup.
_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings.load()
    return _settings


def override_settings(settings: Settings) -> None:
    """Replace the module-level singleton. Used in tests."""
    global _settings
    _settings = settings
