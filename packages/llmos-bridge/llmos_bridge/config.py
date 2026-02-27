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
    rate_limit_per_minute: Annotated[int, Field(ge=1, le=1000)] = Field(
        default=60,
        description="Max POST /plans requests per minute per client IP.",
    )
    max_result_size: Annotated[int, Field(ge=1024, le=10_485_760)] = Field(
        default=524_288,
        description=(
            "Max action result size in bytes before truncation (default 512KB). "
            "Prevents massive outputs from overflowing LLM context."
        ),
    )
    plan_retention_hours: Annotated[int, Field(ge=1, le=8760)] = Field(
        default=168,
        description="Hours to keep completed/failed plans before auto-purge (default 7 days).",
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
        default_factory=lambda: ["filesystem", "os_exec", "api_http", "excel", "word", "powerpoint", "database", "db_gateway", "triggers", "vision"],
        description="List of module IDs to load at startup.",
    )
    disabled: list[str] = Field(
        default_factory=list,
        description="Explicitly disabled modules (overrides 'enabled').",
    )
    fallbacks: dict[str, list[str]] = Field(
        default_factory=lambda: {
            "excel": ["filesystem"],
            "word": ["filesystem"],
            "powerpoint": ["filesystem"],
        },
        description=(
            "Fallback chains per module. When a module fails (unavailable or "
            "action error), the executor tries the next module in the chain. "
            "Example: excel → filesystem means 'if Excel is down, try filesystem'."
        ),
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


class ResourceConfig(BaseModel):
    """Per-module concurrency limits for parallel execution."""

    default_concurrency: Annotated[int, Field(ge=1, le=100)] = 10
    module_limits: dict[str, int] = Field(
        default_factory=lambda: {
            "excel": 3,
            "word": 3,
            "powerpoint": 3,
            "api_http": 10,
            "filesystem": 20,
            "os_exec": 5,
        },
        description="Maximum concurrent actions per module.",
    )


class DatabaseGatewayConfig(BaseModel):
    """Configuration for the Database Gateway (db_gateway) module."""

    max_connections: Annotated[int, Field(ge=1, le=50)] = 10
    default_row_limit: Annotated[int, Field(ge=1, le=100_000)] = 1000
    schema_cache_ttl: Annotated[int, Field(ge=0, le=3600)] = 300
    auto_introspect: bool = Field(
        default=True,
        description="Automatically introspect schema on connect.",
    )


class CustomThreatCategoryConfig(BaseModel):
    """Configuration for a user-defined threat category.

    Custom categories are injected into the IntentVerifier's system prompt
    alongside the built-in categories.  Each category provides a detection
    guidance section that tells the security LLM what to look for.
    """

    id: str = Field(description="Unique identifier for this category (e.g. 'data_retention').")
    name: str = Field(description="Human-readable name (e.g. 'Data Retention Violations').")
    description: str = Field(description="Detection guidance text injected into the system prompt.")
    threat_type: str = Field(
        default="custom",
        description="Threat type for result classification. Default 'custom'.",
    )


class IntentVerifierConfig(BaseModel):
    """Configuration for the LLM-based intent verification layer (Couche 1).

    When enabled, every incoming IML plan is analysed by a dedicated LLM
    before reaching the PermissionGuard.  The security LLM detects prompt
    injection, privilege escalation, data exfiltration, and intent
    misalignment.

    Enabled by default — uses NullLLMClient (zero overhead) until a real
    provider is configured.
    """

    enabled: bool = Field(
        default=True,
        description="Enable LLM-based intent verification on incoming plans.",
    )
    strict: bool = Field(
        default=False,
        description=(
            "When True, verification failure blocks plan execution. "
            "When False, failures are logged but execution continues."
        ),
    )
    provider: Literal["null", "openai", "anthropic", "ollama", "custom"] = Field(
        default="null",
        description="LLM provider for the security analysis model.",
    )
    model: str = Field(
        default="",
        description="Model ID (e.g. 'gpt-4o-mini', 'claude-sonnet-4-20250514'). Provider-specific.",
    )
    api_key: str | None = Field(
        default=None,
        description="API key for the LLM provider. Also settable via LLMOS_INTENT_VERIFIER__API_KEY.",
    )
    api_base_url: str | None = Field(
        default=None,
        description="Custom API base URL (for proxies, Azure, or self-hosted models).",
    )
    timeout_seconds: Annotated[float, Field(ge=5.0, le=120.0)] = Field(
        default=30.0,
        description="Maximum seconds to wait for the LLM response.",
    )
    cache_size: Annotated[int, Field(ge=0, le=10000)] = Field(
        default=256,
        description="Number of plan verification results to cache (LRU). 0 = no caching.",
    )
    cache_ttl_seconds: Annotated[float, Field(ge=0, le=3600)] = Field(
        default=300.0,
        description="TTL for cached verification results in seconds. 0 = no TTL.",
    )
    max_plan_actions_for_verification: Annotated[int, Field(ge=1, le=500)] = Field(
        default=50,
        description="Plans with more actions than this skip LLM verification (too large).",
    )
    skip_modules: list[str] = Field(
        default_factory=list,
        description="Module IDs to skip during verification (e.g. 'recording').",
    )
    max_retries: Annotated[int, Field(ge=0, le=5)] = Field(
        default=2,
        description="Maximum retry attempts on transient LLM provider errors.",
    )
    custom_threat_categories: list[CustomThreatCategoryConfig] = Field(
        default_factory=list,
        description="Additional threat categories injected into the security system prompt.",
    )
    disabled_threat_categories: list[str] = Field(
        default_factory=list,
        description="IDs of built-in threat categories to disable (e.g. ['resource_abuse']).",
    )
    custom_system_prompt_suffix: str = Field(
        default="",
        description="Extra text appended to the security analysis system prompt.",
    )
    custom_provider_class: str | None = Field(
        default=None,
        description=(
            "Fully-qualified class path for a custom LLMClient "
            "(e.g. 'myapp.security.MyClient'). Only used when provider='custom'."
        ),
    )
    custom_verifier_class: str | None = Field(
        default=None,
        description=(
            "Fully-qualified class path for a custom IntentVerifier subclass. "
            "When set, replaces the default IntentVerifier entirely."
        ),
    )


class SecurityAdvancedConfig(BaseModel):
    """Configuration for the OS-level permission system."""

    permissions_db_path: Path = Field(
        default=Path("~/.llmos/permissions.db"),
        description="SQLite database path for permission grant persistence.",
    )
    auto_grant_low_risk: bool = Field(
        default=True,
        description="Auto-grant LOW-risk permissions on first check (no user prompt).",
    )
    enable_decorators: bool = Field(
        default=True,
        description="Enable runtime enforcement of security decorators.",
    )
    enable_rate_limiting: bool = Field(
        default=True,
        description="Enable per-action rate limiting.",
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


class ScannerPatternConfig(BaseModel):
    """Configuration for a user-defined heuristic scanner pattern."""

    id: str = Field(description="Unique pattern ID.")
    category: str = Field(description="Threat category (e.g. 'prompt_injection').")
    pattern: str = Field(description="Regex pattern string.")
    severity: Annotated[float, Field(ge=0.0, le=1.0)] = Field(
        default=0.5,
        description="Risk score contribution (0.0-1.0).",
    )
    description: str = Field(default="", description="Human-readable description.")


class ScannerPipelineConfig(BaseModel):
    """Configuration for the pre-LLM security scanner pipeline.

    The scanner pipeline runs ultra-fast heuristic and (optionally)
    ML-based scanners BEFORE the LLM-based IntentVerifier.  This
    catches obvious attacks in <1ms without incurring API costs.
    """

    enabled: bool = Field(
        default=True,
        description="Enable the scanner pipeline. When disabled, no scanners run.",
    )
    fail_fast: bool = Field(
        default=True,
        description="Short-circuit on first REJECT verdict (fastest path).",
    )
    reject_threshold: Annotated[float, Field(ge=0.0, le=1.0)] = Field(
        default=0.7,
        description="Aggregate risk score above which the plan is rejected.",
    )
    warn_threshold: Annotated[float, Field(ge=0.0, le=1.0)] = Field(
        default=0.3,
        description="Risk score above which a warning is emitted.",
    )
    heuristic_enabled: bool = Field(
        default=True,
        description="Enable the built-in HeuristicScanner (Layer 1, zero deps).",
    )
    heuristic_disabled_patterns: list[str] = Field(
        default_factory=list,
        description="Pattern IDs to disable in the HeuristicScanner.",
    )
    heuristic_extra_patterns: list[ScannerPatternConfig] = Field(
        default_factory=list,
        description="Additional regex patterns for the HeuristicScanner.",
    )
    llm_guard_enabled: bool = Field(
        default=False,
        description="Enable LLM Guard adapter (requires: pip install llm-guard).",
    )
    llm_guard_scanners: list[str] = Field(
        default_factory=lambda: ["PromptInjection"],
        description="LLM Guard scanner names to enable.",
    )
    prompt_guard_enabled: bool = Field(
        default=False,
        description="Enable Meta Prompt Guard adapter (requires: pip install transformers torch).",
    )
    prompt_guard_model: str = Field(
        default="meta-llama/Prompt-Guard-86M",
        description="HuggingFace model ID for Prompt Guard.",
    )


class VisionConfig(BaseModel):
    """Configuration for the visual perception (vision) module."""

    backend: str = Field(
        default="omniparser",
        description=(
            "Vision backend to use. 'omniparser' (default) uses Microsoft OmniParser v2. "
            "Custom backends: subclass BaseVisionModule, install as a package, and set "
            "the fully-qualified class path here (e.g. 'mypackage.MyVisionModule')."
        ),
    )
    omniparser_path: str = Field(
        default="~/.llmos/omniparser",
        description=(
            "Path to the cloned OmniParser repository. "
            "Clone with: git clone https://github.com/microsoft/OmniParser.git ~/.llmos/omniparser"
        ),
    )
    model_dir: str = Field(
        default="~/.llmos/models/omniparser",
        description="Directory containing model weights (icon_detect/ + icon_caption_florence/).",
    )
    device: str = Field(
        default="auto",
        description="Torch device: 'auto', 'cpu', 'cuda', 'mps'.",
    )
    box_threshold: Annotated[float, Field(ge=0.0, le=1.0)] = 0.05
    iou_threshold: Annotated[float, Field(ge=0.0, le=1.0)] = 0.1
    caption_model_name: str = Field(
        default="florence2",
        description="Caption model: 'florence2' (default) or 'blip2'.",
    )
    use_paddleocr: bool = Field(
        default=True,
        description="Use PaddleOCR (True) or EasyOCR (False) for text extraction.",
    )
    auto_download_weights: bool = Field(
        default=True,
        description="Auto-download model weights from HuggingFace on first use.",
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
    resources: ResourceConfig = Field(default_factory=ResourceConfig)
    db_gateway: DatabaseGatewayConfig = Field(default_factory=DatabaseGatewayConfig)
    security_advanced: SecurityAdvancedConfig = Field(default_factory=SecurityAdvancedConfig)
    intent_verifier: IntentVerifierConfig = Field(default_factory=IntentVerifierConfig)
    scanner_pipeline: ScannerPipelineConfig = Field(default_factory=ScannerPipelineConfig)
    node: NodeConfig = Field(default_factory=NodeConfig)
    vision: VisionConfig = Field(default_factory=VisionConfig)

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
